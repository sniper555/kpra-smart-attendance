import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.errors import AppError
from app.models import AttendanceEvent, AttendanceSession, CorrectionRequest, Employee
from app.services.attendance import (
    _new_event,
    lock_employee,
    open_session_for,
    overlaps_existing,
    point_covered,
)
from app.timeutil import PKT, ensure_utc, utcnow


def _aware(value: datetime | None, label: str, *, allow_same_local_day: bool = False) -> datetime:
    if value is None:
        raise AppError("VALIDATION_ERROR", f"{label} is required.", 422)
    try:
        moment = ensure_utc(value)
    except ValueError as exc:
        raise AppError("VALIDATION_ERROR", f"{label} must include a timezone.", 422) from exc
    now = utcnow()
    if moment > now:
        same_day = allow_same_local_day and moment.astimezone(PKT).date() <= now.astimezone(PKT).date()
        if not same_day:
            raise AppError("VALIDATION_ERROR", f"{label} cannot be in the future.", 422)
    return moment


def create_request(
    db: Session,
    *,
    employee: Employee,
    actor_id: uuid.UUID,
    kind: str,
    reason: str,
    requested_start_at: datetime | None,
    requested_end_at: datetime | None,
    ip_address: str | None,
) -> CorrectionRequest:
    start = end = None
    if kind == "MISSED_CHECK_IN":
        start = _aware(requested_start_at, "Check-in time")
        if open_session_for(db, employee.id) is not None:
            raise AppError(
                "CONFLICT",
                "You already have an open session. Request a missed check-out, or a historical pair that does not overlap it.",
                409,
            )
        if point_covered(db, employee.id, start):
            raise AppError("CONFLICT", "That time overlaps an existing attendance session.", 409)
    elif kind == "MISSED_CHECK_OUT":
        end = _aware(requested_end_at, "Check-out time")
        current = open_session_for(db, employee.id)
        if current is None:
            raise AppError(
                "NO_OPEN_SESSION",
                "A missed check-out needs an open session. Request a historical pair if both times are in the past.",
                409,
            )
        if end <= current.opened_at:
            raise AppError("VALIDATION_ERROR", "Check-out time must be after the open check-in.", 422)
    elif kind == "HISTORICAL_PAIR":
        start = _aware(requested_start_at, "Check-in time")
        end = _aware(requested_end_at, "Check-out time")
        if end <= start:
            raise AppError("VALIDATION_ERROR", "Check-out must be after check-in.", 422)
        if overlaps_existing(db, employee.id, start, end):
            raise AppError("CONFLICT", "That time overlaps an existing attendance session.", 409)
    else:
        raise AppError("VALIDATION_ERROR", "Unknown correction type.", 422)

    row = CorrectionRequest(
        employee_id=employee.id,
        requested_by=actor_id,
        kind=kind,
        reason=reason.strip(),
        requested_start_at=start,
        requested_end_at=end,
        status="PENDING",
        created_at=utcnow(),
    )
    db.add(row)
    db.flush()
    write_audit(
        db,
        actor_user_id=actor_id,
        action="CORRECTION_REQUESTED",
        entity_type="correction_request",
        entity_id=str(row.id),
        ip_address=ip_address,
        details={"kind": kind, "employee_number": employee.employee_number},
    )
    db.commit()
    return row


def _correction_event(
    employee: Employee,
    event_type: str,
    effective_at: datetime,
    actor_id: uuid.UUID,
    request_id: uuid.UUID,
    server_time: datetime,
) -> AttendanceEvent:
    return _new_event(
        employee=employee,
        event_type=event_type,
        source="ADMIN_CORRECTION",
        server_time=server_time,
        effective_at=effective_at,
        client_reported_at=None,
        idempotency_key=f"correction:{request_id}:{event_type}",
        created_by=actor_id,
        status="RECORDED",
        metadata={
            "is_test_record": employee.is_test_record,
            "correction_request_id": str(request_id),
        },
    )


def _park_other_open(db: Session, employee_id: uuid.UUID, keep_id: uuid.UUID | None, server_time: datetime) -> None:
    """An administrator correction may replace one day while another day is still open."""
    other = open_session_for(db, employee_id, lock=True)
    if other is None or other.id == keep_id:
        return
    other.status = "CLOSED"
    if other.closed_at is None:
        other.closed_at = other.opened_at
    other.updated_at = server_time
    db.flush()


def _session_opened_on(db: Session, employee_id: uuid.UUID, local_day) -> AttendanceSession | None:
    rows = db.scalars(select(AttendanceSession).where(AttendanceSession.employee_id == employee_id)).all()
    matches = [row for row in rows if row.opened_at.astimezone(PKT).date() == local_day]
    if not matches:
        return None
    return max(matches, key=lambda row: row.opened_at)


def mark_manual(
    db: Session,
    *,
    employee: Employee,
    actor_id: uuid.UUID,
    check_in_at: datetime | None,
    check_out_at: datetime | None,
    reason: str,
    ip_address: str | None,
) -> list[AttendanceEvent]:
    if len(reason.strip()) < 10:
        raise AppError("VALIDATION_ERROR", "Give a reason of at least 10 characters.", 422)
    start = _aware(check_in_at, "Check-in time", allow_same_local_day=True) if check_in_at else None
    end = _aware(check_out_at, "Check-out time", allow_same_local_day=True) if check_out_at else None
    if start is None and end is None:
        raise AppError("VALIDATION_ERROR", "Enter a check-in time, a check-out time, or both.", 422)
    employee = lock_employee(db, employee.id)
    server_time = utcnow()
    mark_id = uuid.uuid4()
    created: list[AttendanceEvent] = []

    def event(event_type: str, effective_at: datetime) -> AttendanceEvent:
        row = _new_event(
            employee=employee,
            event_type=event_type,
            source="ADMIN_CORRECTION",
            server_time=server_time,
            effective_at=effective_at,
            client_reported_at=None,
            idempotency_key=f"mark:{mark_id}:{event_type}",
            created_by=actor_id,
            status="RECORDED",
            metadata={"is_test_record": employee.is_test_record},
        )
        db.add(row)
        db.flush()
        created.append(row)
        return row

    local_day = (start or end).astimezone(PKT).date() if (start or end) else None
    existing = _session_opened_on(db, employee.id, local_day) if local_day is not None else None
    if existing is not None and (start is not None or end is not None):
        opening = start or existing.opened_at
        if end is not None and end <= opening:
            raise AppError("VALIDATION_ERROR", "Check-out must be after check-in.", 422)
        if end is not None and overlaps_existing(
            db, employee.id, opening, end, ignore_session_id=existing.id, ignore_open=True
        ):
            raise AppError("CONFLICT", "That time overlaps an existing attendance session.", 409)
        if start is not None:
            event("CHECK_IN", start)
            existing.check_in_event_id = created[-1].id
            existing.opened_at = start
        if end is not None:
            event("CHECK_OUT", end)
            existing.check_out_event_id = created[-1].id
            existing.status = "CLOSED"
            existing.closed_at = end
        else:
            _park_other_open(db, employee.id, existing.id, server_time)
            existing.check_out_event_id = None
            existing.status = "OPEN"
            existing.closed_at = None
        existing.updated_at = server_time
        if start is None:
            previous = db.get(AttendanceEvent, existing.check_in_event_id)
            if previous is not None:
                created.insert(0, previous)
    elif start is not None and end is not None:
        if end <= start:
            raise AppError("VALIDATION_ERROR", "Check-out must be after check-in.", 422)
        if overlaps_existing(db, employee.id, start, end, ignore_open=True):
            raise AppError("CONFLICT", "That time overlaps an existing attendance session.", 409)
        check_in = event("CHECK_IN", start)
        check_out = event("CHECK_OUT", end)
        db.add(
            AttendanceSession(
                employee_id=employee.id,
                status="CLOSED",
                check_in_event_id=check_in.id,
                check_out_event_id=check_out.id,
                opened_at=start,
                closed_at=end,
                created_at=server_time,
                updated_at=server_time,
            )
        )
    elif start is not None:
        _park_other_open(db, employee.id, None, server_time)
        if point_covered(db, employee.id, start):
            raise AppError("CONFLICT", "That time overlaps an existing attendance session.", 409)
        check_in = event("CHECK_IN", start)
        db.add(
            AttendanceSession(
                employee_id=employee.id,
                status="OPEN",
                check_in_event_id=check_in.id,
                opened_at=start,
                created_at=server_time,
                updated_at=server_time,
            )
        )
    else:
        current = open_session_for(db, employee.id, lock=True)
        if current is None:
            raise AppError("NO_OPEN_SESSION", "A check-out needs an open session. Enter both times for a past day.", 409)
        if end <= current.opened_at:
            raise AppError("VALIDATION_ERROR", "Check-out time must be after the open check-in.", 422)
        check_out = event("CHECK_OUT", end)
        current.check_out_event_id = check_out.id
        current.status = "CLOSED"
        current.closed_at = end
        current.updated_at = server_time
    write_audit(
        db,
        actor_user_id=actor_id,
        action="ATTENDANCE_MARKED",
        entity_type="employee",
        entity_id=str(employee.id),
        ip_address=ip_address,
        details={
            "employee_number": employee.employee_number,
            "source": "ADMIN_CORRECTION",
            "reason": reason.strip(),
            "transmitted": False,
        },
    )
    db.commit()
    return created


def approve_request(
    db: Session,
    *,
    request_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    review_reason: str,
    ip_address: str | None,
) -> CorrectionRequest:
    row = db.get(CorrectionRequest, request_id)
    if row is None:
        raise AppError("NOT_FOUND", "Correction request was not found.", 404)
    if row.status != "PENDING":
        raise AppError("CONFLICT", "This request has already been reviewed.", 409)
    if row.requested_by == reviewer_id:
        raise AppError("FORBIDDEN", "You cannot approve your own correction request.", 403)

    employee = lock_employee(db, row.employee_id)
    server_time = utcnow()
    check_in_event = None
    check_out_event = None
    session: AttendanceSession | None = None

    if row.kind == "MISSED_CHECK_IN":
        if open_session_for(db, employee.id, lock=True) is not None:
            raise AppError("CONFLICT", "An open session now exists, so this check-in cannot be approved.", 409)
        if point_covered(db, employee.id, row.requested_start_at):
            raise AppError("CONFLICT", "That time overlaps an existing attendance session.", 409)
        check_in_event = _correction_event(
            employee, "CHECK_IN", row.requested_start_at, reviewer_id, row.id, server_time
        )
        db.add(check_in_event)
        db.flush()
        session = AttendanceSession(
            employee_id=employee.id,
            status="OPEN",
            check_in_event_id=check_in_event.id,
            opened_at=row.requested_start_at,
            created_at=server_time,
            updated_at=server_time,
        )
        db.add(session)
        db.flush()
    elif row.kind == "MISSED_CHECK_OUT":
        session = open_session_for(db, employee.id, lock=True)
        if session is None:
            raise AppError("NO_OPEN_SESSION", "The open session is gone, so this check-out cannot be approved.", 409)
        if row.requested_end_at <= session.opened_at:
            raise AppError("VALIDATION_ERROR", "Check-out time must be after the open check-in.", 422)
        check_out_event = _correction_event(
            employee, "CHECK_OUT", row.requested_end_at, reviewer_id, row.id, server_time
        )
        db.add(check_out_event)
        db.flush()
        session.check_out_event_id = check_out_event.id
        session.status = "CLOSED"
        session.closed_at = row.requested_end_at
        session.updated_at = server_time
    elif row.kind == "HISTORICAL_PAIR":
        if overlaps_existing(db, employee.id, row.requested_start_at, row.requested_end_at):
            raise AppError("CONFLICT", "That time overlaps an existing attendance session.", 409)
        check_in_event = _correction_event(
            employee, "CHECK_IN", row.requested_start_at, reviewer_id, row.id, server_time
        )
        check_out_event = _correction_event(
            employee, "CHECK_OUT", row.requested_end_at, reviewer_id, row.id, server_time
        )
        db.add(check_in_event)
        db.add(check_out_event)
        db.flush()
        session = AttendanceSession(
            employee_id=employee.id,
            status="CLOSED",
            check_in_event_id=check_in_event.id,
            check_out_event_id=check_out_event.id,
            opened_at=row.requested_start_at,
            closed_at=row.requested_end_at,
            created_at=server_time,
            updated_at=server_time,
        )
        db.add(session)
        db.flush()
    else:
        raise AppError("VALIDATION_ERROR", "Unknown correction type.", 422)

    row.status = "APPROVED"
    row.review_reason = review_reason.strip()
    row.reviewed_by = reviewer_id
    row.reviewed_at = server_time
    row.resulting_check_in_event_id = check_in_event.id if check_in_event else None
    row.resulting_check_out_event_id = check_out_event.id if check_out_event else None
    row.resulting_session_id = session.id if session else None
    write_audit(
        db,
        actor_user_id=reviewer_id,
        action="CORRECTION_APPROVED",
        entity_type="correction_request",
        entity_id=str(row.id),
        ip_address=ip_address,
        details={"kind": row.kind, "employee_number": employee.employee_number},
    )
    db.commit()
    return row


def reject_request(
    db: Session,
    *,
    request_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    review_reason: str,
    ip_address: str | None,
) -> CorrectionRequest:
    row = db.get(CorrectionRequest, request_id)
    if row is None:
        raise AppError("NOT_FOUND", "Correction request was not found.", 404)
    if row.status != "PENDING":
        raise AppError("CONFLICT", "This request has already been reviewed.", 409)
    row.status = "REJECTED"
    row.review_reason = review_reason.strip()
    row.reviewed_by = reviewer_id
    row.reviewed_at = utcnow()
    write_audit(
        db,
        actor_user_id=reviewer_id,
        action="CORRECTION_REJECTED",
        entity_type="correction_request",
        entity_id=str(row.id),
        ip_address=ip_address,
        details={"kind": row.kind},
    )
    db.commit()
    return row
