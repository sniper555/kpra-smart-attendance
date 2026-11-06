import logging

from sqlalchemy import func, select

from app.audit import write_audit
from app.config import get_settings
from app.db import SessionLocal
from app.models import AttendancePolicy, AuditLog, Department, Employee, User
from app.security import hash_password, validate_password
from app.services.directory import _department
from app.timeutil import utcnow

logger = logging.getLogger("kpra.bootstrap")


def bootstrap() -> None:
    settings = get_settings()
    if settings.app_env == "production" and settings.seed_dev_data:
        raise SystemExit("Refusing to start: SEED_DEV_DATA is set in production")
    db = SessionLocal()
    try:
        _bootstrap_admin(db, settings)
        if settings.app_env == "development" and settings.seed_dev_data:
            _seed_manager(db, settings)
            _seed_admin_employee(db, settings)
            _seed_policy_once(db)
    finally:
        db.close()


def _bootstrap_admin(db, settings) -> None:
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        return
    email = settings.bootstrap_admin_email.strip().lower()
    existing = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing is not None:
        return
    validate_password(settings.bootstrap_admin_password, email)
    now = utcnow()
    db.add(
        User(
            email=email,
            display_name=settings.bootstrap_admin_name.strip() or "Administrator",
            password_hash=hash_password(settings.bootstrap_admin_password),
            role="ADMIN",
            is_active=True,
            must_change_password=True,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    logger.info("Bootstrap administrator created")


def _seed_manager(db, settings) -> None:
    if not settings.seed_manager_email or not settings.seed_manager_password:
        logger.warning("Development manager seed skipped because email or password is unset")
        return
    email = settings.seed_manager_email.strip().lower()
    existing = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing is not None:
        return
    validate_password(settings.seed_manager_password, email)
    now = utcnow()
    user = User(
        email=email,
        display_name=settings.seed_manager_name.strip() or "Test Manager",
        password_hash=hash_password(settings.seed_manager_password),
        role="MANAGER",
        is_active=True,
        must_change_password=False,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.flush()
    department = _department(db, "Test Department")
    db.add(
        Employee(
            user_id=user.id,
            department_id=department.id if department else None,
            employee_number="TEST-0001",
            full_name=user.display_name,
            designation="Test account",
            remote_attendance_enabled=True,
            is_active=True,
            is_test_record=True,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    logger.info("Development test manager created")


def _seed_admin_employee(db, settings) -> None:
    if not settings.bootstrap_admin_email:
        return
    email = settings.bootstrap_admin_email.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        return
    existing = db.scalar(select(Employee).where(Employee.user_id == user.id))
    if existing is not None:
        return
    now = utcnow()
    department = _department(db, "Test Department")
    db.add(
        Employee(
            user_id=user.id,
            department_id=department.id if department else None,
            employee_number="TEST-ADMIN",
            full_name=user.display_name,
            designation="Development administrator",
            remote_attendance_enabled=True,
            is_active=True,
            is_test_record=True,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    logger.info("Development administrator linked to test employee TEST-ADMIN")


def _seed_policy_once(db) -> None:
    already = db.scalar(select(AuditLog.id).where(AuditLog.action == "DEV_POLICY_SEED"))
    if already is not None:
        return
    policy = db.get(AttendancePolicy, 1)
    if policy is None:
        return
    policy.remote_attendance_approved = True
    policy.approved_at = utcnow()
    policy.policy_note = (
        "DEVELOPMENT ONLY. Remote attendance was enabled for local test accounts. "
        "This is not KPRA production approval."
    )
    policy.updated_at = utcnow()
    write_audit(
        db,
        actor_user_id=None,
        action="DEV_POLICY_SEED",
        entity_type="attendance_policy",
        entity_id="1",
        ip_address=None,
        details={"remote_attendance_approved": True},
    )
    db.commit()
    logger.info("Development remote-attendance policy enabled once")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bootstrap()
