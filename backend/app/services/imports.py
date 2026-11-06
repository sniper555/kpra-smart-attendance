import csv
import hashlib
import io
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.constants import IMPORT_CONFIRMATION
from app.errors import AppError
from email_validator import EmailNotValidError, validate_email
from sqlalchemy.exc import IntegrityError

from app.db import constraint_name
from app.models import AttendanceEvent, AttendanceSession, Employee, User
from app.security import hash_password, validate_password
from app.services.attendance import _new_event, lock_employee, open_session_for, point_covered
from app.services.directory import _clean_number, _department
from app.timeutil import ensure_utc, utcnow

BANNED_HEADER_WORDS = (
    "fingerprint",
    "face",
    "template",
    "photo",
    "image",
    "gps",
    "latitude",
    "longitude",
    "biometric_image",
)
REQUIRED_HEADERS = ["employee_number", "event_type", "event_time_utc", "external_event_id", "source"]
EMPLOYEE_HEADERS = [
    "employee_number",
    "full_name",
    "email",
    "temporary_password",
    "designation",
    "department_name",
    "is_test_record",
]


def _decode(raw: bytes) -> str:
    if len(raw) > 1_000_000:
        raise AppError("VALIDATION_ERROR", "The CSV file must be 1 MB or smaller.", 422)
    try:
        return raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise AppError("VALIDATION_ERROR", "The CSV file must be UTF-8.", 422) from exc


def import_physical_csv(
    db: Session,
    *,
    raw: bytes,
    reason: str,
    confirmation: str,
    actor_id: uuid.UUID,
    ip_address: str | None,
) -> dict:
    if confirmation.strip() != IMPORT_CONFIRMATION:
        raise AppError(
            "VALIDATION_ERROR",
            f"Type {IMPORT_CONFIRMATION} to confirm this is an authorized export with no biometric templates.",
            422,
        )
    if len(reason.strip()) < 10:
        raise AppError("VALIDATION_ERROR", "A reason of at least 10 characters is required.", 422)

    text = _decode(raw)
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise AppError("VALIDATION_ERROR", "The CSV file needs a header row.", 422)
    headers = [name.strip() for name in reader.fieldnames]
    for header in headers:
        lowered = header.lower()
        if any(word in lowered for word in BANNED_HEADER_WORDS):
            raise AppError(
                "VALIDATION_ERROR",
                "The file looks like it contains biometric or location data. Remove those columns. This import accepts attendance times only.",
                422,
            )
    missing = [name for name in REQUIRED_HEADERS if name not in headers]
    if missing:
        raise AppError(
            "VALIDATION_ERROR",
            "The CSV is missing required columns: " + ", ".join(missing),
            422,
        )

    parsed: list[dict] = []
    errors: list[dict] = []
    for index, row in enumerate(reader, start=2):
        if index > 2001:
            raise AppError("VALIDATION_ERROR", "The CSV is limited to 2000 data rows.", 422)
        item, error = _parse_row(db, index, row)
        if error:
            errors.append(error)
        else:
            parsed.append(item)
    if errors:
        raise AppError("VALIDATION_ERROR", _error_message(errors), 422)
    if not parsed:
        raise AppError("VALIDATION_ERROR", "The CSV has no attendance rows.", 422)

    batch_id = str(uuid.uuid4())
    server_time = utcnow()
    imported = 0
    flagged = 0
    skipped = 0
    parsed.sort(key=lambda item: (item["employee"].id.hex, item["event_time"]))
    by_employee: dict[uuid.UUID, list[dict]] = {}
    for item in parsed:
        by_employee.setdefault(item["employee"].id, []).append(item)

    for employee_id, items in by_employee.items():
        employee = lock_employee(db, employee_id)
        for item in items:
            existing = db.scalar(
                select(AttendanceEvent.id).where(
                    AttendanceEvent.source == "PHYSICAL_BIOMETRIC",
                    AttendanceEvent.external_event_id == item["external_event_id"],
                )
            )
            if existing is not None:
                skipped += 1
                continue
            outcome = _apply_row(db, employee, item, batch_id, actor_id, server_time)
            if outcome == "RECORDED":
                imported += 1
            else:
                flagged += 1

    write_audit(
        db,
        actor_user_id=actor_id,
        action="ATTENDANCE_IMPORTED",
        entity_type="import_batch",
        entity_id=batch_id,
        ip_address=ip_address,
        details={"imported": imported, "flagged": flagged, "skipped_duplicates": skipped},
    )
    db.commit()
    return {
        "batch_id": batch_id,
        "imported": imported,
        "flagged": flagged,
        "skipped_duplicates": skipped,
        "message": "Rows were stored as physical biometric imports or flagged. Nothing was sent to TimeNex.",
    }


def import_employee_directory(
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
    text = _decode(raw)
    reader = csv.DictReader(io.StringIO(text))
    headers = [name.strip() for name in (reader.fieldnames or [])]
    missing = [name for name in EMPLOYEE_HEADERS if name not in headers]
    if missing:
        raise AppError("VALIDATION_ERROR", "Missing columns: " + ", ".join(missing), 422)
    rows = list(reader)
    if not rows or len(rows) > 500:
        raise AppError("VALIDATION_ERROR", "The file must contain 1 to 500 employee rows.", 422)

    prepared, errors = _prepare_employees(db, rows)
    if errors:
        raise AppError("VALIDATION_ERROR", _error_message(errors), 422)

    now = utcnow()
    imported = 0
    try:
        for item in prepared:
            department = _department(db, item["department_name"])
            user = User(
                email=item["email"],
                display_name=item["full_name"],
                password_hash=hash_password(item["temporary_password"]),
                role="MANAGER",
                is_active=True,
                must_change_password=True,
                created_at=now,
                updated_at=now,
            )
            db.add(user)
            db.flush()
            db.add(
                Employee(
                    user_id=user.id,
                    department_id=department.id if department else None,
                    employee_number=item["employee_number"],
                    full_name=item["full_name"],
                    designation=item["designation"] or None,
                    remote_attendance_enabled=False,
                    is_active=True,
                    is_test_record=item["is_test_record"],
                    created_at=now,
                    updated_at=now,
                )
            )
            db.flush()
            imported += 1
        batch_id = uuid.uuid4().hex
        write_audit(
            db,
            actor_user_id=actor_id,
            action="EMPLOYEES_IMPORTED",
            entity_type="import_batch",
            entity_id=batch_id,
            ip_address=ip_address,
            details={"imported": imported, "reason": reason.strip()},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        name = constraint_name(exc)
        raise AppError("CONFLICT", f"A row conflicts with an existing record ({name}).", 409) from exc
    return {
        "batch_id": batch_id,
        "imported": imported,
        "duplicates": [],
        "message": "New manager accounts were created in this application. Nothing was read from or sent to TimeNex.",
    }


def _prepare_employees(db: Session, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    prepared: list[dict] = []
    errors: list[dict] = []
    seen_numbers: set[str] = set()
    seen_emails: set[str] = set()
    for line, row in enumerate(rows, start=2):
        try:
            item = _employee_row(row)
        except AppError as exc:
            errors.append({"row": line, "message": exc.message})
            continue
        if item["employee_number"] in seen_numbers or item["email"] in seen_emails:
            errors.append({"row": line, "message": "duplicate employee number or email inside this file"})
            continue
        existing_number = db.scalar(select(Employee.id).where(Employee.employee_number == item["employee_number"]))
        existing_email = db.scalar(select(User.id).where(User.email == item["email"]))
        if existing_number is not None or existing_email is not None:
            errors.append({"row": line, "message": "duplicate employee number or email already stored"})
            continue
        seen_numbers.add(item["employee_number"])
        seen_emails.add(item["email"])
        prepared.append(item)
    return prepared, errors


def _employee_row(row: dict) -> dict:
    number = _clean_number(row.get("employee_number") or "")
    full_name = " ".join((row.get("full_name") or "").split())
    email = (row.get("email") or "").strip().lower()
    password = row.get("temporary_password") or ""
    designation = " ".join((row.get("designation") or "").split())
    department_name = row.get("department_name") or ""
    flag = (row.get("is_test_record") or "").strip().lower()
    if len(full_name) < 2 or len(full_name) > 160:
        raise AppError("VALIDATION_ERROR", "full_name must be 2 to 160 characters.", 422)
    if len(designation) > 120:
        raise AppError("VALIDATION_ERROR", "designation is too long.", 422)
    try:
        email = validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise AppError("VALIDATION_ERROR", "email is not valid.", 422) from exc
    validate_password(password, email)
    if flag not in {"true", "false"}:
        raise AppError("VALIDATION_ERROR", "is_test_record must be true or false.", 422)
    return {
        "employee_number": number,
        "full_name": full_name,
        "email": email,
        "temporary_password": password,
        "designation": designation,
        "department_name": department_name,
        "is_test_record": flag == "true",
    }


def _error_message(errors: list[dict]) -> str:
    preview = "; ".join(f"row {item['row']}: {item['message']}" for item in errors[:8])
    extra = len(errors) - 8
    if extra > 0:
        preview += f"; and {extra} more"
    return "The file was not imported. " + preview


def _parse_row(db: Session, line: int, row: dict) -> tuple[dict | None, dict | None]:
    number = (row.get("employee_number") or "").strip()
    event_type = (row.get("event_type") or "").strip().upper()
    raw_time = (row.get("event_time_utc") or "").strip()
    external = (row.get("external_event_id") or "").strip()
    source = (row.get("source") or "").strip().upper()
    if source != "PHYSICAL_BIOMETRIC":
        return None, {"row": line, "message": "source must be PHYSICAL_BIOMETRIC"}
    if event_type not in {"CHECK_IN", "CHECK_OUT"}:
        return None, {"row": line, "message": "event_type must be CHECK_IN or CHECK_OUT"}
    if not external or len(external) > 120:
        return None, {"row": line, "message": "external_event_id is required and must be 120 characters or less"}
    employee = db.scalar(select(Employee).where(Employee.employee_number == number))
    if employee is None:
        return None, {"row": line, "message": f"unknown employee number {number}"}
    try:
        event_time = ensure_utc(datetime.fromisoformat(raw_time.replace("Z", "+00:00")))
    except ValueError:
        return None, {"row": line, "message": "event_time_utc must be a timezone-aware timestamp"}
    return {
        "employee": employee,
        "event_type": event_type,
        "event_time": event_time,
        "external_event_id": external,
    }, None


def _apply_row(
    db: Session,
    employee: Employee,
    item: dict,
    batch_id: str,
    actor_id: uuid.UUID,
    server_time: datetime,
) -> str:
    event_type = item["event_type"]
    event_time = item["event_time"]
    status = "RECORDED"
    reconciliation = "IMPORTED"
    current = open_session_for(db, employee.id, lock=True)
    if event_type == "CHECK_IN":
        if current is not None or point_covered(db, employee.id, event_time):
            status = "FLAGGED"
            reconciliation = "DUPLICATE_OR_OVERLAP"
        else:
            event = _store(db, employee, item, status, reconciliation, batch_id, actor_id, server_time)
            db.flush()
            db.add(
                AttendanceSession(
                    employee_id=employee.id,
                    status="OPEN",
                    check_in_event_id=event.id,
                    opened_at=event_time,
                    created_at=server_time,
                    updated_at=server_time,
                )
            )
            db.flush()
            return status
    else:
        if current is None or event_time <= current.opened_at:
            status = "FLAGGED"
            reconciliation = "NO_OPEN_SESSION"
        else:
            event = _store(db, employee, item, status, reconciliation, batch_id, actor_id, server_time)
            db.flush()
            current.check_out_event_id = event.id
            current.status = "CLOSED"
            current.closed_at = event_time
            current.updated_at = server_time
            db.flush()
            return status
    _store(db, employee, item, status, reconciliation, batch_id, actor_id, server_time)
    db.flush()
    return status


def _store(
    db: Session,
    employee: Employee,
    item: dict,
    status: str,
    reconciliation: str,
    batch_id: str,
    actor_id: uuid.UUID,
    server_time: datetime,
) -> AttendanceEvent:
    event = _new_event(
        employee=employee,
        event_type=item["event_type"],
        source="PHYSICAL_BIOMETRIC",
        server_time=server_time,
        effective_at=item["event_time"],
        client_reported_at=None,
        idempotency_key="imp:" + hashlib.sha256(item["external_event_id"].encode()).hexdigest(),
        created_by=actor_id,
        status=status,
        metadata={
            "is_test_record": employee.is_test_record,
            "import_batch_id": batch_id,
            "reconciliation_status": reconciliation,
        },
        external_event_id=item["external_event_id"],
    )
    db.add(event)
    return event
