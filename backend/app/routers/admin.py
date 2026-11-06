import csv
import io
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db import get_db
from app.deps import client_ip, load_principal, query_events, require_admin
from app.errors import AppError
from app.labels import csv_safe, source_label
from app.models import (
    AttendanceEvent,
    AuditLog,
    Department,
    Device,
    Employee,
    EmployeeDeviceMapping,
    IntegrationConnection,
    SyncJob,
    User,
)
from app.presentation import (
    audit_summary,
    present_account,
    present_device,
    present_event,
    present_integration,
    present_policy,
)
from app.schemas import DeviceCreate, DevicePatch, ManagerCreate, ManagerPatch, MappingCreate, PolicyUpdate
from app.services.attendance import daily_board, load_policy, monthly_board
from app.services.corrections import mark_manual
from app.services.timenex_push import push_correction
from app.timeutil import PKT
from app.services.directory import (
    create_account,
    create_device,
    create_mapping,
    delete_mapping,
    reset_mfa,
    set_active,
    update_account,
    update_device,
    update_policy,
)
from app.adapters.senseface import SenseFaceAdapter
from app.services.passkeys import revoke_passkeys
from app.adapters.timenex import TimeNexAdapter
from app.services.harness import run_harness
from app.services.imports import import_employee_directory, import_physical_csv
from app.timeutil import time_view

router = APIRouter(prefix="/api/admin", tags=["admin"])

TEMPLATE = (
    "employee_number,event_type,event_time_utc,external_event_id,source\n"
    "EMP-100,CHECK_IN,2026-11-03T04:00:00+00:00,EXAMPLE-1,PHYSICAL_BIOMETRIC\n"
    "EMP-100,CHECK_OUT,2026-11-03T12:00:00+00:00,EXAMPLE-2,PHYSICAL_BIOMETRIC\n"
)
EMPLOYEE_TEMPLATE = (
    "employee_number,full_name,email,temporary_password,designation,department_name,is_test_record\n"
    "EMP-200,Example Manager,example.manager@kpra.example,Change-Me-Please-1,Manager,Example Department,true\n"
)


class ManualMarkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_number: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=10, max_length=500)
    check_in_at: datetime | None = None
    check_out_at: datetime | None = None
    push_to_timenex: bool = False


class HarnessBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employees: int = Field(ge=1, le=20)
    pairs: int = Field(ge=1, le=5)
    confirmation: str = Field(min_length=1, max_length=80)


class ExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date_from: date
    date_to: date
    employee_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    source: str | None = None
    include_flagged: bool = False


def _admin(request: Request, db: Session):
    return require_admin(load_principal(request, db, require_ready=True))


@router.get("/managers")
def managers(request: Request, db: Session = Depends(get_db)) -> dict:
    _admin(request, db)
    users = db.scalars(select(User).order_by(User.display_name, User.email)).all()
    return {"managers": [present_account(db, user) for user in users]}


@router.post("/managers", status_code=201)
def create_manager(body: ManagerCreate, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _admin(request, db)
    user = create_account(db, body, principal.user.id, client_ip(request))
    return present_account(db, user)


@router.patch("/managers/{user_id}")
def patch_manager(user_id: uuid.UUID, body: ManagerPatch, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _admin(request, db)
    user = update_account(db, user_id, body, principal.user.id, client_ip(request))
    return present_account(db, user)


@router.post("/managers/{user_id}/deactivate")
def deactivate(user_id: uuid.UUID, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _admin(request, db)
    user = set_active(db, user_id, False, principal.user.id, client_ip(request))
    return present_account(db, user)


@router.post("/managers/{user_id}/reactivate")
def reactivate(user_id: uuid.UUID, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _admin(request, db)
    user = set_active(db, user_id, True, principal.user.id, client_ip(request))
    return present_account(db, user)


class PasskeyRevokeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=10, max_length=500)


@router.post("/managers/{user_id}/passkeys/revoke")
def passkey_revoke(user_id: uuid.UUID, body: PasskeyRevokeBody, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _admin(request, db)
    return revoke_passkeys(
        db,
        actor_id=principal.user.id,
        user_id=user_id,
        reason=body.reason,
        ip=client_ip(request),
    )


@router.post("/managers/{user_id}/reset-mfa", status_code=204)
def mfa_reset(user_id: uuid.UUID, request: Request, db: Session = Depends(get_db)) -> Response:
    principal = _admin(request, db)
    reset_mfa(db, user_id, principal.user.id, client_ip(request))
    return Response(status_code=204)


@router.post("/attendance/mark")
def attendance_mark(body: ManualMarkBody, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _admin(request, db)
    employee = db.scalar(select(Employee).where(Employee.employee_number == body.employee_number.strip()))
    if employee is None:
        raise AppError("NOT_FOUND", "No employee with that number is in this application.", 404)
    events = mark_manual(
        db,
        employee=employee,
        actor_id=principal.user.id,
        check_in_at=body.check_in_at,
        check_out_at=body.check_out_at,
        reason=body.reason,
        ip_address=client_ip(request),
    )
    transmitted = False
    confirmed = False
    timenex_error = None
    timenex_push = "skipped"
    message = (
        "Saved in this application as an administrator correction. "
        "It is not a fingerprint and it was not sent to TimeNex."
    )
    if body.push_to_timenex:
        form_in = body.check_in_at.astimezone(PKT).time().replace(microsecond=0) if body.check_in_at else None
        form_out = body.check_out_at.astimezone(PKT).time().replace(microsecond=0) if body.check_out_at else None
        result = push_correction(db, events, time_in=form_in, time_out=form_out, use_form_out=True)
        timenex_error = None if result.get("ok") else result.get("error")
        if result.get("ok"):
            transmitted = True
            confirmed = True
            timenex_push = "confirmed"
            message = "Day replaced in TimeNex"
        else:
            timenex_push = "failed"
            message = timenex_error or "TimeNex did not accept the day."
    department = db.get(Department, employee.department_id) if employee.department_id else None
    fresh = [db.get(AttendanceEvent, event.id) for event in events]
    return {
        "source": "ADMIN_CORRECTION",
        "source_label": source_label("ADMIN_CORRECTION"),
        "transmitted": transmitted,
        "timenex_confirmed": confirmed,
        "timenex_error": timenex_error,
        "timenex_push": timenex_push,
        "message": message,
        "events": [present_event(event, employee, department) for event in fresh if event is not None],
    }


@router.get("/attendance/today")
def attendance_today(request: Request, db: Session = Depends(get_db)) -> dict:
    _admin(request, db)
    return daily_board(db)


@router.get("/attendance/month")
def attendance_month(
    request: Request,
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    db: Session = Depends(get_db),
) -> dict:
    _admin(request, db)
    year_text, month_text = month.split("-", 1)
    year, month_number = int(year_text), int(month_text)
    if not 1 <= month_number <= 12:
        raise AppError("VALIDATION_ERROR", "Month must use YYYY-MM.", 422)
    return monthly_board(db, year, month_number)


@router.get("/attendance")
def attendance(
    request: Request,
    date_from: date = Query(...),
    date_to: date = Query(...),
    employee_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    source: str | None = None,
    include_flagged: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=5000),
    db: Session = Depends(get_db),
) -> dict:
    _admin(request, db)
    rows, total = query_events(
        db,
        date_from=date_from,
        date_to=date_to,
        employee_id=employee_id,
        department_id=department_id,
        source=source,
        include_flagged=include_flagged,
        limit=limit,
        offset=offset,
    )
    return {"total": total, "events": [present_event(event, emp, dept) for event, emp, dept in rows]}


@router.post("/attendance/export")
def export_attendance(body: ExportBody, request: Request, db: Session = Depends(get_db)) -> Response:
    principal = _admin(request, db)
    rows, _total = query_events(
        db,
        date_from=body.date_from,
        date_to=body.date_to,
        employee_id=body.employee_id,
        department_id=body.department_id,
        source=body.source,
        include_flagged=body.include_flagged,
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
            "department",
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
            "client_reported_at_utc",
            "external_event_id",
        ]
    )
    for event, employee, department in rows:
        presented = present_event(event, employee, department)
        writer.writerow(
            [
                csv_safe("TEST RECORD" if presented["is_test_record"] else "OFFICIAL"),
                csv_safe(presented["employee_number"]),
                csv_safe(presented["employee_name"]),
                csv_safe(presented["department"]),
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
                (presented["client_reported_at"] or {}).get("utc", ""),
                csv_safe(presented["external_event_id"]),
            ]
        )
    write_audit(
        db,
        actor_user_id=principal.user.id,
        action="ATTENDANCE_EXPORTED",
        entity_type="attendance_export",
        entity_id=None,
        ip_address=client_ip(request),
        details={"rows": len(rows), "date_from": body.date_from.isoformat(), "date_to": body.date_to.isoformat()},
    )
    db.commit()
    content = "\ufeff" + buffer.getvalue()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="attendance-export.csv"'},
    )


@router.post("/harness/run")
def harness_run(body: HarnessBody, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _admin(request, db)
    return run_harness(
        db,
        actor_id=principal.user.id,
        employees=body.employees,
        pairs=body.pairs,
        confirmation=body.confirmation,
        ip_address=client_ip(request),
    )


@router.get("/integrations/adapters")
def integration_adapters(request: Request, db: Session = Depends(get_db)) -> dict:
    _admin(request, db)
    return {"adapters": [SenseFaceAdapter().status(), TimeNexAdapter().status()]}


@router.get("/imports/employees-template.csv")
def employee_template(request: Request, db: Session = Depends(get_db)) -> Response:
    _admin(request, db)
    return Response(
        content=EMPLOYEE_TEMPLATE,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="employee-import-template.csv"'},
    )


@router.post("/imports/employees")
async def import_employees(
    request: Request,
    file: UploadFile = File(...),
    reason: str = Form(...),
    confirmation: str = Form(...),
    db: Session = Depends(get_db),
) -> dict:
    principal = _admin(request, db)
    raw = await file.read()
    return import_employee_directory(
        db,
        raw=raw,
        reason=reason,
        confirmation=confirmation,
        actor_id=principal.user.id,
        ip_address=client_ip(request),
    )


@router.get("/imports/template.csv")
def template(request: Request, db: Session = Depends(get_db)) -> Response:
    _admin(request, db)
    return Response(
        content=TEMPLATE,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="physical-import-template.csv"'},
    )


@router.post("/imports/attendance")
async def import_csv(
    request: Request,
    file: UploadFile = File(...),
    reason: str = Form(...),
    confirmation: str = Form(...),
    db: Session = Depends(get_db),
) -> dict:
    principal = _admin(request, db)
    raw = await file.read()
    return import_physical_csv(
        db,
        raw=raw,
        reason=reason,
        confirmation=confirmation,
        actor_id=principal.user.id,
        ip_address=client_ip(request),
    )


@router.get("/audit")
def audit(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=5000),
    db: Session = Depends(get_db),
) -> dict:
    _admin(request, db)
    total = db.scalar(select(func.count()).select_from(AuditLog))
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)).all()
    return {
        "total": int(total or 0),
        "entries": [
            {
                "id": str(row.id),
                "action": row.action,
                "summary": audit_summary(row.action, row.details or {}),
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
                "ip_address": row.ip_address,
                "details": row.details,
                "created_at": time_view(row.created_at),
            }
            for row in rows
        ],
    }


@router.get("/policy")
def get_policy(request: Request, db: Session = Depends(get_db)) -> dict:
    _admin(request, db)
    return present_policy(load_policy(db))


@router.patch("/policy")
def patch_policy(body: PolicyUpdate, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _admin(request, db)
    policy = update_policy(db, body, principal.user.id, client_ip(request))
    return present_policy(policy)


@router.get("/devices")
def devices(request: Request, db: Session = Depends(get_db)) -> dict:
    _admin(request, db)
    rows = db.scalars(select(Device).order_by(Device.name)).all()
    payload = []
    for device in rows:
        mappings = db.scalars(
            select(EmployeeDeviceMapping).where(EmployeeDeviceMapping.device_id == device.id)
        ).all()
        payload.append(present_device(device, list(mappings)))
    return {"devices": payload}


@router.post("/devices", status_code=201)
def add_device(body: DeviceCreate, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _admin(request, db)
    device = create_device(db, body, principal.user.id, client_ip(request))
    return present_device(device, [])


@router.patch("/devices/{device_id}")
def patch_device(device_id: uuid.UUID, body: DevicePatch, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _admin(request, db)
    device = update_device(db, device_id, body, principal.user.id, client_ip(request))
    mappings = db.scalars(select(EmployeeDeviceMapping).where(EmployeeDeviceMapping.device_id == device.id)).all()
    return present_device(device, list(mappings))


@router.post("/devices/{device_id}/mappings", status_code=201)
def add_mapping(
    device_id: uuid.UUID, body: MappingCreate, request: Request, db: Session = Depends(get_db)
) -> dict:
    principal = _admin(request, db)
    mapping = create_mapping(db, device_id, body, principal.user.id, client_ip(request))
    return {"id": str(mapping.id), "device_user_pin": mapping.device_user_pin}


@router.delete("/mappings/{mapping_id}", status_code=204)
def remove_mapping(mapping_id: uuid.UUID, request: Request, db: Session = Depends(get_db)) -> Response:
    principal = _admin(request, db)
    delete_mapping(db, mapping_id, principal.user.id, client_ip(request))
    return Response(status_code=204)


@router.get("/integrations")
def integrations(request: Request, db: Session = Depends(get_db)) -> dict:
    _admin(request, db)
    rows = db.scalars(select(IntegrationConnection).order_by(IntegrationConnection.display_name)).all()
    job_count = int(db.scalar(select(func.count()).select_from(SyncJob)) or 0)
    return {
        "integrations": [present_integration(row, job_count) for row in rows],
        "sync_available": False,
        "note": "TimeNex has a web screen and CSV backup, not a verified API. This app does not send attendance there and does not mark records synced.",
    }
