import csv
import io
import uuid
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.erp import ErpAttendanceAdapter
from app.audit import write_audit
from app.config import get_settings
from app.db import get_db
from app.deps import client_ip, load_principal, query_events
from app.errors import AppError
from app.labels import classify_remote_source, csv_safe, source_label
from app.models import AttendanceEvent, Department, Employee, WebAuthnCredential
from app.presentation import present_event, present_session
from app.rate_limit import limiter
from app.schemas import PunchBody
from app.services.attendance import block_reason, load_policy, open_session_for, punch, summarize_month
from app.services.passkeys import attendance_options, verify_attendance
from app.services.sync_queue import latest_for_employee, mapping_for, receipt_for
from app.services.timenex_push import manual_sync_block, push_attendance, queue_checkout_push, run_checkout_push
from app.timeutil import PKT, pkt_day_bounds, time_view, utcnow

router = APIRouter(prefix="/api/attendance", tags=["attendance"])


class OwnExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date_from: date
    date_to: date


class AttendancePasskeyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: uuid.UUID
    credential: dict


def _ready(request: Request, db: Session):
    return load_principal(request, db, require_ready=True)


def _employee_or_error(principal) -> Employee:
    if principal.employee is None:
        raise AppError("FORBIDDEN", "No employee profile is linked to this account.", 403)
    return principal.employee


def _timenex_state(db: Session, employee: Employee | None) -> dict:
    if employee is None:
        return {
            "confirmed": False,
            "submitted": False,
            "mapping_status": "UNMAPPED",
            "status": "NOT_SUBMITTED",
            "message": "This account has no employee profile. Nothing is sent to TimeNex.",
        }
    row = mapping_for(db, employee)
    verified = row if row and row.verification_status == "VERIFIED" and employee.is_active else None
    latest = latest_for_employee(db, employee.id)
    if get_settings().timenex_portal_enabled and verified is not None:
        message = latest.message if latest is not None else "Check-out sends the finished day to TimeNex."
    elif employee.is_test_record:
        message = "Saved in this application. Test records are not submitted to TimeNex."
    elif verified is None:
        message = (
            "TimeNex was not contacted because this account has no verified TimeNex employee mapping."
        )
    elif latest is not None:
        message = latest.message
    else:
        message = "No attendance has been queued. TimeNex has not confirmed a record."
    confirmed = bool(latest and latest.status == "CONFIRMED" and latest.timenex_reference)
    return {
        "confirmed": confirmed,
        "submitted": False,
        "mapping_status": row.verification_status if row else "UNMAPPED",
        "timenex_employee_id": row.timenex_employee_id if row else None,
        "authenticates_by_itself": False,
        "status": latest.status if latest else "NOT_SUBMITTED",
        "reference": latest.timenex_reference if confirmed and latest else None,
        "message": message,
    }


@router.get("/me/state")
def state(request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _ready(request, db)
    employee = principal.employee
    policy = load_policy(db)
    current = open_session_for(db, employee.id) if employee else None
    source = classify_remote_source(request.headers.get("user-agent", ""), request.headers.get("sec-ch-ua-mobile"))
    check_in_block = block_reason(employee, policy, "CHECK_IN")
    check_out_block = block_reason(employee, policy, "CHECK_OUT")
    return {
        "status": "CHECKED_IN" if current else "NOT_CHECKED_IN",
        "open_session": present_session(current),
        "server_time": time_view(utcnow()),
        "source_preview": source,
        "source_label": source_label(source),
        "can_check_in": current is None and check_in_block is None,
        "can_check_out": current is not None and check_out_block is None,
        "check_in_block_reason": check_in_block,
        "check_out_block_reason": check_out_block,
        "is_test_record": bool(employee and employee.is_test_record),
        "authentication_method": principal.session.auth_method or "PASSWORD_MFA",
        "passkey_required_for_attendance": policy.passkey_required_for_attendance,
        "passkey_count": int(
            db.scalar(
                select(func.count()).select_from(WebAuthnCredential).where(WebAuthnCredential.user_id == principal.user.id)
            )
            or 0
        ),
        "passkey_verified": (principal.session.auth_method or "") == "WEBAUTHN",
        "location_checked": False,
        "environment": get_settings().app_env,
        "timenex": _timenex_state(db, employee),
        "erp": ErpAttendanceAdapter().status(),
    }


@router.post("/passkey/options")
def attendance_passkey_options(request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _ready(request, db)
    settings = get_settings()
    limiter.hit(f"attendance-passkey:{principal.user.id}", settings.attendance_rate_limit, settings.attendance_rate_window_seconds)
    return attendance_options(db, principal.user, request.headers.get("origin"))


@router.post("/passkey/verify")
def attendance_passkey_verify(body: AttendancePasskeyBody, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _ready(request, db)
    settings = get_settings()
    limiter.hit(f"attendance-passkey:{principal.user.id}", settings.attendance_rate_limit, settings.attendance_rate_window_seconds)
    return verify_attendance(
        db,
        user=principal.user,
        session=principal.session,
        challenge_id=body.challenge_id,
        credential=body.credential,
        ip=client_ip(request),
        request_origin=request.headers.get("origin"),
    )


@router.post("/check-in")
def check_in(body: PunchBody, request: Request, db: Session = Depends(get_db)) -> dict:
    return _punch(body, request, db, "CHECK_IN")


@router.post("/check-out")
def check_out(body: PunchBody, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)) -> dict:
    result = _punch(body, request, db, "CHECK_OUT")
    if result["replayed"]:
        return result
    event = db.get(AttendanceEvent, uuid.UUID(result["event"]["id"]))
    if event is not None and queue_checkout_push(db, event):
        background.add_task(run_checkout_push, str(event.employee_id), str(event.id))
        result["message"] = "Saved locally. Syncing to TimeNex…"
        result["timenex"]["message"] = result["message"]
    return result


def _punch(body: PunchBody, request: Request, db: Session, event_type: str) -> dict:
    principal = _ready(request, db)
    employee = _employee_or_error(principal)
    settings = get_settings()
    limiter.hit(
        f"attendance:{principal.user.id}",
        settings.attendance_rate_limit,
        settings.attendance_rate_window_seconds,
    )
    event, session, replayed = punch(
        db,
        employee=employee,
        actor_id=principal.user.id,
        event_type=event_type,
        user_agent=request.headers.get("user-agent", ""),
        sec_ch_ua_mobile=request.headers.get("sec-ch-ua-mobile"),
        idempotency_key=str(body.idempotency_key),
        client_reported_at=body.client_reported_at,
        ip_address=client_ip(request),
        authentication_method=principal.session.auth_method or "PASSWORD_MFA",
    )
    department = db.get(Department, employee.department_id) if employee.department_id else None
    timenex = receipt_for(db, event)
    return {
        "replayed": replayed,
        "event": present_event(event, employee, department),
        "session": present_session(session),
        "timenex": timenex,
        "erp": timenex.get("erp") or ErpAttendanceAdapter().status(),
        "message": timenex["message"],
    }


@router.get("/me/today")
def today(request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _ready(request, db)
    if principal.employee is None:
        return {"date_pkt": utcnow().astimezone(PKT).date().isoformat(), "events": []}
    employee = principal.employee
    start, end = pkt_day_bounds(utcnow().astimezone(PKT))
    rows = db.scalars(
        select(AttendanceEvent)
        .where(
            AttendanceEvent.employee_id == employee.id,
            AttendanceEvent.status == "RECORDED",
            AttendanceEvent.effective_at_utc >= start,
            AttendanceEvent.effective_at_utc < end,
        )
        .order_by(AttendanceEvent.effective_at_utc.asc())
    ).all()
    department = db.get(Department, employee.department_id) if employee.department_id else None
    return {
        "date_pkt": utcnow().astimezone(PKT).date().isoformat(),
        "events": [present_event(event, employee, department) for event in rows],
    }


@router.get("/me/summary")
def summary(
    request: Request,
    month: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    principal = _ready(request, db)
    today_pkt = utcnow().astimezone(PKT)
    year, month_number = today_pkt.year, today_pkt.month
    if month:
        try:
            year_text, month_text = month.split("-", 1)
            year, month_number = int(year_text), int(month_text)
            if not 1 <= month_number <= 12:
                raise ValueError
        except ValueError as exc:
            raise AppError("VALIDATION_ERROR", "Month must use YYYY-MM.", 422) from exc
    if principal.employee is None:
        return summarize_month(db, None, year, month_number)
    return summarize_month(db, principal.employee.id, year, month_number)


@router.get("/me/history")
def history(
    request: Request,
    date_from: date = Query(...),
    date_to: date = Query(...),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=5000),
    db: Session = Depends(get_db),
) -> dict:
    principal = _ready(request, db)
    if principal.employee is None:
        return {"total": 0, "events": []}
    employee = principal.employee
    rows, total = query_events(
        db,
        date_from=date_from,
        date_to=date_to,
        restrict_employee_id=employee.id,
        include_flagged=False,
        limit=limit,
        offset=offset,
    )
    return {
        "total": total,
        "events": [present_event(event, emp, dept) for event, emp, dept in rows],
    }


@router.post("/me/export")
def export_own(body: OwnExportBody, request: Request, db: Session = Depends(get_db)) -> Response:
    principal = _ready(request, db)
    employee = principal.employee
    rows = []
    if employee is not None:
        rows, _total = query_events(
            db,
            date_from=body.date_from,
            date_to=body.date_to,
            restrict_employee_id=employee.id,
            include_flagged=False,
            limit=10000,
            offset=0,
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "record_class",
            "employee_number",
            "employee_name",
            "event_id",
            "event_type",
            "source_code",
            "source_label",
            "status",
            "authentication_method",
            "authentication_label",
            "timenex_employee_id",
            "timenex_mapping_label",
            "timenex_sync_status",
            "timenex_label",
            "timenex_reference",
            "effective_at_pkt",
            "server_received_at_pkt",
            "server_received_at_utc",
        ]
    )
    for event, owner, department in rows:
        presented = present_event(event, owner, department)
        writer.writerow(
            [
                csv_safe("TEST RECORD" if presented["is_test_record"] else "OFFICIAL"),
                csv_safe(presented["employee_number"]),
                csv_safe(presented["employee_name"]),
                presented["id"],
                presented["event_type"],
                presented["source"],
                csv_safe(presented["source_label"]),
                presented["status"],
                csv_safe(presented["authentication_method"]),
                csv_safe(presented["authentication_label"]),
                csv_safe(presented["timenex_employee_id"]),
                csv_safe(presented["timenex_mapping_label"]),
                csv_safe(presented["timenex_sync_status"]),
                csv_safe(presented["timenex_label"]),
                csv_safe(presented["timenex_reference"]),
                presented["effective_at"]["pkt_display"],
                presented["server_received_at"]["pkt_display"],
                presented["server_received_at"]["utc"],
            ]
        )
    write_audit(
        db,
        actor_user_id=principal.user.id,
        action="ATTENDANCE_EXPORTED",
        entity_type="attendance_export",
        entity_id=str(employee.id) if employee else None,
        ip_address=client_ip(request),
        details={
            "rows": len(rows),
            "scope": "own",
            "date_from": body.date_from.isoformat(),
            "date_to": body.date_to.isoformat(),
        },
    )
    db.commit()
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="my-attendance.csv"'},
    )


def _own_event(request: Request, db: Session, attendance_id: uuid.UUID) -> AttendanceEvent:
    principal = _ready(request, db)
    event = db.get(AttendanceEvent, attendance_id)
    if event is None:
        raise AppError("NOT_FOUND", "That attendance record was not found.", 404)
    if principal.user.role != "ADMIN" and (principal.employee is None or event.employee_id != principal.employee.id):
        raise AppError("NOT_FOUND", "That attendance record was not found.", 404)
    return event


@router.post("/sync-to-timenex/{attendance_id}")
def sync_to_timenex(
    attendance_id: uuid.UUID,
    request: Request,
    confirm_overwrite: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    event = _own_event(request, db, attendance_id)
    if not confirm_overwrite:
        warning = manual_sync_block(db, event.id)
        if warning:
            raise AppError("FORCE_REQUIRED", warning, 409)
    result = push_attendance(db, event.id, allow_missing_out=True)
    fresh = db.get(AttendanceEvent, event.id)
    employee = db.get(Employee, event.employee_id)
    department = db.get(Department, employee.department_id) if employee and employee.department_id else None
    return {
        "ok": result["ok"],
        "message": (
            "In only – Out still open"
            if result["ok"] and not (result.get("payload") or {}).get("time_out")
            else "Confirmed by TimeNex." if result["ok"] else (result.get("error") or "TimeNex did not accept the day.")
        ),
        "timenex_error": None if result["ok"] else result.get("error"),
        "event": present_event(fresh, employee, department) if fresh is not None else None,
    }


status_router = APIRouter(prefix="/api/timenex", tags=["timenex"])


@status_router.get("/status/{attendance_id}")
def timenex_status(attendance_id: uuid.UUID, request: Request, db: Session = Depends(get_db)) -> dict:
    event = _own_event(request, db, attendance_id)
    employee = db.get(Employee, event.employee_id)
    department = db.get(Department, employee.department_id) if employee and employee.department_id else None
    presented = present_event(event, employee, department)
    return {
        "attendance_id": str(event.id),
        "timenex_status": presented["timenex_sync_status"],
        "timenex_confirmed": presented["timenex_confirmed"],
        "timenex_reference": presented["timenex_reference"],
        "timenex_error": event.timenex_error,
        "timenex_pushed_at": time_view(event.timenex_pushed_at),
        "event": presented,
    }
