import base64
import hmac
import io

import pyotp
import qrcode
import qrcode.image.svg
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.errors import AppError
from app.models import AuthSession, LoginChallenge, User, UserMfa
from app.security import (
    challenge_expiry,
    decrypt_secret,
    dummy_password_hash,
    encrypt_secret,
    generate_recovery_codes,
    hash_password,
    hash_recovery_code,
    hash_token,
    new_csrf,
    new_token,
    provisioning_uri,
    session_expiry,
    totp_for,
    validate_password,
    verify_password,
)
from app.timeutil import utcnow

PURPOSE_STATUS = {
    "PASSWORD": "password_change_required",
    "ENROLL": "enrollment_required",
    "MFA": "mfa_required",
}


def consume_open_challenges(db: Session, user_id) -> None:
    db.execute(
        update(LoginChallenge)
        .where(LoginChallenge.user_id == user_id, LoginChallenge.consumed_at.is_(None))
        .values(consumed_at=utcnow())
    )


def create_challenge(db: Session, user: User, purpose: str) -> tuple[LoginChallenge, str]:
    consume_open_challenges(db, user.id)
    token = new_token()
    row = LoginChallenge(
        user_id=user.id,
        token_hash=hash_token(token),
        purpose=purpose,
        created_at=utcnow(),
        expires_at=challenge_expiry(),
    )
    db.add(row)
    db.flush()
    return row, token


def load_challenge(db: Session, raw_token: str | None, expected: str | None = None) -> tuple[User, LoginChallenge]:
    if not raw_token:
        raise AppError("CHALLENGE_EXPIRED", "Sign-in step expired. Start again.", 401)
    row = db.scalar(select(LoginChallenge).where(LoginChallenge.token_hash == hash_token(raw_token)))
    if row is None or row.consumed_at is not None or row.expires_at < utcnow():
        raise AppError("CHALLENGE_EXPIRED", "Sign-in step expired. Start again.", 401)
    if expected and row.purpose != expected:
        raise AppError("CHALLENGE_EXPIRED", "This sign-in step is no longer valid. Start again.", 401)
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AppError("ACCOUNT_INACTIVE", "This account is deactivated. Contact an administrator.", 403)
    return user, row


def next_purpose(db: Session, user: User) -> str:
    if user.must_change_password:
        return "PASSWORD"
    mfa = db.get(UserMfa, user.id)
    if mfa is None or not mfa.enabled:
        return "ENROLL"
    return "MFA"


def begin_login(db: Session, email: str, password: str, ip: str | None) -> tuple[str, str]:
    normalized = email.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == normalized))
    if user is None:
        verify_password(dummy_password_hash(), password)
        write_audit(
            db,
            actor_user_id=None,
            action="LOGIN_FAILURE",
            entity_type="user",
            entity_id=None,
            ip_address=ip,
            details={"result": "rejected"},
        )
        db.commit()
        raise AppError("AUTH_FAILED", "Email or password is incorrect.", 401)
    if not verify_password(user.password_hash, password):
        write_audit(
            db,
            actor_user_id=user.id,
            action="LOGIN_FAILURE",
            entity_type="user",
            entity_id=str(user.id),
            ip_address=ip,
            details={"result": "rejected"},
        )
        db.commit()
        raise AppError("AUTH_FAILED", "Email or password is incorrect.", 401)
    if not user.is_active:
        write_audit(
            db,
            actor_user_id=user.id,
            action="LOGIN_FAILURE",
            entity_type="user",
            entity_id=str(user.id),
            ip_address=ip,
            details={"result": "inactive"},
        )
        db.commit()
        raise AppError("ACCOUNT_INACTIVE", "This account is deactivated. Contact an administrator.", 403)
    purpose = next_purpose(db, user)
    _row, token = create_challenge(db, user, purpose)
    write_audit(
        db,
        actor_user_id=user.id,
        action="LOGIN_CHALLENGE",
        entity_type="user",
        entity_id=str(user.id),
        ip_address=ip,
        details={"purpose": purpose},
    )
    db.commit()
    return purpose, token


def change_password_with_challenge(db: Session, user: User, challenge: LoginChallenge, new_password: str, ip: str | None) -> tuple[str, str]:
    validate_password(new_password, user.email)
    if verify_password(user.password_hash, new_password):
        raise AppError("VALIDATION_ERROR", "Choose a password that is different from the current one.", 422)
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.updated_at = utcnow()
    challenge.consumed_at = utcnow()
    purpose = next_purpose(db, user)
    _row, token = create_challenge(db, user, purpose)
    write_audit(
        db,
        actor_user_id=user.id,
        action="PASSWORD_CHANGED",
        entity_type="user",
        entity_id=str(user.id),
        ip_address=ip,
        details={},
    )
    db.commit()
    return purpose, token


def enrollment_payload(db: Session, user: User, challenge: LoginChallenge) -> dict:
    if challenge.pending_totp_secret_encrypted:
        secret = decrypt_secret(challenge.pending_totp_secret_encrypted)
    else:
        secret = pyotp.random_base32()
        challenge.pending_totp_secret_encrypted = encrypt_secret(secret)
        db.commit()
    uri = provisioning_uri(secret, user.email)
    return {"secret": secret, "otpauth_uri": uri}


def confirm_enrollment(db: Session, user: User, challenge: LoginChallenge, code: str, ip: str | None, family: str | None) -> tuple[str, str, list[str]]:
    if not challenge.pending_totp_secret_encrypted:
        raise AppError("CHALLENGE_EXPIRED", "Start MFA enrollment again.", 401)
    secret = decrypt_secret(challenge.pending_totp_secret_encrypted)
    if not totp_for(secret).verify(code.strip(), valid_window=1):
        raise AppError("AUTH_FAILED", "The authentication code is incorrect.", 401)
    codes = generate_recovery_codes()
    now = utcnow()
    existing = db.get(UserMfa, user.id)
    if existing is None:
        existing = UserMfa(
            user_id=user.id,
            totp_secret_encrypted=encrypt_secret(secret),
            enabled=True,
            recovery_code_hashes=[hash_recovery_code(item) for item in codes],
            last_totp_step=int(now.timestamp()) // 30,
            enrolled_at=now,
            created_at=now,
        )
        db.add(existing)
    else:
        existing.totp_secret_encrypted = encrypt_secret(secret)
        existing.enabled = True
        existing.recovery_code_hashes = [hash_recovery_code(item) for item in codes]
        existing.last_totp_step = int(now.timestamp()) // 30
        existing.enrolled_at = now
    challenge.consumed_at = now
    session_token, csrf = _new_session(db, user, ip, family)
    write_audit(
        db,
        actor_user_id=user.id,
        action="MFA_ENROLLED",
        entity_type="user",
        entity_id=str(user.id),
        ip_address=ip,
        details={},
    )
    db.commit()
    return session_token, csrf, codes


def verify_second_factor(db: Session, user: User, challenge: LoginChallenge, code: str, ip: str | None, family: str | None) -> tuple[str, str, bool, int]:
    mfa = db.get(UserMfa, user.id)
    if mfa is None or not mfa.enabled:
        raise AppError("SETUP_INCOMPLETE", "MFA is not enrolled for this account.", 403)
    normalized = "".join(char for char in code.strip() if char.isalnum())
    used_recovery = False
    if len(normalized) == 6 and normalized.isdigit():
        if not totp_for(decrypt_secret(mfa.totp_secret_encrypted)).verify(normalized, valid_window=1):
            raise AppError("AUTH_FAILED", "The authentication code is incorrect.", 401)
        mfa.last_totp_step = int(utcnow().timestamp()) // 30
    else:
        candidate = hash_recovery_code(code)
        hashes = list(mfa.recovery_code_hashes or [])
        matched = None
        for index, stored in enumerate(hashes):
            if hmac.compare_digest(str(stored), candidate):
                matched = index
        if matched is None:
            raise AppError("AUTH_FAILED", "The authentication code is incorrect.", 401)
        mfa.recovery_code_hashes = [value for index, value in enumerate(hashes) if index != matched]
        used_recovery = True
    challenge.consumed_at = utcnow()
    session_token, csrf = _new_session(db, user, ip, family)
    write_audit(
        db,
        actor_user_id=user.id,
        action="LOGIN_SUCCESS",
        entity_type="user",
        entity_id=str(user.id),
        ip_address=ip,
        details={"used_backup_code": used_recovery},
    )
    db.commit()
    remaining = len(mfa.recovery_code_hashes or [])
    return session_token, csrf, used_recovery, remaining


def open_session(
    db: Session,
    user: User,
    ip: str | None,
    family: str | None,
    auth_method: str = "PASSWORD_MFA",
) -> tuple[str, str]:
    if auth_method not in {"PASSWORD_MFA", "WEBAUTHN"}:
        raise AppError("INTERNAL", "The authentication method is not supported.", 500)
    return _new_session(db, user, ip, family, auth_method)


def _new_session(db: Session, user: User, ip: str | None, family: str | None, auth_method: str = "PASSWORD_MFA") -> tuple[str, str]:
    token = new_token()
    csrf = new_csrf()
    now = utcnow()
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_token(token),
            csrf_token=csrf,
            created_at=now,
            expires_at=session_expiry(),
            last_seen_at=now,
            ip_address=(ip or "")[:64] or None,
            user_agent_family=(family or None),
            auth_method=auth_method,
        )
    )
    user.last_login_at = now
    return token, csrf


def qr_svg_base64(payload: str) -> str:
    image = qrcode.make(payload, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def change_password_in_session(db: Session, user: User, current_password: str, new_password: str, session_id, ip: str | None) -> None:
    if not verify_password(user.password_hash, current_password):
        raise AppError("AUTH_FAILED", "The current password is incorrect.", 401)
    validate_password(new_password, user.email)
    if verify_password(user.password_hash, new_password):
        raise AppError("VALIDATION_ERROR", "Choose a password that is different from the current one.", 422)
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.updated_at = utcnow()
    db.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user.id,
            AuthSession.id != session_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow())
    )
    write_audit(
        db,
        actor_user_id=user.id,
        action="PASSWORD_CHANGED",
        entity_type="user",
        entity_id=str(user.id),
        ip_address=ip,
        details={},
    )
    db.commit()
