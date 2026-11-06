import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import CHALLENGE_COOKIE, CHALLENGE_MINUTES
from app.db import get_db
from app.deps import client_ip, load_principal
from app.errors import AppError
from app.labels import classify_remote_source, user_agent_family
from app.models import AttendancePolicy
from app.presentation import present_account
from app.rate_limit import limiter
from app.schemas import AccountPasswordBody, CodeBody, LoginBody, PasswordBody
from app.security import clear_challenge_cookie, clear_session_cookie, set_challenge_cookie, set_session_cookie
from app.services.passkeys import authenticate, authentication_options, register_credential, registration_options
from app.services.auth import (
    PURPOSE_STATUS,
    begin_login,
    change_password_in_session,
    change_password_with_challenge,
    confirm_enrollment,
    enrollment_payload,
    load_challenge,
    qr_svg_base64,
    verify_second_factor,
)
from app.audit import write_audit
from app.timeutil import time_view, utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"])


class PasskeyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: uuid.UUID
    credential: dict = Field()


def _family(request: Request) -> str:
    source = classify_remote_source(request.headers.get("user-agent", ""), request.headers.get("sec-ch-ua-mobile"))
    return user_agent_family(source)


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    ip = client_ip(request)
    email = body.email.strip().lower()
    settings = get_settings()
    limiter.hit(f"login:{ip}:{email}", settings.login_rate_limit, settings.login_rate_window_seconds)
    limiter.hit(f"login-ip:{ip}", settings.login_rate_limit * 4, settings.login_rate_window_seconds)
    purpose, token = begin_login(db, email, body.password, ip)
    response = JSONResponse({"status": PURPOSE_STATUS[purpose], "expires_in": CHALLENGE_MINUTES * 60})
    set_challenge_cookie(response, token)
    return response


@router.get("/challenge")
def challenge(request: Request, db: Session = Depends(get_db)) -> dict:
    _user, row = load_challenge(db, request.cookies.get(CHALLENGE_COOKIE))
    remaining = max(0, int((row.expires_at - utcnow()).total_seconds()))
    return {"status": PURPOSE_STATUS[row.purpose], "expires_in": remaining}


@router.post("/password")
def password(body: PasswordBody, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    ip = client_ip(request)
    user, row = load_challenge(db, request.cookies.get(CHALLENGE_COOKIE), "PASSWORD")
    limiter.hit(f"password:{user.id}", get_settings().login_rate_limit, get_settings().login_rate_window_seconds)
    purpose, token = change_password_with_challenge(db, user, row, body.new_password, ip)
    response = JSONResponse({"status": PURPOSE_STATUS[purpose], "expires_in": CHALLENGE_MINUTES * 60})
    set_challenge_cookie(response, token)
    return response


@router.post("/mfa/enroll/start")
def enroll_start(request: Request, db: Session = Depends(get_db)) -> dict:
    user, row = load_challenge(db, request.cookies.get(CHALLENGE_COOKIE), "ENROLL")
    payload = enrollment_payload(db, user, row)
    payload["qr_svg_base64"] = qr_svg_base64(payload["otpauth_uri"])
    return payload


@router.post("/mfa/enroll/confirm")
def enroll_confirm(body: CodeBody, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    ip = client_ip(request)
    user, row = load_challenge(db, request.cookies.get(CHALLENGE_COOKIE), "ENROLL")
    limiter.hit(f"mfa:{user.id}", get_settings().login_rate_limit, get_settings().login_rate_window_seconds)
    token, csrf, codes = confirm_enrollment(db, user, row, body.code, ip, _family(request))
    response = JSONResponse(
        {
            "csrf_token": csrf,
            "recovery_codes": codes,
            "user": present_account(db, user),
        }
    )
    clear_challenge_cookie(response)
    set_session_cookie(response, token)
    return response


@router.post("/mfa/verify")
def mfa_verify(body: CodeBody, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    ip = client_ip(request)
    user, row = load_challenge(db, request.cookies.get(CHALLENGE_COOKIE), "MFA")
    limiter.hit(f"mfa:{user.id}", get_settings().login_rate_limit, get_settings().login_rate_window_seconds)
    token, csrf, used_backup, remaining = verify_second_factor(db, user, row, body.code, ip, _family(request))
    response = JSONResponse(
        {
            "csrf_token": csrf,
            "used_backup_code": used_backup,
            "backup_codes_remaining": remaining,
            "user": present_account(db, user),
        }
    )
    clear_challenge_cookie(response)
    set_session_cookie(response, token)
    return response


@router.post("/webauthn/register/options")
def webauthn_register_options(request: Request, db: Session = Depends(get_db)) -> dict:
    principal = load_principal(request, db, require_ready=True)
    return registration_options(db, principal.user, request.headers.get("origin"))


@router.post("/webauthn/register/verify")
def webauthn_register_verify(body: PasskeyBody, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = load_principal(request, db, require_ready=True)
    return register_credential(db, principal.user, body.challenge_id, body.credential, client_ip(request), request.headers.get("origin"))


@router.post("/webauthn/login/options")
def webauthn_login_options(request: Request, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    limiter.hit(f"webauthn:{client_ip(request)}", settings.login_rate_limit, settings.login_rate_window_seconds)
    return authentication_options(db, request.headers.get("origin"))


@router.post("/webauthn/login/verify")
def webauthn_login_verify(body: PasskeyBody, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    settings = get_settings()
    limiter.hit(f"webauthn:{client_ip(request)}", settings.login_rate_limit, settings.login_rate_window_seconds)
    token, csrf, user = authenticate(db, body.challenge_id, body.credential, client_ip(request), _family(request), request.headers.get("origin"))
    response = JSONResponse(
        {
            "csrf_token": csrf,
            "authentication_method": "WEBAUTHN",
            "user": present_account(db, user),
        }
    )
    set_session_cookie(response, token)
    return response


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)) -> Response:
    principal = load_principal(request, db, require_ready=False)
    principal.session.revoked_at = utcnow()
    write_audit(
        db,
        actor_user_id=principal.user.id,
        action="LOGOUT",
        entity_type="user",
        entity_id=str(principal.user.id),
        ip_address=client_ip(request),
        details={},
    )
    db.commit()
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)) -> dict:
    principal = load_principal(request, db, require_ready=True)
    policy = db.get(AttendancePolicy, 1)
    if policy is None:
        raise AppError("INTERNAL", "Attendance policy is not configured.", 500)
    return {
        "csrf_token": principal.session.csrf_token,
        "environment": get_settings().app_env,
        "user": present_account(db, principal.user),
        "policy": {
            "remote_attendance_approved": policy.remote_attendance_approved,
            "inference_enabled": policy.inference_enabled,
        },
        "server_time": time_view(utcnow()),
    }


@router.post("/account/password")
def account_password(body: AccountPasswordBody, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = load_principal(request, db, require_ready=True)
    change_password_in_session(
        db,
        principal.user,
        body.current_password,
        body.new_password,
        principal.session.id,
        client_ip(request),
    )
    return {"status": "password_changed"}
