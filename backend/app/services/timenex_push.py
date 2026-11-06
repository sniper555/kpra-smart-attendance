"""Push a finished local day through the TimeNex mark attendance form."""

import uuid
from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import get_settings
from app.db import SessionLocal
from app.errors import AppError
from app.models import AttendanceEvent, AttendanceOutbox, AttendanceSession, Employee, TimenexIdentityMapping
from app.services.sync_queue import verified_mapping
from app.services.timenex_portal import TimenexPortalClient
from app.timeutil import PKT, pkt_day_bounds, utcnow


def queue_checkout_push(db: Session, event: AttendanceEvent) -> bool:
    settings = get_settings()
    if not settings.timenex_portal_enabled or event.event_type != "CHECK_OUT":
        return False
    employee = db.get(Employee, event.employee_id)
    if employee is None:
        return False
    mapping = verified_mapping(db, employee)
    if mapping is None:
        return False
    row = db.scalar(select(AttendanceOutbox).where(AttendanceOutbox.event_id == event.id))
    if row is None:
        return False
    row.next_attempt_at = utcnow().replace(year=utcnow().year + 1)
    row.message = "Saved locally. Syncing to TimeNex…"
    row.updated_at = utcnow()
    event.timenex_sync_status = "AWAITING_CONTRACT"
    event.timenex_error = None
    db.commit()
    return True


def push_attendance(db: Session, event_id: uuid.UUID, *, allow_missing_out: bool) -> dict:
    employee, mapping, check_in, check_out, local_day, time_in, time_out = _resolve(
        db, event_id, allow_missing_out=allow_missing_out
    )
    client = TimenexPortalClient()
    try:
        result = client.mark_day(mapping.timenex_employee_id, local_day, time_in, time_out)
    finally:
        client.close()
    _store_result(db, [check_in, check_out], mapping, result)
    return result


def push_correction(
    db: Session,
    events: list[AttendanceEvent],
    *,
    time_in: time | None = None,
    time_out: time | None = None,
    use_form_out: bool = False,
) -> dict:
    """Replace one TimeNex day with the times the administrator entered."""
    if not events:
        return {"ok": False, "error": "Nothing was saved to send."}
    employee = db.get(Employee, events[0].employee_id)
    if not get_settings().timenex_portal_enabled:
        message = "TimeNex portal sync is turned off."
        _mark_failed(db, events, message)
        return {"ok": False, "error": message}
    mapping = verified_mapping(db, employee) if employee is not None else None
    if mapping is None:
        message = "This account has no verified TimeNex employee ID."
        _mark_failed(db, events, message)
        return {"ok": False, "error": message}
    check_out = next((row for row in reversed(events) if row.event_type == "CHECK_OUT"), None)
    check_in = next((row for row in reversed(events) if row.event_type == "CHECK_IN"), None)
    if check_in is None and check_out is not None:
        session = db.scalar(
            select(AttendanceSession).where(AttendanceSession.check_out_event_id == check_out.id)
        )
        if session is not None:
            check_in = db.get(AttendanceEvent, session.check_in_event_id)
    if check_in is None and time_in is None:
        message = "TimeNex needs a local check-in for that date."
        _mark_failed(db, events, message)
        return {"ok": False, "error": message}
    anchor = check_out or check_in
    local_day = anchor.effective_at_utc.astimezone(PKT).date() if anchor is not None else utcnow().astimezone(PKT).date()
    if time_in is None and check_in is not None:
        time_in = check_in.effective_at_utc.astimezone(PKT).time().replace(microsecond=0)
    if not use_form_out and time_out is None and check_out is not None:
        time_out = check_out.effective_at_utc.astimezone(PKT).time().replace(microsecond=0)
    if time_in is None:
        message = "TimeNex needs a local check-in for that date."
        _mark_failed(db, events, message)
        return {"ok": False, "error": message}
    client = TimenexPortalClient()
    try:
        try:
            result = client.mark_day(mapping.timenex_employee_id, local_day, time_in, time_out)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)[:500], "reference": None, "payload": None, "status_code": None}
    finally:
        client.close()
    _store_result(db, [check_in, check_out], mapping, result)
    return result


def manual_sync_block(db: Session, event_id: uuid.UUID) -> str | None:
    """Return the overwrite warning when a manual sync must be confirmed first."""
    _employee, mapping, check_in, check_out, local_day, time_in, time_out = _resolve(
        db, event_id, allow_missing_out=True
    )
    sentence = (
        f"Force sync this day. Overwrite the TimeNex day {local_day.isoformat()} "
        f"with time in {time_in.strftime('%H:%M:%S')} and time out "
        f"{time_out.strftime('%H:%M:%S') if time_out is not None else 'none'}."
    )
    today = utcnow().astimezone(PKT).date()
    if local_day != today:
        return sentence
    if _already_recorded(check_in) or _already_recorded(check_out):
        return sentence + " TimeNex already has a record for this day."
    client = TimenexPortalClient()
    try:
        try:
            existing = client.current_times(mapping.timenex_employee_id)
        except Exception:
            return sentence + " The existing TimeNex day could not be read, so confirm before replacing it."
    finally:
        client.close()
    if existing.get("time_in") or existing.get("time_out"):
        shown = existing.get("time_in") or existing.get("time_out")
        return sentence + f" TimeNex already has {shown} on this day."
    return None


def run_checkout_push(employee_id: str, event_id: str) -> None:
    db = SessionLocal()
    try:
        push_attendance(db, uuid.UUID(event_id), allow_missing_out=False)
    except Exception as exc:
        event = db.get(AttendanceEvent, uuid.UUID(event_id))
        if event is not None:
            message = str(exc)[:500]
            event.timenex_sync_status = "FAILED"
            event.timenex_error = message
            event.timenex_pushed_at = utcnow()
            row = db.scalar(select(AttendanceOutbox).where(AttendanceOutbox.event_id == event.id))
            if row is not None:
                row.status = "FAILED"
                row.message = message
                row.updated_at = utcnow()
            db.commit()
    finally:
        db.close()


def _resolve(
    db: Session,
    event_id: uuid.UUID,
    *,
    allow_missing_out: bool,
) -> tuple[Employee, TimenexIdentityMapping, AttendanceEvent, AttendanceEvent | None, date, time, time | None]:
    event = db.get(AttendanceEvent, event_id)
    if event is None:
        raise AppError("NOT_FOUND", "That attendance record was not found.", 404)
    employee = db.get(Employee, event.employee_id)
    if employee is None:
        raise AppError("NOT_FOUND", "That attendance record was not found.", 404)
    if not get_settings().timenex_portal_enabled:
        raise AppError("NOT_AVAILABLE", "TimeNex portal sync is turned off.", 409)
    mapping = verified_mapping(db, employee)
    if mapping is None:
        raise AppError("NOT_MAPPED", "This account has no verified TimeNex employee ID.", 409)
    local_day = event.effective_at_utc.astimezone(PKT).date()
    check_in, check_out = _day_pair(db, employee.id, local_day)
    if check_in is None:
        raise AppError("NO_OPEN_SESSION", "TimeNex needs a local check-in for that date.", 409)
    if check_out is None and not allow_missing_out:
        raise AppError("NO_OPEN_SESSION", "Check out locally before sending the day to TimeNex.", 409)
    time_in = check_in.effective_at_utc.astimezone(PKT).time().replace(microsecond=0)
    time_out = check_out.effective_at_utc.astimezone(PKT).time().replace(microsecond=0) if check_out is not None else None
    return employee, mapping, check_in, check_out, local_day, time_in, time_out


def _already_recorded(event: AttendanceEvent | None) -> bool:
    if event is None:
        return False
    return bool(event.timenex_reference) or event.timenex_sync_status == "CONFIRMED"


def _mark_failed(db: Session, events: list[AttendanceEvent], message: str) -> None:
    now = utcnow()
    for event in events:
        event.timenex_sync_status = "FAILED"
        event.timenex_error = message
        event.timenex_pushed_at = now
    db.commit()


def _day_pair(db: Session, employee_id: uuid.UUID, local_day) -> tuple[AttendanceEvent | None, AttendanceEvent | None]:
    start = datetime.combine(local_day, time.min, tzinfo=PKT)
    start_utc, end_utc = pkt_day_bounds(start)
    rows = db.scalars(
        select(AttendanceEvent)
        .where(
            AttendanceEvent.employee_id == employee_id,
            AttendanceEvent.status == "RECORDED",
            AttendanceEvent.effective_at_utc >= start_utc,
            AttendanceEvent.effective_at_utc < end_utc,
        )
        .order_by(AttendanceEvent.effective_at_utc.asc())
    ).all()
    check_in = next((row for row in rows if row.event_type == "CHECK_IN"), None)
    check_out = next((row for row in reversed(rows) if row.event_type == "CHECK_OUT"), None)
    opened = [
        row for row in db.scalars(select(AttendanceSession).where(AttendanceSession.employee_id == employee_id)).all()
        if row.opened_at.astimezone(PKT).date() == local_day
    ]
    if opened:
        current = max(opened, key=lambda row: row.opened_at)
        stored_in = db.get(AttendanceEvent, current.check_in_event_id)
        if stored_in is not None:
            check_in = stored_in
        if current.check_out_event_id is not None:
            stored_out = db.get(AttendanceEvent, current.check_out_event_id)
            if stored_out is not None:
                check_out = stored_out
    if check_in is None:
        session = db.scalar(
            select(AttendanceSession)
            .where(AttendanceSession.employee_id == employee_id, AttendanceSession.status == "CLOSED")
            .order_by(AttendanceSession.closed_at.desc())
        )
        if session is not None and session.closed_at is not None and session.closed_at.astimezone(PKT).date() == local_day:
            check_in = db.get(AttendanceEvent, session.check_in_event_id)
            check_out = db.get(AttendanceEvent, session.check_out_event_id) if session.check_out_event_id else check_out
    return check_in, check_out


def _store_result(db: Session, events: list[AttendanceEvent | None], mapping: TimenexIdentityMapping, result: dict) -> None:
    now = utcnow()
    status = "CONFIRMED" if result.get("ok") else "FAILED"
    error = None if result.get("ok") else (result.get("error") or "TimeNex did not accept the day.")
    reference = result.get("reference")
    for event in events:
        if event is None:
            continue
        event.timenex_sync_status = status
        event.timenex_reference = reference
        event.timenex_error = error
        event.timenex_pushed_at = now
        row = db.scalar(select(AttendanceOutbox).where(AttendanceOutbox.event_id == event.id))
        if row is None:
            continue
        row.status = status
        row.timenex_reference = reference
        row.timenex_employee_id = mapping.timenex_employee_id
        out_open = result.get("ok") and not (result.get("payload") or {}).get("time_out")
        row.message = "In only – Out still open" if out_open else ("Confirmed by TimeNex." if result.get("ok") else error)
        row.attempt_count += 1
        row.updated_at = now
    target = next((event for event in events if event is not None and event.event_type == "CHECK_OUT"), None)
    if target is None:
        target = next((event for event in events if event is not None), None)
    if target is not None:
        write_audit(
            db,
            actor_user_id=target.created_by,
            action="TIMENEX_PORTAL_PUSH",
            entity_type="attendance_event",
            entity_id=str(target.id),
            ip_address=None,
            details={
                "admin_user_id": str(target.created_by) if target.created_by else None,
                "timenex_employee_id": mapping.timenex_employee_id,
                "payload": result.get("payload"),
                "http_status": result.get("status_code"),
                "ok": bool(result.get("ok")),
                "error": error,
            },
        )
    db.commit()
