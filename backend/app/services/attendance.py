import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db import constraint_name
from app.errors import AppError
from app.constants import DAY_CODES
from app.labels import allow_metadata, classify_remote_source, client_platform, source_label, user_agent_family
from app.models import (
    AttendanceEvent,
    AttendanceOutbox,
    AttendancePolicy,
    AttendanceSession,
    Department,
    Employee,
    TimenexIdentityMapping,
)
from app.services.sync_queue import attach_outbox, plan_submission
from app.timeutil import PKT, ensure_utc, month_bounds, pkt_day_bounds, time_view, utcnow


def load_policy(db: Session) -> AttendancePolicy:
    policy = db.get(AttendancePolicy, 1)
    if policy is None:
        raise AppError("INTERNAL", "Attendance policy is not configured.", 500)
    return policy


def open_session_for(db: Session, employee_id: uuid.UUID, lock: bool = False) -> AttendanceSession | None:
    stmt = select(AttendanceSession).where(
        AttendanceSession.employee_id == employee_id,
        AttendanceSession.status == "OPEN",
    )
    if lock:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def lock_employee(db: Session, employee_id: uuid.UUID) -> Employee:
    employee = db.scalar(select(Employee).where(Employee.id == employee_id).with_for_update())
    if employee is None:
        raise AppError("NOT_FOUND", "Employee record was not found.", 404)
    return employee


def find_by_key(db: Session, key: str) -> AttendanceEvent | None:
    return db.scalar(select(AttendanceEvent).where(AttendanceEvent.idempotency_key == key))


def session_for_event(db: Session, event_id: uuid.UUID) -> AttendanceSession | None:
    return db.scalar(
        select(AttendanceSession).where(
            (AttendanceSession.check_in_event_id == event_id)
            | (AttendanceSession.check_out_event_id == event_id)
        )
    )


def _intervals_overlap(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    return start < other_end and end > other_start


def overlaps_existing(
    db: Session,
    employee_id: uuid.UUID,
    start: datetime,
    end: datetime,
    ignore_session_id: uuid.UUID | None = None,
    ignore_open: bool = False,
) -> bool:
    rows = db.scalars(select(AttendanceSession).where(AttendanceSession.employee_id == employee_id)).all()
    for row in rows:
        if ignore_session_id and row.id == ignore_session_id:
            continue
        if ignore_open and row.status == "OPEN":
            continue
        other_end = row.closed_at or utcnow()
        if _intervals_overlap(start, end, row.opened_at, other_end):
            return True
    return False


def point_covered(db: Session, employee_id: uuid.UUID, moment: datetime) -> bool:
    rows = db.scalars(select(AttendanceSession).where(AttendanceSession.employee_id == employee_id)).all()
    for row in rows:
        if row.closed_at is None:
            if moment >= row.opened_at:
                return True
        elif row.opened_at <= moment < row.closed_at:
            return True
    return False


def _require_remote_allowed(employee: Employee, policy: AttendancePolicy, event_type: str) -> None:
    if not employee.is_active:
        raise AppError("PERMISSION_BLOCKED", "This employee record is inactive.", 403)
    if event_type == "CHECK_OUT":
        return
    if not policy.remote_attendance_approved:
        raise AppError(
            "POLICY_BLOCKED",
            "Remote attendance is turned off until an administrator records KPRA approval in Attendance policy.",
            403,
        )
    if not employee.remote_attendance_enabled:
        raise AppError(
            "PERMISSION_BLOCKED",
            "Remote attendance is not enabled for your account. Ask an administrator.",
            403,
        )
    if policy.enforce_working_days:
        today = DAY_CODES[utcnow().astimezone(PKT).weekday()]
        if today not in set(policy.working_days or []):
            raise AppError(
                "POLICY_BLOCKED",
                "Today is outside the working days configured by an administrator.",
                403,
            )


def _not_future(moment: datetime, label: str) -> datetime:
    moment = ensure_utc(moment)
    if moment > utcnow() + timedelta(seconds=90):
        raise AppError("VALIDATION_ERROR", f"{label} cannot be in the future.", 422)
    return moment


def block_reason(employee: Employee | None, policy: AttendancePolicy, event_type: str) -> str | None:
    if employee is None:
        return (
            "This account has no employee profile, so it cannot mark attendance. "
            "A browser check-in is stored only in this application and is not sent to TimeNex."
        )
    if not employee.is_active:
        return "This employee record is inactive."
    if event_type == "CHECK_OUT":
        return None
    if not policy.remote_attendance_approved:
        return "Remote attendance is turned off until an administrator records KPRA approval."
    if not employee.remote_attendance_enabled:
        return "Remote attendance is not enabled for your account."
    if policy.enforce_working_days:
        today = DAY_CODES[utcnow().astimezone(PKT).weekday()]
        if today not in set(policy.working_days or []):
            return "Today is outside the configured working days."
    return None


def _new_event(
    *,
    employee: Employee,
    event_type: str,
    source: str,
    server_time: datetime,
    effective_at: datetime,
    client_reported_at: datetime | None,
    idempotency_key: str,
    created_by: uuid.UUID | None,
    status: str,
    metadata: dict,
    device_id: uuid.UUID | None = None,
    external_event_id: str | None = None,
    authentication_method: str | None = None,
    timenex_sync_status: str = "NOT_SUBMITTED",
) -> AttendanceEvent:
    return AttendanceEvent(
        employee_id=employee.id,
        event_type=event_type,
        source=source,
        server_received_at_utc=server_time,
        effective_at_utc=effective_at,
        client_reported_at=client_reported_at,
        device_id=device_id,
        external_event_id=external_event_id,
        idempotency_key=idempotency_key,
        status=status,
        created_by=created_by,
        metadata_json=allow_metadata(metadata),
        authentication_method=authentication_method,
        timenex_sync_status=timenex_sync_status,
        created_at=server_time,
    )


def _replay_or_conflict(
    existing: AttendanceEvent, employee_id: uuid.UUID, event_type: str
) -> AttendanceEvent:
    if existing.employee_id != employee_id or existing.event_type != event_type:
        raise AppError(
            "IDEMPOTENCY_CONFLICT",
            "This idempotency key was already used for a different attendance action.",
            409,
        )
    return existing


def _punch_metadata(employee: Employee, source: str, user_agent: str, authentication_method: str) -> dict:
    metadata = {
        "user_agent_family": user_agent_family(source),
        "client_platform": client_platform(user_agent),
        "is_test_record": employee.is_test_record,
    }
    if authentication_method == "WEBAUTHN":
        metadata["user_verification"] = "required"
    return metadata


def punch(
    db: Session,
    *,
    employee: Employee,
    actor_id: uuid.UUID,
    event_type: str,
    user_agent: str,
    sec_ch_ua_mobile: str | None,
    idempotency_key: str,
    client_reported_at: datetime | None,
    ip_address: str | None,
    authentication_method: str = "PASSWORD_MFA",
) -> tuple[AttendanceEvent, AttendanceSession | None, bool]:
    key = idempotency_key.strip()
    employee_id = employee.id
    existing = find_by_key(db, key)
    if existing:
        event = _replay_or_conflict(existing, employee.id, event_type)
        return event, session_for_event(db, event.id), True

    policy = load_policy(db)
    _require_remote_allowed(employee, policy, event_type)
    if policy.passkey_required_for_attendance and authentication_method != "WEBAUTHN":
        raise AppError(
            "PASSKEY_REQUIRED",
            "Attendance needs a passkey with user verification. A password session is not enough. A passkey does not prove where you are.",
            403,
        )
    source = classify_remote_source(user_agent, sec_ch_ua_mobile)
    if client_reported_at is not None:
        try:
            client_reported_at = ensure_utc(client_reported_at)
        except ValueError as exc:
            raise AppError("VALIDATION_ERROR", "Client time must include a timezone.", 422) from exc

    try:
        employee = lock_employee(db, employee.id)
        existing = find_by_key(db, key)
        if existing:
            event = _replay_or_conflict(existing, employee.id, event_type)
            db.commit()
            return event, session_for_event(db, event.id), True

        current = open_session_for(db, employee.id, lock=True)
        server_time = utcnow()
        if authentication_method not in {"PASSWORD_MFA", "WEBAUTHN"}:
            authentication_method = "PASSWORD_MFA"
        sync_status, timenex_employee_id, sync_message = plan_submission(db, employee)
        if event_type == "CHECK_IN":
            if current is not None:
                raise AppError(
                    "DUPLICATE_CHECK_IN",
                    "You already have an open attendance session. Check out before checking in again.",
                    409,
                )
            event = _new_event(
                employee=employee,
                event_type="CHECK_IN",
                source=source,
                server_time=server_time,
                effective_at=server_time,
                client_reported_at=client_reported_at,
                idempotency_key=key,
                created_by=actor_id,
                status="RECORDED",
                metadata=_punch_metadata(employee, source, user_agent, authentication_method),
                authentication_method=authentication_method,
                timenex_sync_status=sync_status,
            )
            db.add(event)
            db.flush()
            session = AttendanceSession(
                employee_id=employee.id,
                status="OPEN",
                check_in_event_id=event.id,
                opened_at=server_time,
                created_at=server_time,
                updated_at=server_time,
            )
            db.add(session)
        else:
            if current is None:
                raise AppError(
                    "NO_OPEN_SESSION",
                    "Check-out needs an open attendance session. There is not one for your account.",
                    409,
                )
            event = _new_event(
                employee=employee,
                event_type="CHECK_OUT",
                source=source,
                server_time=server_time,
                effective_at=server_time,
                client_reported_at=client_reported_at,
                idempotency_key=key,
                created_by=actor_id,
                status="RECORDED",
                metadata=_punch_metadata(employee, source, user_agent, authentication_method),
                authentication_method=authentication_method,
                timenex_sync_status=sync_status,
            )
            db.add(event)
            db.flush()
            current.check_out_event_id = event.id
            current.status = "CLOSED"
            current.closed_at = server_time
            current.updated_at = server_time
            session = current

        attach_outbox(
            db,
            event,
            status=sync_status,
            timenex_employee_id=timenex_employee_id,
            message=sync_message,
        )
        write_audit(
            db,
            actor_user_id=actor_id,
            action=f"ATTENDANCE_{event_type}",
            entity_type="attendance_event",
            entity_id=str(event.id),
            ip_address=ip_address,
            details={
                "source": source,
                "employee_number": employee.employee_number,
                "authentication_method": authentication_method,
                "user_agent_family": user_agent_family(source),
                "client_platform": client_platform(user_agent),
                "user_verification": "required" if authentication_method == "WEBAUTHN" else "not_used",
                "proves_location": False,
                "timenex_sync_status": sync_status,
            },
        )
        db.commit()
        return event, session, False
    except IntegrityError as exc:
        db.rollback()
        name = constraint_name(exc)
        if "idempotency" in name:
            existing = find_by_key(db, key)
            if existing:
                event = _replay_or_conflict(existing, employee_id, event_type)
                return event, session_for_event(db, event.id), True
        if "open_session" in name:
            raise AppError(
                "DUPLICATE_CHECK_IN",
                "You already have an open attendance session. Check out before checking in again.",
                409,
            ) from exc
        raise AppError(
            "CONFLICT",
            "The attendance record could not be saved because it conflicts with an existing one.",
            409,
        ) from exc


def evaluate_late(effective_at: datetime, policy: AttendancePolicy) -> bool | None:
    if not policy.inference_enabled or policy.work_start_local is None or policy.grace_minutes is None:
        return None
    local = effective_at.astimezone(PKT)
    threshold = datetime.combine(local.date(), policy.work_start_local, tzinfo=PKT) + timedelta(
        minutes=policy.grace_minutes
    )
    return local > threshold


def summarize_month(db: Session, employee_id: uuid.UUID | None, year: int, month: int) -> dict:
    from app.timeutil import month_bounds
    from collections import Counter

    start, end = month_bounds(year, month)
    policy = load_policy(db)
    if employee_id is None:
        events = []
    else:
        events = db.scalars(
            select(AttendanceEvent).where(
                AttendanceEvent.employee_id == employee_id,
                AttendanceEvent.status == "RECORDED",
                AttendanceEvent.effective_at_utc >= start,
                AttendanceEvent.effective_at_utc < end,
            )
        ).all()
    check_ins = [event for event in events if event.event_type == "CHECK_IN"]
    check_outs = [event for event in events if event.event_type == "CHECK_OUT"]
    outbox = _outbox_for(db, {event.id for event in check_ins})
    confirmed = sum(1 for event in check_ins if _event_sync(event, outbox)["timenex_confirmed"])
    days = {event.effective_at_utc.astimezone(PKT).date().isoformat() for event in check_ins}
    counts = Counter(event.source for event in events)
    late = None
    early = None
    note = "Late and early status is not calculated. No office timings are stored yet."
    if policy.work_start_local and policy.work_end_local and not policy.inference_enabled:
        note = (
            f"Office timings are stored as {policy.work_start_local.strftime('%H:%M')}–"
            f"{policy.work_end_local.strftime('%H:%M')} PKT. Late and early status stays off."
        )
    if policy.inference_enabled:
        late = sum(1 for event in check_ins if evaluate_late(event.effective_at_utc, policy))
        early = sum(1 for event in check_outs if evaluate_early(event.effective_at_utc, policy))
        note = (
            "Provisional late and early counts use the schedule entered by an administrator. "
            "They are not an official KPRA payroll determination."
        )
    return {
        "month": f"{year:04d}-{month:02d}",
        "check_ins": len(check_ins),
        "check_outs": len(check_outs),
        "days_with_check_in": len(days),
        "timenex_confirmed": confirmed,
        "by_source": [
            {"source": source, "label": source_label(source), "count": count}
            for source, count in sorted(counts.items())
        ],
        "late_check_ins": late,
        "early_check_outs": early,
        "inference_enabled": policy.inference_enabled,
        "schedule_note": note,
    }


def _auth_label(method: str | None) -> str | None:
    if method == "PASSWORD_MFA":
        return "Password and authenticator code"
    if method == "WEBAUTHN":
        return "Passkey on this device"
    return None


def _timenex_label(status: str | None, confirmed: bool) -> str | None:
    if status is None:
        return None
    if confirmed:
        return "Confirmed by TimeNex"
    if status == "NOT_MAPPED":
        return "Saved here · mapping not verified"
    if status == "AWAITING_CONTRACT":
        return "Saved here · TimeNex has not confirmed"
    if status == "MISCONFIGURED":
        return "Saved here · no verified TimeNex contract"
    if status == "FAILED":
        return "TimeNex sync failed"
    return "Saved here · not submitted"


def _event_sync(event: AttendanceEvent | None, outbox_by_event: dict[uuid.UUID, AttendanceOutbox]) -> dict:
    if event is None:
        return {
            "authentication_method": None,
            "authentication_label": None,
            "timenex_sync_status": None,
            "timenex_confirmed": False,
            "timenex_label": None,
        }
    outbox = outbox_by_event.get(event.id)
    status = outbox.status if outbox is not None else event.timenex_sync_status
    reference = outbox.timenex_reference if outbox is not None else event.timenex_reference
    confirmed = status == "CONFIRMED" and bool(reference)
    return {
        "authentication_method": event.authentication_method,
        "authentication_label": _auth_label(event.authentication_method),
        "timenex_sync_status": status,
        "timenex_confirmed": confirmed,
        "timenex_label": _timenex_label(status, confirmed),
    }


def _mapping_fields(row: TimenexIdentityMapping | None) -> dict:
    if row is None:
        return {
            "timenex_mapping_status": "UNMAPPED",
            "timenex_employee_id": None,
            "timenex_mapping_label": "Not mapped",
        }
    names = {"VERIFIED": "Verified", "PENDING": "Pending", "REJECTED": "Rejected"}
    name = names.get(row.verification_status, row.verification_status)
    return {
        "timenex_mapping_status": row.verification_status,
        "timenex_employee_id": row.timenex_employee_id,
        "timenex_mapping_label": f"{name} · {row.timenex_employee_id}",
    }


def _mappings_for(db: Session, employee_ids: set[uuid.UUID]) -> dict[uuid.UUID, TimenexIdentityMapping]:
    if not employee_ids:
        return {}
    rows = db.scalars(
        select(TimenexIdentityMapping).where(TimenexIdentityMapping.employee_id.in_(employee_ids))
    ).all()
    return {row.employee_id: row for row in rows}


def _outbox_for(db: Session, event_ids: set[uuid.UUID]) -> dict[uuid.UUID, AttendanceOutbox]:
    if not event_ids:
        return {}
    rows = db.scalars(select(AttendanceOutbox).where(AttendanceOutbox.event_id.in_(event_ids))).all()
    return {row.event_id: row for row in rows}


def daily_board(db: Session) -> dict:
    today = utcnow().astimezone(PKT)
    start, end = pkt_day_bounds(today)
    employees = db.scalars(
        select(Employee).where(Employee.is_active.is_(True)).order_by(Employee.full_name, Employee.employee_number)
    ).all()
    departments = {row.id: row.name for row in db.scalars(select(Department)).all()}
    open_sessions = {
        row.employee_id: row
        for row in db.scalars(select(AttendanceSession).where(AttendanceSession.status == "OPEN")).all()
    }
    events = db.scalars(
        select(AttendanceEvent)
        .where(
            AttendanceEvent.status == "RECORDED",
            AttendanceEvent.effective_at_utc >= start,
            AttendanceEvent.effective_at_utc < end,
        )
        .order_by(AttendanceEvent.effective_at_utc.asc())
    ).all()
    latest = {event.employee_id: event for event in events}
    check_in_ids = [row.check_in_event_id for row in open_sessions.values()]
    check_ins = {}
    if check_in_ids:
        check_ins = {
            event.id: event
            for event in db.scalars(select(AttendanceEvent).where(AttendanceEvent.id.in_(check_in_ids))).all()
        }
    outbox_by_event = _outbox_for(db, {event.id for event in events} | set(check_ins))
    mappings = _mappings_for(db, {employee.id for employee in employees})
    counts = {"checked_in": 0, "checked_out": 0, "no_record": 0}
    confirmed = 0
    rows = []
    for employee in employees:
        session = open_sessions.get(employee.id)
        event = latest.get(employee.id)
        if session is not None:
            status = "CHECKED_IN"
            counts["checked_in"] += 1
            source_event = event or check_ins.get(session.check_in_event_id)
        elif event is not None and event.event_type == "CHECK_OUT":
            status = "CHECKED_OUT"
            counts["checked_out"] += 1
            source_event = event
        elif event is not None:
            status = "RECORDED"
            source_event = event
        else:
            status = "NO_RECORD"
            counts["no_record"] += 1
            source_event = None
        opened = session.opened_at.astimezone(PKT).date() if session else None
        sync = _event_sync(source_event, outbox_by_event)
        if sync["timenex_confirmed"]:
            confirmed += 1
        rows.append(
            {
                "employee_number": employee.employee_number,
                "employee_name": employee.full_name,
                "department": departments.get(employee.department_id) if employee.department_id else None,
                "is_test_record": employee.is_test_record,
                "status": status,
                "source": source_event.source if source_event else None,
                "source_label": source_label(source_event.source) if source_event else None,
                "last_event_type": source_event.event_type if source_event else None,
                "last_event_at": time_view(source_event.effective_at_utc) if source_event else None,
                "open_since_previous_day": bool(opened and opened < today.date()),
                **sync,
                **_mapping_fields(mappings.get(employee.id)),
            }
        )
    return {
        "date_pkt": today.date().isoformat(),
        "counts": counts,
        "timenex_confirmed": confirmed,
        "rows": rows,
    }


def monthly_board(db: Session, year: int, month: int) -> dict:
    start, end = month_bounds(year, month)
    employees = db.scalars(
        select(Employee).where(Employee.is_active.is_(True)).order_by(Employee.full_name, Employee.employee_number)
    ).all()
    departments = {row.id: row.name for row in db.scalars(select(Department)).all()}
    events = db.scalars(
        select(AttendanceEvent).where(
            AttendanceEvent.status == "RECORDED",
            AttendanceEvent.effective_at_utc >= start,
            AttendanceEvent.effective_at_utc < end,
        )
    ).all()
    grouped: dict[uuid.UUID, list[AttendanceEvent]] = {}
    for event in events:
        grouped.setdefault(event.employee_id, []).append(event)
    outbox_by_event = _outbox_for(db, {event.id for event in events})
    mappings = _mappings_for(db, {employee.id for employee in employees})
    rows = []
    with_check_in = 0
    confirmed_total = 0
    for employee in employees:
        own = grouped.get(employee.id, [])
        check_ins = [event for event in own if event.event_type == "CHECK_IN"]
        check_outs = [event for event in own if event.event_type == "CHECK_OUT"]
        days = {event.effective_at_utc.astimezone(PKT).date().isoformat() for event in check_ins}
        confirmed_days = sum(1 for event in check_ins if _event_sync(event, outbox_by_event)["timenex_confirmed"])
        if days:
            with_check_in += 1
        confirmed_total += confirmed_days
        rows.append(
            {
                "employee_number": employee.employee_number,
                "employee_name": employee.full_name,
                "department": departments.get(employee.department_id) if employee.department_id else None,
                "is_test_record": employee.is_test_record,
                "days_with_check_in": len(days),
                "check_ins": len(check_ins),
                "check_outs": len(check_outs),
                "timenex_confirmed_check_ins": confirmed_days,
                **_mapping_fields(mappings.get(employee.id)),
            }
        )
    return {
        "month": f"{year:04d}-{month:02d}",
        "employees_with_check_in": with_check_in,
        "employees_without_check_in": len(employees) - with_check_in,
        "timenex_confirmed": confirmed_total,
        "rows": rows,
    }


def evaluate_early(effective_at: datetime, policy: AttendancePolicy) -> bool | None:
    if not policy.inference_enabled or policy.work_end_local is None or policy.grace_minutes is None:
        return None
    local = effective_at.astimezone(PKT)
    threshold = datetime.combine(local.date(), policy.work_end_local, tzinfo=PKT) - timedelta(
        minutes=policy.grace_minutes
    )
    return local < threshold
