import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.constants import ALLOW_PASSWORD_ATTENDANCE, APPROVE_REMOTE, DAY_CODES, REQUIRE_PASSKEY, REVOKE_REMOTE
from app.db import constraint_name
from app.errors import AppError
from app.models import (
    AttendancePolicy,
    AuthSession,
    Department,
    Device,
    Employee,
    EmployeeDeviceMapping,
    User,
    UserMfa,
)
from app.security import hash_password, validate_password
from app.timeutil import parse_hhmm, utcnow


def revoke_sessions(db: Session, user_id: uuid.UUID) -> None:
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


def _clean_pin(pin: str | None) -> str | None:
    if pin is None or pin.strip() == "":
        return None
    value = pin.strip()
    if not value.isdigit() or not 1 <= len(value) <= 14:
        raise AppError("VALIDATION_ERROR", "Device PIN must be 1 to 14 digits.", 422)
    return value


def _clean_number(value: str) -> str:
    number = value.strip()
    if not number or len(number) > 32 or not all(char.isalnum() or char in "-/" for char in number):
        raise AppError(
            "VALIDATION_ERROR",
            "Employee number may contain letters, numbers, a hyphen, or a slash.",
            422,
        )
    return number


def _department(db: Session, name: str | None) -> Department | None:
    if name is None or not name.strip():
        return None
    cleaned = " ".join(name.split())
    if len(cleaned) > 120:
        raise AppError("VALIDATION_ERROR", "Department name is too long.", 422)
    existing = db.scalar(select(Department).where(func.lower(Department.name) == cleaned.lower()))
    if existing:
        return existing
    created = Department(name=cleaned, created_at=utcnow())
    db.add(created)
    db.flush()
    return created


def create_account(db: Session, body, actor_id: uuid.UUID, ip: str | None) -> User:
    email = body.email.strip().lower()
    validate_password(body.temporary_password, email)
    if body.role == "MANAGER" and not (body.employee_number and body.employee_number.strip()):
        raise AppError("VALIDATION_ERROR", "A manager account needs an employee number.", 422)
    now = utcnow()
    user = User(
        email=email,
        display_name=body.display_name.strip(),
        password_hash=hash_password(body.temporary_password),
        role=body.role,
        is_active=True,
        must_change_password=True,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        _raise_unique(exc)
    if body.employee_number and body.employee_number.strip():
        department = _department(db, body.department_name)
        db.add(
            Employee(
                user_id=user.id,
                department_id=department.id if department else None,
                employee_number=_clean_number(body.employee_number),
                device_pin=_clean_pin(body.device_pin),
                full_name=body.display_name.strip(),
                designation=(body.designation or "").strip() or None,
                remote_attendance_enabled=body.remote_attendance_enabled,
                is_active=True,
                is_test_record=body.is_test_record,
                created_at=now,
                updated_at=now,
            )
        )
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        _raise_unique(exc)
    write_audit(
        db,
        actor_user_id=actor_id,
        action="ACCOUNT_CREATED",
        entity_type="user",
        entity_id=str(user.id),
        ip_address=ip,
        details={"role": body.role, "email": email, "is_test_record": body.is_test_record},
    )
    db.commit()
    return user


def _raise_unique(exc: IntegrityError) -> None:
    name = constraint_name(exc)
    if "email" in name:
        raise AppError("CONFLICT", "An account with this email already exists.", 409) from exc
    if "employee_number" in name:
        raise AppError("CONFLICT", "That employee number is already in use.", 409) from exc
    if "device_pin" in name:
        raise AppError("CONFLICT", "That device PIN is already in use.", 409) from exc
    raise AppError("CONFLICT", "The account conflicts with an existing record.", 409) from exc


def update_account(db: Session, user_id: uuid.UUID, body, actor_id: uuid.UUID, ip: str | None) -> User:
    if not body.model_fields_set:
        raise AppError("VALIDATION_ERROR", "No changes were provided.", 422)
    user = db.get(User, user_id)
    if user is None:
        raise AppError("NOT_FOUND", "Account was not found.", 404)
    employee = db.scalar(select(Employee).where(Employee.user_id == user.id))
    if "display_name" in body.model_fields_set and body.display_name:
        user.display_name = body.display_name.strip()
        if employee:
            employee.full_name = user.display_name
    if employee is None and body.model_fields_set.intersection(
        {"designation", "department_name", "employee_number", "device_pin", "remote_attendance_enabled", "is_test_record"}
    ):
        raise AppError("CONFLICT", "This account has no employee profile to update.", 409)
    if employee is not None:
        if "designation" in body.model_fields_set:
            employee.designation = (body.designation or "").strip() or None
        if "department_name" in body.model_fields_set:
            department = _department(db, body.department_name)
            employee.department_id = department.id if department else None
        if "employee_number" in body.model_fields_set and body.employee_number:
            employee.employee_number = _clean_number(body.employee_number)
        if "device_pin" in body.model_fields_set:
            employee.device_pin = _clean_pin(body.device_pin)
        if "remote_attendance_enabled" in body.model_fields_set:
            employee.remote_attendance_enabled = bool(body.remote_attendance_enabled)
        if "is_test_record" in body.model_fields_set:
            employee.is_test_record = bool(body.is_test_record)
        employee.updated_at = utcnow()
    user.updated_at = utcnow()
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        _raise_unique(exc)
    write_audit(
        db,
        actor_user_id=actor_id,
        action="ACCOUNT_UPDATED",
        entity_type="user",
        entity_id=str(user.id),
        ip_address=ip,
        details={"fields": sorted(body.model_fields_set)},
    )
    db.commit()
    return user


def set_active(db: Session, user_id: uuid.UUID, active: bool, actor_id: uuid.UUID, ip: str | None) -> User:
    if user_id == actor_id and not active:
        raise AppError("CONFLICT", "You cannot deactivate your own account.", 409)
    user = db.get(User, user_id)
    if user is None:
        raise AppError("NOT_FOUND", "Account was not found.", 404)
    if user.role == "ADMIN" and not active:
        others = db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "ADMIN", User.is_active.is_(True), User.id != user.id)
        )
        if not others:
            raise AppError("CONFLICT", "The last active administrator cannot be deactivated.", 409)
    user.is_active = active
    user.updated_at = utcnow()
    employee = db.scalar(select(Employee).where(Employee.user_id == user.id))
    if employee is not None:
        employee.is_active = active
        employee.updated_at = utcnow()
    if not active:
        revoke_sessions(db, user.id)
    write_audit(
        db,
        actor_user_id=actor_id,
        action="ACCOUNT_REACTIVATED" if active else "ACCOUNT_DEACTIVATED",
        entity_type="user",
        entity_id=str(user.id),
        ip_address=ip,
        details={"active": active},
    )
    db.commit()
    return user


def reset_mfa(db: Session, user_id: uuid.UUID, actor_id: uuid.UUID, ip: str | None) -> None:
    user = db.get(User, user_id)
    if user is None:
        raise AppError("NOT_FOUND", "Account was not found.", 404)
    mfa = db.get(UserMfa, user.id)
    if mfa is not None:
        db.delete(mfa)
    revoke_sessions(db, user.id)
    write_audit(
        db,
        actor_user_id=actor_id,
        action="MFA_RESET",
        entity_type="user",
        entity_id=str(user.id),
        ip_address=ip,
        details={},
    )
    db.commit()


def _validate_policy(policy: AttendancePolicy) -> None:
    if policy.inference_enabled:
        if policy.work_start_local is None or policy.work_end_local is None or policy.grace_minutes is None:
            raise AppError(
                "VALIDATION_ERROR",
                "Set a start time, end time, and grace period before turning on late/early inference.",
                422,
            )
        if policy.work_end_local <= policy.work_start_local:
            raise AppError(
                "VALIDATION_ERROR",
                "Overnight schedules are not evaluated. Leave inference off until a same-day schedule is confirmed.",
                422,
            )
    if policy.enforce_working_days and not policy.working_days:
        raise AppError(
            "VALIDATION_ERROR",
            "Choose at least one working day before enforcing them.",
            422,
        )


def update_policy(db: Session, body, actor_id: uuid.UUID, ip: str | None) -> AttendancePolicy:
    policy = db.get(AttendancePolicy, 1)
    if policy is None:
        raise AppError("INTERNAL", "Attendance policy is not configured.", 500)
    fields = body.model_fields_set
    if "remote_attendance_approved" in fields and body.remote_attendance_approved != policy.remote_attendance_approved:
        expected = APPROVE_REMOTE if body.remote_attendance_approved else REVOKE_REMOTE
        if (body.confirmation or "").strip() != expected:
            raise AppError("VALIDATION_ERROR", f"Type {expected} to confirm this policy change.", 422)
        policy.remote_attendance_approved = body.remote_attendance_approved
        policy.approved_at = utcnow() if body.remote_attendance_approved else None
        policy.approved_by = actor_id if body.remote_attendance_approved else None
    if (
        "passkey_required_for_attendance" in fields
        and body.passkey_required_for_attendance is not None
        and body.passkey_required_for_attendance != policy.passkey_required_for_attendance
    ):
        expected = REQUIRE_PASSKEY if body.passkey_required_for_attendance else ALLOW_PASSWORD_ATTENDANCE
        if (body.confirmation or "").strip() != expected:
            raise AppError("VALIDATION_ERROR", f"Type {expected} to confirm this policy change.", 422)
        policy.passkey_required_for_attendance = body.passkey_required_for_attendance
    if "working_days" in fields and body.working_days is not None:
        policy.working_days = [day for day in DAY_CODES if day in body.working_days]
    if "work_start_local" in fields:
        policy.work_start_local = None if not body.work_start_local else _time_or_error(body.work_start_local)
    if "work_end_local" in fields:
        policy.work_end_local = None if not body.work_end_local else _time_or_error(body.work_end_local)
    if "grace_minutes" in fields:
        policy.grace_minutes = body.grace_minutes
    if "inference_enabled" in fields and body.inference_enabled is not None:
        policy.inference_enabled = body.inference_enabled
    if "enforce_working_days" in fields and body.enforce_working_days is not None:
        policy.enforce_working_days = body.enforce_working_days
    if "policy_note" in fields and body.policy_note is not None:
        policy.policy_note = body.policy_note.strip()
    _validate_policy(policy)
    policy.updated_at = utcnow()
    write_audit(
        db,
        actor_user_id=actor_id,
        action="POLICY_UPDATED",
        entity_type="attendance_policy",
        entity_id="1",
        ip_address=ip,
        details={
            "remote_attendance_approved": policy.remote_attendance_approved,
            "inference_enabled": policy.inference_enabled,
            "enforce_working_days": policy.enforce_working_days,
            "passkey_required_for_attendance": policy.passkey_required_for_attendance,
        },
    )
    db.commit()
    return policy


def _time_or_error(value: str):
    try:
        return parse_hhmm(value)
    except ValueError as exc:
        raise AppError("VALIDATION_ERROR", "Times must use HH:MM in 24-hour form.", 422) from exc


def create_device(db: Session, body, actor_id: uuid.UUID, ip: str | None) -> Device:
    now = utcnow()
    device = Device(
        name=body.name.strip(),
        model=body.model.strip(),
        serial_number=body.serial_number.strip(),
        firmware=(body.firmware or "").strip() or None,
        platform=(body.platform or "").strip() or None,
        mac_address=(body.mac_address or "").strip() or None,
        location=(body.location or "").strip() or None,
        protocol=body.protocol,
        status="NOT_CONNECTED",
        notes=(body.notes or "").strip() or None,
        created_at=now,
        updated_at=now,
    )
    db.add(device)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError("CONFLICT", "A device with this serial number is already registered.", 409) from exc
    write_audit(
        db,
        actor_user_id=actor_id,
        action="DEVICE_REGISTERED",
        entity_type="device",
        entity_id=str(device.id),
        ip_address=ip,
        details={"serial_number": device.serial_number, "status": device.status},
    )
    db.commit()
    return device


def update_device(db: Session, device_id: uuid.UUID, body, actor_id: uuid.UUID, ip: str | None) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise AppError("NOT_FOUND", "Device was not found.", 404)
    if "name" in body.model_fields_set and body.name:
        device.name = body.name.strip()
    if "firmware" in body.model_fields_set:
        device.firmware = (body.firmware or "").strip() or None
    if "location" in body.model_fields_set:
        device.location = (body.location or "").strip() or None
    if "notes" in body.model_fields_set:
        device.notes = (body.notes or "").strip() or None
    if "status" in body.model_fields_set and body.status:
        device.status = body.status
    device.updated_at = utcnow()
    device.last_seen_at = None
    write_audit(
        db,
        actor_user_id=actor_id,
        action="DEVICE_UPDATED",
        entity_type="device",
        entity_id=str(device.id),
        ip_address=ip,
        details={"status": device.status},
    )
    db.commit()
    return device


def create_mapping(db: Session, device_id: uuid.UUID, body, actor_id: uuid.UUID, ip: str | None) -> EmployeeDeviceMapping:
    device = db.get(Device, device_id)
    if device is None:
        raise AppError("NOT_FOUND", "Device was not found.", 404)
    employee = db.get(Employee, body.employee_id)
    if employee is None:
        raise AppError("NOT_FOUND", "Employee was not found.", 404)
    pin = _clean_pin(body.device_user_pin)
    mapping = EmployeeDeviceMapping(
        employee_id=employee.id,
        device_id=device.id,
        device_user_pin=pin,
        created_at=utcnow(),
    )
    db.add(mapping)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError("CONFLICT", "That employee or PIN is already mapped on this device.", 409) from exc
    write_audit(
        db,
        actor_user_id=actor_id,
        action="DEVICE_MAPPING_CREATED",
        entity_type="employee_device_mapping",
        entity_id=str(mapping.id),
        ip_address=ip,
        details={"device_serial": device.serial_number, "employee_number": employee.employee_number},
    )
    db.commit()
    return mapping


def delete_mapping(db: Session, mapping_id: uuid.UUID, actor_id: uuid.UUID, ip: str | None) -> None:
    mapping = db.get(EmployeeDeviceMapping, mapping_id)
    if mapping is None:
        raise AppError("NOT_FOUND", "Mapping was not found.", 404)
    db.delete(mapping)
    write_audit(
        db,
        actor_user_id=actor_id,
        action="DEVICE_MAPPING_REMOVED",
        entity_type="employee_device_mapping",
        entity_id=str(mapping_id),
        ip_address=ip,
        details={},
    )
    db.commit()
