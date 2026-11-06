"""Passkeys via WebAuthn.

The credential stored here is a public key. Fingerprint images, fingerprint
templates, and facial templates are not requested or saved. The browser may
offer this computer or a phone. User verification is required, and that check
does not tell this server whether the device used a fingerprint or a PIN.
"""

import ipaddress
import json
import uuid
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import options_to_json_dict, parse_authentication_credential_json, parse_registration_credential_json
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse, WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialHint,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.audit import write_audit
from app.config import browser_origins, get_settings
from app.errors import AppError
from app.models import AuthSession, User, UserMfa, WebAuthnChallenge, WebAuthnCredential
from app.services.auth import open_session
from app.timeutil import utcnow

CHALLENGE_MINUTES = 5
MAX_CREDENTIALS = 5


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


_IP_PASSKEY = (
    "Passkeys cannot be saved on an IP address. Open http://localhost:3000, sign in again, then click Add a passkey."
)


def _host_allowed(host: str, scheme: str) -> bool:
    settings = get_settings()
    allowed: set[str] = set()
    for item in browser_origins(settings):
        name = (urlparse(item).hostname or "").lower()
        if name:
            allowed.add(name)
    configured_host = (urlparse(settings.frontend_origin).hostname or "").lower()
    if configured_host in {"127.0.0.1", "localhost"}:
        allowed.add("localhost")
    if host.lower() in allowed:
        return True
    # A phone can enrol a passkey only on an HTTPS name. Development tunnels
    # (ngrok, Cloudflare) change hostname; production must list the name.
    return settings.app_env == "development" and scheme == "https" and not _is_ip(host)


def _origin_and_rp(request_origin: str | None = None) -> tuple[str, str]:
    configured = get_settings().frontend_origin.rstrip("/")
    origin = configured
    if request_origin:
        candidate = request_origin.strip().rstrip("/")
        parsed = urlparse(candidate)
        host = parsed.hostname or ""
        scheme = (parsed.scheme or "").lower()
        if _is_ip(host):
            raise AppError("PASSKEY_ORIGIN", _IP_PASSKEY, 400)
        if not host or scheme not in {"http", "https"} or not _host_allowed(host, scheme):
            raise AppError(
                "PASSKEY_ORIGIN",
                "This address is not allowed for passkeys. Use localhost, the configured site, or an HTTPS name while the server is in development.",
                400,
            )
        port = parsed.port
        origin = f"{scheme}://{host}" + (f":{port}" if port else "")
    host = urlparse(origin).hostname or ""
    if not host:
        raise AppError("INTERNAL", "The application origin is not configured.", 500)
    return origin, host


def _load_challenge(db: Session, challenge_id: uuid.UUID, purpose: str, user_id: uuid.UUID | None) -> WebAuthnChallenge:
    row = db.get(WebAuthnChallenge, challenge_id)
    if row is None or row.purpose != purpose or row.consumed_at is not None or row.expires_at < utcnow():
        raise AppError("AUTH_FAILED", "This device could not be verified. Start again.", 401)
    if user_id is not None and row.user_id != user_id:
        raise AppError("AUTH_FAILED", "This device could not be verified. Start again.", 401)
    row.consumed_at = utcnow()
    return row


def registration_options(db: Session, user: User, request_origin: str | None = None) -> dict:
    count = db.scalar(
        select(func.count()).select_from(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id)
    )
    if int(count or 0) >= MAX_CREDENTIALS:
        raise AppError("CONFLICT", "This account already has the maximum number of passkeys.", 409)
    existing = db.scalars(select(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id)).all()
    _origin, rp_id = _origin_and_rp(request_origin)
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=get_settings().mfa_issuer,
        user_id=user.id.bytes,
        user_name=user.email,
        user_display_name=user.display_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        hints=[
            PublicKeyCredentialHint.CLIENT_DEVICE,
            PublicKeyCredentialHint.SECURITY_KEY,
            PublicKeyCredentialHint.HYBRID,
        ],
        exclude_credentials=[PublicKeyCredentialDescriptor(id=row.credential_id) for row in existing],
    )
    now = utcnow()
    challenge = WebAuthnChallenge(
        user_id=user.id,
        purpose="REGISTER",
        challenge=options.challenge,
        created_at=now,
        expires_at=now + timedelta(minutes=CHALLENGE_MINUTES),
    )
    db.add(challenge)
    db.commit()
    return {"challenge_id": str(challenge.id), "public_key": options_to_json_dict(options)}


def register_credential(db: Session, user: User, challenge_id: uuid.UUID, credential: dict, ip: str | None, request_origin: str | None = None) -> dict:
    _reject_oversized(credential)
    row = _load_challenge(db, challenge_id, "REGISTER", user.id)
    origin, rp_id = _origin_and_rp(request_origin)
    try:
        verified = verify_registration_response(
            credential=parse_registration_credential_json(json.dumps(credential)),
            expected_challenge=bytes(row.challenge),
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=True,
        )
    except (InvalidRegistrationResponse, WebAuthnException) as exc:
        db.commit()
        raise AppError("AUTH_FAILED", "This device could not be verified. Start again.", 401) from exc
    if not verified.user_verified:
        db.commit()
        raise AppError("AUTH_FAILED", "This device could not be verified. Start again.", 401)
    now = utcnow()
    transports = _transports(credential)
    db.add(
        WebAuthnCredential(
            user_id=user.id,
            credential_id=bytes(verified.credential_id),
            public_key=bytes(verified.credential_public_key),
            sign_count=verified.sign_count,
            transports=transports,
            aaguid=str(verified.aaguid)[:36],
            device_type=str(getattr(verified.credential_device_type, "value", verified.credential_device_type))[:32],
            backed_up=bool(verified.credential_backed_up),
            created_at=now,
        )
    )
    write_audit(
        db,
        actor_user_id=user.id,
        action="PASSKEY_REGISTERED",
        entity_type="user",
        entity_id=str(user.id),
        ip_address=ip,
        details={"authentication_method": "WEBAUTHN", "user_verification": "required"},
    )
    db.commit()
    total = db.scalar(select(func.count()).select_from(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id))
    return {
        "registered": True,
        "passkey_count": int(total or 0),
        "message": (
            "Passkey saved. This application stores the public key only. "
            "No fingerprint or face template was collected. "
            "User verification was required, and this server cannot tell a fingerprint from a device PIN."
        ),
    }


def authentication_options(db: Session, request_origin: str | None = None) -> dict:
    _origin, rp_id = _origin_and_rp(request_origin)
    options = generate_authentication_options(
        rp_id=rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    now = utcnow()
    challenge = WebAuthnChallenge(
        user_id=None,
        purpose="AUTHENTICATE",
        challenge=options.challenge,
        created_at=now,
        expires_at=now + timedelta(minutes=CHALLENGE_MINUTES),
    )
    db.add(challenge)
    db.commit()
    return {"challenge_id": str(challenge.id), "public_key": options_to_json_dict(options)}


def authenticate(db: Session, challenge_id: uuid.UUID, credential: dict, ip: str | None, family: str | None, request_origin: str | None = None) -> tuple[str, str, User]:
    _reject_oversized(credential)
    row = _load_challenge(db, challenge_id, "AUTHENTICATE", None)
    try:
        parsed = _parse_authentication(credential)
    except AppError:
        db.commit()
        raise
    stored = db.scalar(select(WebAuthnCredential).where(WebAuthnCredential.credential_id == parsed.raw_id))
    if stored is None:
        db.commit()
        raise AppError("AUTH_FAILED", "This device could not be verified.", 401)
    origin, rp_id = _origin_and_rp(request_origin)
    try:
        verified = verify_authentication_response(
            credential=parsed,
            expected_challenge=bytes(row.challenge),
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=bytes(stored.public_key),
            credential_current_sign_count=stored.sign_count,
            require_user_verification=True,
        )
    except (InvalidAuthenticationResponse, WebAuthnException) as exc:
        db.commit()
        raise AppError("AUTH_FAILED", "This device could not be verified.", 401) from exc
    if not verified.user_verified:
        db.commit()
        raise AppError("AUTH_FAILED", "This device could not be verified.", 401)
    user = db.get(User, stored.user_id)
    mfa = db.get(UserMfa, stored.user_id) if user else None
    if user is None or not user.is_active or user.must_change_password or mfa is None or not mfa.enabled:
        db.commit()
        raise AppError("AUTH_FAILED", "This device could not be verified.", 401)
    stored.sign_count = verified.new_sign_count
    stored.last_used_at = utcnow()
    token, csrf = open_session(db, user, ip, family, "WEBAUTHN")
    write_audit(
        db,
        actor_user_id=user.id,
        action="LOGIN_SUCCESS",
        entity_type="user",
        entity_id=str(user.id),
        ip_address=ip,
        details={"authentication_method": "WEBAUTHN", "user_verification": "required"},
    )
    db.commit()
    return token, csrf, user


def _descriptors(rows: list[WebAuthnCredential], *, localhost: bool) -> list[PublicKeyCredentialDescriptor]:
    chosen = rows
    if localhost:
        internal = [row for row in rows if "internal" in (row.transports or [])]
        if internal:
            chosen = internal
    descriptors: list[PublicKeyCredentialDescriptor] = []
    for row in chosen:
        names = row.transports or []
        if localhost:
            names = [name for name in names if name == "internal"]
        transports = []
        for name in names:
            try:
                transports.append(AuthenticatorTransport(name))
            except ValueError:
                continue
        descriptors.append(PublicKeyCredentialDescriptor(id=row.credential_id, transports=transports or None))
    return descriptors


def attendance_options(db: Session, user: User, request_origin: str | None = None) -> dict:
    existing = db.scalars(select(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id)).all()
    if not existing:
        raise AppError(
            "PASSKEY_REQUIRED",
            "Add a passkey on the account page before marking attendance. No fingerprint or face template is stored.",
            409,
        )
    origin, rp_id = _origin_and_rp(request_origin)
    options = generate_authentication_options(
        rp_id=rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
        allow_credentials=_descriptors(list(existing), localhost=urlparse(origin).hostname == "localhost"),
    )
    now = utcnow()
    challenge = WebAuthnChallenge(
        user_id=user.id,
        purpose="ATTENDANCE",
        challenge=options.challenge,
        created_at=now,
        expires_at=now + timedelta(minutes=CHALLENGE_MINUTES),
    )
    db.add(challenge)
    db.commit()
    return {
        "challenge_id": str(challenge.id),
        "public_key": options_to_json_dict(options),
        "message": "User verification is required. This check does not prove where you are, and no fingerprint or face template is stored.",
    }


def verify_attendance(
    db: Session,
    *,
    user: User,
    session: AuthSession,
    challenge_id: uuid.UUID,
    credential: dict,
    ip: str | None,
    request_origin: str | None = None,
) -> dict:
    _reject_oversized(credential)
    row = _load_challenge(db, challenge_id, "ATTENDANCE", user.id)
    try:
        parsed = _parse_authentication(credential)
    except AppError:
        db.commit()
        raise
    stored = db.scalar(select(WebAuthnCredential).where(WebAuthnCredential.credential_id == parsed.raw_id))
    if stored is None or stored.user_id != user.id:
        db.commit()
        raise AppError("AUTH_FAILED", "This device could not be verified.", 401)
    origin, rp_id = _origin_and_rp(request_origin)
    try:
        verified = verify_authentication_response(
            credential=parsed,
            expected_challenge=bytes(row.challenge),
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=bytes(stored.public_key),
            credential_current_sign_count=stored.sign_count,
            require_user_verification=True,
        )
    except (InvalidAuthenticationResponse, WebAuthnException) as exc:
        db.commit()
        raise AppError("AUTH_FAILED", "This device could not be verified.", 401) from exc
    if not verified.user_verified:
        db.commit()
        raise AppError("AUTH_FAILED", "This device could not be verified.", 401)
    stored.sign_count = verified.new_sign_count
    stored.last_used_at = utcnow()
    session.auth_method = "WEBAUTHN"
    write_audit(
        db,
        actor_user_id=user.id,
        action="ATTENDANCE_PASSKEY_VERIFIED",
        entity_type="auth_session",
        entity_id=str(session.id),
        ip_address=ip,
        details={"authentication_method": "WEBAUTHN", "user_verification": "required", "proves_location": False},
    )
    db.commit()
    return {
        "verified": True,
        "authentication_method": "WEBAUTHN",
        "proves_location": False,
        "message": "Passkey user verification succeeded. This does not prove where you are. No fingerprint or face template was stored.",
    }


def revoke_passkeys(db: Session, *, actor_id: uuid.UUID, user_id: uuid.UUID, reason: str, ip: str | None) -> dict:
    if len(reason.strip()) < 10:
        raise AppError("VALIDATION_ERROR", "Give a reason of at least 10 characters.", 422)
    user = db.get(User, user_id)
    if user is None:
        raise AppError("NOT_FOUND", "That account was not found.", 404)
    rows = db.scalars(select(WebAuthnCredential).where(WebAuthnCredential.user_id == user_id)).all()
    for row in rows:
        db.delete(row)
    write_audit(
        db,
        actor_user_id=actor_id,
        action="PASSKEY_REVOKED",
        entity_type="user",
        entity_id=str(user_id),
        ip_address=ip,
        details={"revoked": len(rows), "reason": reason.strip()[:500]},
    )
    db.commit()
    return {
        "revoked": len(rows),
        "message": "Passkeys for that account were revoked. No fingerprint or face template was stored.",
    }


def _parse_authentication(credential: dict):
    try:
        return parse_authentication_credential_json(json.dumps(credential))
    except (ValueError, WebAuthnException) as exc:
        raise AppError("AUTH_FAILED", "This device could not be verified.", 401) from exc


def _reject_oversized(credential: dict) -> None:
    if len(json.dumps(credential)) > 20000:
        raise AppError("VALIDATION_ERROR", "The passkey response is too large.", 422)


def _transports(credential: dict) -> list[str]:
    response = credential.get("response") if isinstance(credential, dict) else None
    raw = response.get("transports") if isinstance(response, dict) else None
    if not isinstance(raw, list):
        return []
    return [str(item)[:32] for item in raw[:8] if isinstance(item, str)]
