import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet
from fastapi import Response

from app.config import get_settings
from app.constants import CHALLENGE_COOKIE, CHALLENGE_MINUTES, SESSION_COOKIE
from app.errors import AppError
from app.timeutil import utcnow

_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
_dummy_hash: str | None = None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False


def dummy_password_hash() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("not-a-real-password")
    return _dummy_hash


def validate_password(password: str, email: str) -> None:
    if not 12 <= len(password) <= 128:
        raise AppError("VALIDATION_ERROR", "Password must be 12 to 128 characters.", 422)
    if not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password):
        raise AppError(
            "VALIDATION_ERROR",
            "Password must include at least one letter and one number.",
            422,
        )
    local = email.split("@", 1)[0].lower()
    if password.lower() == email.lower() or (len(local) >= 4 and local in password.lower()):
        raise AppError("VALIDATION_ERROR", "Password must not contain the email address.", 422)


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().app_secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def normalize_recovery_code(code: str) -> str:
    return "".join(char for char in code.strip().upper() if char.isalnum())


def hash_recovery_code(code: str) -> str:
    pepper = get_settings().app_secret_key.encode()
    normalized = normalize_recovery_code(code)
    return hmac.new(pepper, normalized.encode(), hashlib.sha256).hexdigest()


def generate_recovery_codes(count: int = 10) -> list[str]:
    codes: list[str] = []
    while len(codes) < count:
        raw = secrets.token_hex(4).upper()
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_csrf() -> str:
    return secrets.token_urlsafe(32)


def totp_for(secret: str) -> pyotp.TOTP:
    return pyotp.TOTP(secret)


def provisioning_uri(secret: str, email: str) -> str:
    return totp_for(secret).provisioning_uri(name=email, issuer_name=get_settings().mfa_issuer)


def cookie_kwargs() -> dict:
    settings = get_settings()
    return {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "path": "/",
    }


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=get_settings().session_hours * 3600,
        **cookie_kwargs(),
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", **{k: v for k, v in cookie_kwargs().items() if k != "httponly"})


def set_challenge_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        CHALLENGE_COOKIE,
        token,
        max_age=CHALLENGE_MINUTES * 60,
        **cookie_kwargs(),
    )


def clear_challenge_cookie(response: Response) -> None:
    response.delete_cookie(CHALLENGE_COOKIE, path="/", **{k: v for k, v in cookie_kwargs().items() if k != "httponly"})


def challenge_expiry():
    return utcnow() + timedelta(minutes=CHALLENGE_MINUTES)


def session_expiry():
    return utcnow() + timedelta(hours=get_settings().session_hours)
