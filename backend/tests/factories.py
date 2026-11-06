import pyotp
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import AttendancePolicy, Employee, User, UserMfa
from app.security import encrypt_secret, hash_password
from app.timeutil import utcnow

PASSWORD = "Correct-Horse-9"
SECRET = "JBSWY3DPEHPK3PXP"


def add_user(
    *,
    email: str,
    role: str = "MANAGER",
    secret: str | None = SECRET,
    employee_number: str | None = None,
    full_name: str = "Amina Khan",
    remote: bool = True,
    test_record: bool = True,
    active: bool = True,
    must_change: bool = False,
) -> None:
    session = SessionLocal()
    now = utcnow()
    user = User(
        email=email,
        display_name=full_name,
        password_hash=hash_password(PASSWORD),
        role=role,
        is_active=active,
        must_change_password=must_change,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.flush()
    if secret:
        session.add(
            UserMfa(
                user_id=user.id,
                totp_secret_encrypted=encrypt_secret(secret),
                enabled=True,
                recovery_code_hashes=[],
                enrolled_at=now,
                created_at=now,
            )
        )
    if employee_number:
        session.add(
            Employee(
                user_id=user.id,
                employee_number=employee_number,
                full_name=full_name,
                designation="Deputy Director",
                remote_attendance_enabled=remote,
                is_active=active,
                is_test_record=test_record,
                created_at=now,
                updated_at=now,
            )
        )
    session.commit()
    session.close()


def enable_remote_policy() -> None:
    session = SessionLocal()
    policy = session.get(AttendancePolicy, 1)
    policy.remote_attendance_approved = True
    policy.passkey_required_for_attendance = False
    policy.updated_at = utcnow()
    session.commit()
    session.close()


def sign_in(client: TestClient, email: str, secret: str = SECRET) -> str:
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "mfa_required"
    code = pyotp.TOTP(secret).now()
    response = client.post("/api/auth/mfa/verify", json={"code": code})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]
