"""Administrator-approved link between a local account and a TimeNex employee ID.

The TimeNex employee ID is not a password and does not create a session.
"""

import csv
import io
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.constants import IMPORT_CONFIRMATION
from app.errors import AppError
from app.models import Department, Employee, TimenexIdentityMapping, User
from app.timeutil import utcnow

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,31}")


def clean_timenex_id(value: str) -> str:
    text = (value or "").strip()
    if not _ID.fullmatch(text):
        raise AppError(
            "VALIDATION_ERROR",
            "TimeNex employee ID must be 1 to 32 letters, digits, or hyphens.",
            422,
        )
    return text


def account_status_for(user: User, employee: Employee) -> str:
    if user.is_active and employee.is_active:
        return "ACTIVE"
    return "INACTIVE"


def department_name_for(db: Session, employee: Employee) -> str:
    if employee.department_id is None:
        return "Unassigned"
    department = db.get(Department, employee.department_id)
    return department.name if department else "Unassigned"


def present_mapping(db: Session, row: TimenexIdentityMapping) -> dict:
    user = db.get(User, row.user_id)
    employee = db.get(Employee, row.employee_id)
    status = row.account_status
    if user and employee:
        status = account_status_for(user, employee)
    return {
        "id": str(row.id),
        "user_id": str(row.user_id),
        "employee_id": str(row.employee_id),
        "local_employee_number": employee.employee_number if employee else None,
        "timenex_employee_id": row.timenex_employee_id,
        "erp_employee_id": row.erp_employee_id,
        "employee_name": row.employee_name,
        "department_name": row.department_name,
        "account_status": status,
        "verification_status": row.verification_status,
        "verification_source": row.verification_source,
        "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        "authenticates_by_itself": False,
    }


def set_erp_employee_id(
    db: Session,
    *,
    actor_id: uuid.UUID,
    mapping_id: uuid.UUID,
    erp_employee_id: str,
    reason: str,
    ip_address: str | None,
) -> TimenexIdentityMapping:
    if len(reason.strip()) < 10:
        raise AppError("VALIDATION_ERROR", "Give a reason of at least 10 characters.", 422)
    row = db.get(TimenexIdentityMapping, mapping_id)
    if row is None:
        raise AppError("NOT_FOUND", "That mapping was not found.", 404)
    if row.verification_status != "VERIFIED":
        raise AppError("CONFLICT", "Verify the TimeNex link before storing an ERP internal ID.", 409)
    cleaned = clean_timenex_id(erp_employee_id)
    row.erp_employee_id = cleaned
    row.updated_at = utcnow()
    write_audit(
        db,
        actor_user_id=actor_id,
        action="ERP_INTERNAL_ID_RECORDED",
        entity_type="timenex_identity_mapping",
        entity_id=str(row.id),
        ip_address=ip_address,
        details={
            "timenex_employee_id": row.timenex_employee_id,
            "erp_employee_id": cleaned,
            "reason": reason.strip()[:500],
        },
    )
    db.commit()
    db.refresh(row)
    return row


def _employee_by_number(db: Session, employee_number: str) -> Employee:
    number = employee_number.strip()
    employee = db.scalar(select(Employee).where(Employee.employee_number == number))
    if employee is None:
        raise AppError("NOT_FOUND", "No employee in this application uses that employee number.", 404)
    return employee


def _rejected_row_for_replacement(
    db: Session,
    employee: Employee,
    user: User,
    timenex_id: str,
) -> TimenexIdentityMapping | None:
    rows = db.scalars(
        select(TimenexIdentityMapping).where(
            (TimenexIdentityMapping.employee_id == employee.id)
            | (TimenexIdentityMapping.user_id == user.id)
            | (TimenexIdentityMapping.timenex_employee_id == timenex_id)
        )
    ).all()
    own = [row for row in rows if row.employee_id == employee.id or row.user_id == user.id]
    others = [row for row in rows if row not in own]
    if others:
        raise AppError("CONFLICT", "That TimeNex employee ID is already mapped to another person.", 409)
    if not own:
        return None
    if len(own) > 1 or own[0].verification_status != "REJECTED":
        raise AppError(
            "CONFLICT",
            "A pending or verified mapping already exists for that person.",
            409,
        )
    return own[0]


def create_mapping(
    db: Session,
    *,
    actor_id,
    employee_number: str,
    timenex_employee_id: str,
    ip_address: str | None,
) -> TimenexIdentityMapping:
    employee = _employee_by_number(db, employee_number)
    user = db.get(User, employee.user_id)
    if user is None:
        raise AppError("NOT_FOUND", "The employee account is missing.", 404)
    timenex_id = clean_timenex_id(timenex_employee_id)
    existing = _rejected_row_for_replacement(db, employee, user, timenex_id)
    now = utcnow()
    if existing is not None:
        previous_id = existing.timenex_employee_id
        existing.timenex_employee_id = timenex_id
        existing.employee_name = employee.full_name
        existing.department_name = department_name_for(db, employee)
        existing.account_status = account_status_for(user, employee)
        existing.verification_status = "PENDING"
        existing.verification_source = None
        existing.verified_by = None
        existing.verified_at = None
        existing.updated_at = now
        write_audit(
            db,
            actor_user_id=actor_id,
            action="TIMENEX_MAPPING_REPLACED",
            entity_type="timenex_identity_mapping",
            entity_id=str(existing.id),
            ip_address=ip_address,
            details={
                "employee_number": employee.employee_number,
                "previous_timenex_employee_id": previous_id,
                "timenex_employee_id": timenex_id,
            },
        )
        db.commit()
        return existing
    row = TimenexIdentityMapping(
        user_id=user.id,
        employee_id=employee.id,
        timenex_employee_id=timenex_id,
        employee_name=employee.full_name,
        department_name=department_name_for(db, employee),
        account_status=account_status_for(user, employee),
        verification_status="PENDING",
        verification_source=None,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    write_audit(
        db,
        actor_user_id=actor_id,
        action="TIMENEX_MAPPING_CREATED",
        entity_type="timenex_identity_mapping",
        entity_id=str(row.id),
        ip_address=ip_address,
        details={"timenex_employee_id": timenex_id, "employee_number": employee.employee_number},
    )
    db.commit()
    return row


def decide_mapping(
    db: Session,
    *,
    actor_id,
    mapping_id,
    decision: str,
    reason: str,
    ip_address: str | None,
) -> TimenexIdentityMapping:
    row = db.get(TimenexIdentityMapping, mapping_id)
    if row is None:
        raise AppError("NOT_FOUND", "That TimeNex mapping was not found.", 404)
    if row.verification_status != "PENDING":
        raise AppError("CONFLICT", "Only a pending mapping can be approved or rejected.", 409)
    if len(reason.strip()) < 10:
        raise AppError("VALIDATION_ERROR", "A reason of at least 10 characters is required.", 422)
    now = utcnow()
    if decision == "APPROVE":
        row.verification_status = "VERIFIED"
        row.verification_source = "ADMIN_APPROVAL"
        action = "TIMENEX_MAPPING_APPROVED"
    elif decision == "REJECT":
        row.verification_status = "REJECTED"
        action = "TIMENEX_MAPPING_REJECTED"
    else:
        raise AppError("VALIDATION_ERROR", "Decision must be APPROVE or REJECT.", 422)
    user = db.get(User, row.user_id)
    employee = db.get(Employee, row.employee_id)
    if user and employee:
        row.account_status = account_status_for(user, employee)
        row.employee_name = employee.full_name
        row.department_name = department_name_for(db, employee)
    row.verified_by = actor_id
    row.verified_at = now
    row.updated_at = now
    write_audit(
        db,
        actor_user_id=actor_id,
        action=action,
        entity_type="timenex_identity_mapping",
        entity_id=str(row.id),
        ip_address=ip_address,
        details={"reason": reason.strip()[:500], "timenex_employee_id": row.timenex_employee_id},
    )
    db.commit()
    return row


def import_mapping_csv(
    db: Session,
    *,
    actor_id,
    content: str,
    confirmation: str,
    reason: str,
    ip_address: str | None,
) -> dict:
    if confirmation.strip() != IMPORT_CONFIRMATION:
        raise AppError("VALIDATION_ERROR", "Type AUTHORIZED EXPORT to confirm this file was supplied to you.", 422)
    if len(reason.strip()) < 10:
        raise AppError("VALIDATION_ERROR", "A reason of at least 10 characters is required.", 422)
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    required = {"employee_number", "timenex_employee_id", "full_name", "department_name"}
    if reader.fieldnames is None or not required.issubset({name.strip() for name in reader.fieldnames}):
        raise AppError(
            "VALIDATION_ERROR",
            "The file needs columns employee_number, timenex_employee_id, full_name, and department_name.",
            422,
        )
    prepared: list[tuple[Employee, User, str, TimenexIdentityMapping | None]] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_numbers: set[str] = set()
    for index, raw in enumerate(reader, start=2):
        number = (raw.get("employee_number") or "").strip()
        full_name = (raw.get("full_name") or "").strip()
        department_name = (raw.get("department_name") or "").strip()
        try:
            timenex_id = clean_timenex_id(raw.get("timenex_employee_id") or "")
        except AppError as exc:
            errors.append(f"Row {index}: {exc.message}")
            continue
        if not number or not full_name or not department_name:
            errors.append(f"Row {index}: employee number, name, and department are required.")
            continue
        if number in seen_numbers or timenex_id in seen_ids:
            errors.append(f"Row {index}: duplicate employee number or TimeNex ID in the file.")
            continue
        seen_numbers.add(number)
        seen_ids.add(timenex_id)
        employee = db.scalar(select(Employee).where(Employee.employee_number == number))
        if employee is None:
            errors.append(f"Row {index}: employee {number} is not in this application.")
            continue
        if employee.full_name.casefold() != full_name.casefold():
            errors.append(f"Row {index}: the name does not match employee {number}.")
            continue
        local_department = department_name_for(db, employee)
        if local_department.casefold() != department_name.casefold():
            errors.append(f"Row {index}: the department does not match employee {number}.")
            continue
        user = db.get(User, employee.user_id)
        if user is None:
            errors.append(f"Row {index}: employee {number} has no account.")
            continue
        try:
            existing = _rejected_row_for_replacement(db, employee, user, timenex_id)
        except AppError as exc:
            errors.append(f"Row {index}: {exc.message}")
            continue
        prepared.append((employee, user, timenex_id, existing))
        if len(prepared) > 500:
            errors.append("The file has more than 500 rows.")
            break
    if errors or not prepared:
        raise AppError(
            "VALIDATION_ERROR",
            " ".join(errors[:8]) or "The file has no rows to import.",
            422,
        )
    now = utcnow()
    created = 0
    for employee, user, timenex_id, existing in prepared:
        if existing is None:
            db.add(
                TimenexIdentityMapping(
                    user_id=user.id,
                    employee_id=employee.id,
                    timenex_employee_id=timenex_id,
                    employee_name=employee.full_name,
                    department_name=department_name_for(db, employee),
                    account_status=account_status_for(user, employee),
                    verification_status="VERIFIED",
                    verification_source="AUTHORIZED_EXPORT",
                    verified_by=actor_id,
                    verified_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            existing.timenex_employee_id = timenex_id
            existing.employee_name = employee.full_name
            existing.department_name = department_name_for(db, employee)
            existing.account_status = account_status_for(user, employee)
            existing.verification_status = "VERIFIED"
            existing.verification_source = "AUTHORIZED_EXPORT"
            existing.verified_by = actor_id
            existing.verified_at = now
            existing.updated_at = now
        created += 1
    write_audit(
        db,
        actor_user_id=actor_id,
        action="TIMENEX_MAPPING_IMPORTED",
        entity_type="timenex_identity_mapping",
        entity_id=None,
        ip_address=ip_address,
        details={"rows": created, "reason": reason.strip()[:500]},
    )
    db.commit()
    return {
        "created": created,
        "verification_status": "VERIFIED",
        "message": "Mappings were verified from the authorized file. Nothing was read from or sent to TimeNex.",
    }
