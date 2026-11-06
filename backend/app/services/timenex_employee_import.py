"""Import people from an authorized TimeNex employee export.

The TimeNex employee ID is stored as the local employee number and as the mapping ID.
It is not a password. In, Out, and Status on this export are a snapshot, not attendance to upload.
"""

import csv
import io
import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.constants import IMPORT_CONFIRMATION
from app.db import constraint_name
from app.errors import AppError
from app.models import Department, Employee, TimenexIdentityMapping, User
from app.security import hash_password
from app.services.timenex_mapping import account_status_for, clean_timenex_id, department_name_for
from app.timeutil import utcnow

REQUIRED_COLUMNS = ("ID", "Name", "Department", "Designation")
IGNORED_COLUMNS = ("S#", "File #", "Gender", "Bps", "In", "Out", "Status", "Remarks")
ATTENDANCE_NOTE = (
    "In, Out, and Status on this file are a snapshot from TimeNex. "
    "They are not imported as attendance, and no attendance file was generated for TimeNex."
)


def preview_timenex_employees(db: Session, raw: bytes) -> dict:
    rows, columns = _rows(raw)
    reviewed = _review(db, rows)
    blocked = [row for row in reviewed if row["blocking"]]
    ready = [row for row in reviewed if row["result"] == "READY"]
    return {
        "columns": columns,
        "used_columns": list(REQUIRED_COLUMNS),
        "ignored_columns": [name for name in IGNORED_COLUMNS if name in columns],
        "attendance_import_supported": False,
        "attendance_note": ATTENDANCE_NOTE,
        "id_is_password": False,
        "total": len(reviewed),
        "ready": len(ready),
        "already_stored": sum(1 for row in reviewed if row["result"] == "ALREADY_STORED"),
        "missing_department": sum(1 for row in ready if not row["department_name"]),
        "blocked": len(blocked),
        "rows": reviewed,
    }


def import_timenex_employees(
    db: Session,
    *,
    raw: bytes,
    reason: str,
    confirmation: str,
    actor_id: uuid.UUID,
    ip_address: str | None,
) -> dict:
    if confirmation.strip() != IMPORT_CONFIRMATION:
        raise AppError("VALIDATION_ERROR", f"Type {IMPORT_CONFIRMATION} to confirm this is an authorized export.", 422)
    if len(reason.strip()) < 10:
        raise AppError("VALIDATION_ERROR", "Give a reason of at least 10 characters.", 422)
    reviewed = preview_timenex_employees(db, raw)
    if reviewed["blocked"]:
        first = next(row for row in reviewed["rows"] if row["blocking"])
        raise AppError("VALIDATION_ERROR", f"Row {first['line']}: {first['message']}", 422)
    ready = [row for row in reviewed["rows"] if row["result"] == "READY"]
    if not ready and reviewed["already_stored"] == 0:
        raise AppError("VALIDATION_ERROR", "The file has no employee rows.", 422)
    now = utcnow()
    imported = 0
    try:
        for item in ready:
            department = _department(db, item["department_name"], now)
            user = User(
                email=f"timenex.{item['timenex_employee_id']}@kpra.example",
                display_name=item["full_name"],
                password_hash=hash_password(secrets.token_urlsafe(24)),
                role="MANAGER",
                is_active=False,
                must_change_password=True,
                created_at=now,
                updated_at=now,
            )
            db.add(user)
            db.flush()
            employee = Employee(
                user_id=user.id,
                department_id=department.id if department else None,
                employee_number=item["timenex_employee_id"],
                full_name=item["full_name"],
                designation=item["designation"] or None,
                device_pin=None,
                remote_attendance_enabled=False,
                is_active=False,
                is_test_record=False,
                created_at=now,
                updated_at=now,
            )
            db.add(employee)
            db.flush()
            db.add(
                TimenexIdentityMapping(
                    user_id=user.id,
                    employee_id=employee.id,
                    timenex_employee_id=item["timenex_employee_id"],
                    employee_name=item["full_name"],
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
            imported += 1
        batch_id = uuid.uuid4().hex
        write_audit(
            db,
            actor_user_id=actor_id,
            action="TIMENEX_EMPLOYEES_IMPORTED",
            entity_type="import_batch",
            entity_id=batch_id,
            ip_address=ip_address,
            details={
                "imported": imported,
                "already_stored": reviewed["already_stored"],
                "reason": reason.strip()[:500],
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError("CONFLICT", f"A row conflicts with an existing record ({constraint_name(exc)}).", 409) from exc
    return {
        "batch_id": batch_id,
        "imported": imported,
        "already_stored": reviewed["already_stored"],
        "attendance_imported": 0,
        "id_is_password": False,
        "accounts_can_sign_in": False,
        "message": (
            "Employees were stored in this application and linked to their TimeNex IDs. "
            "The IDs are not passwords. Accounts stay inactive, so these people cannot sign in and are not added to today's board. "
            "No attendance was imported or sent to TimeNex."
        ),
    }


def _rows(raw: bytes) -> tuple[list[dict], list[str]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError("VALIDATION_ERROR", "The file must be UTF-8 CSV.", 422) from exc
    reader = csv.DictReader(io.StringIO(text))
    columns = [name.strip() for name in (reader.fieldnames or [])]
    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    if missing:
        raise AppError("VALIDATION_ERROR", "Missing columns: " + ", ".join(missing), 422)
    rows = list(reader)
    if len(rows) > 500:
        raise AppError("VALIDATION_ERROR", "The file must contain no more than 500 employee rows.", 422)
    return rows, columns


def _review(db: Session, rows: list[dict]) -> list[dict]:
    reviewed: list[dict] = []
    seen: dict[str, str] = {}
    for line, raw in enumerate(rows, start=2):
        timenex_id = (raw.get("ID") or "").strip()
        full_name = " ".join((raw.get("Name") or "").split())
        department_name = " ".join((raw.get("Department") or "").split())
        designation = " ".join((raw.get("Designation") or "").split())
        export_status = (raw.get("Status") or "").strip()
        item = {
            "line": line,
            "timenex_employee_id": timenex_id,
            "full_name": full_name,
            "department_name": department_name,
            "designation": designation,
            "export_status": export_status,
            "result": "READY",
            "blocking": False,
            "message": "Ready to store. The TimeNex ID is not a password. The export status is not attendance.",
        }
        if not timenex_id or len(full_name) < 2:
            item["result"] = "INCOMPLETE"
            item["blocking"] = True
            item["message"] = "ID and name are required."
            reviewed.append(item)
            continue
        try:
            timenex_id = clean_timenex_id(timenex_id)
        except AppError:
            item["result"] = "INCOMPLETE"
            item["blocking"] = True
            item["message"] = "The ID must be 1 to 32 letters, digits, or hyphens."
            reviewed.append(item)
            continue
        item["timenex_employee_id"] = timenex_id
        if len(full_name) > 120 or len(department_name) > 120 or len(designation) > 120:
            item["result"] = "INCOMPLETE"
            item["blocking"] = True
            item["message"] = "Name, department, or designation is longer than 120 characters."
            reviewed.append(item)
            continue
        previous = seen.get(timenex_id)
        if previous is not None and previous.casefold() != full_name.casefold():
            item["result"] = "INCONSISTENT"
            item["blocking"] = True
            item["message"] = "This ID appears again with a different name."
            reviewed.append(item)
            continue
        if previous is not None:
            item["result"] = "DUPLICATE_IN_FILE"
            item["blocking"] = True
            item["message"] = "This ID is repeated in the file."
            reviewed.append(item)
            continue
        seen[timenex_id] = full_name
        stored = _stored_name(db, timenex_id)
        if stored is not None and stored.casefold() != full_name.casefold():
            item["result"] = "INCONSISTENT"
            item["blocking"] = True
            item["message"] = "This ID is already stored with a different name."
        elif stored is not None:
            item["result"] = "ALREADY_STORED"
            item["message"] = "This ID is already stored. It will be left unchanged."
        elif not department_name:
            item["message"] = "Ready to store. Department is blank, so it will be saved as Unassigned. The TimeNex ID is not a password."
        reviewed.append(item)
    return reviewed


def _stored_name(db: Session, timenex_id: str) -> str | None:
    employee = db.scalar(select(Employee).where(Employee.employee_number == timenex_id))
    if employee is not None:
        return employee.full_name
    mapping = db.scalar(
        select(TimenexIdentityMapping).where(TimenexIdentityMapping.timenex_employee_id == timenex_id)
    )
    if mapping is not None:
        return mapping.employee_name
    return None


def _department(db: Session, name: str, now) -> Department | None:
    if not name:
        return None
    found = db.scalar(select(Department).where(func.lower(Department.name) == name.casefold()))
    if found is not None:
        return found
    created = Department(name=name, created_at=now)
    db.add(created)
    db.flush()
    return created


