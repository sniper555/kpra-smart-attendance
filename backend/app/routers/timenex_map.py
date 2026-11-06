import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import client_ip, load_principal, require_admin
from app.errors import AppError
from app.labels import source_label
from app.models import AttendanceEvent, AttendanceOutbox, Employee, TimenexIdentityMapping
from app.services.timenex_employee_import import import_timenex_employees, preview_timenex_employees
from app.services.timenex_mapping import create_mapping, decide_mapping, import_mapping_csv, present_mapping, set_erp_employee_id
from app.timeutil import time_view

router = APIRouter(prefix="/api/admin/timenex", tags=["timenex"])


class MappingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_number: str = Field(min_length=1, max_length=32)
    timenex_employee_id: str = Field(min_length=1, max_length=32)


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVE", "REJECT"]
    reason: str = Field(min_length=10, max_length=500)


def _admin(request: Request, db: Session):
    return require_admin(load_principal(request, db, require_ready=True))


@router.get("/mappings")
def list_mappings(request: Request, db: Session = Depends(get_db)) -> dict:
    _admin(request, db)
    rows = db.scalars(select(TimenexIdentityMapping).order_by(TimenexIdentityMapping.created_at.desc())).all()
    return {
        "mappings": [present_mapping(db, row) for row in rows],
        "note": "A TimeNex employee ID does not sign anyone in. TimeNex has not confirmed attendance from this application.",
    }


@router.post("/mappings", status_code=201)
def add_mapping(body: MappingBody, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _admin(request, db)
    row = create_mapping(
        db,
        actor_id=principal.user.id,
        employee_number=body.employee_number,
        timenex_employee_id=body.timenex_employee_id,
        ip_address=client_ip(request),
    )
    return present_mapping(db, row)


@router.get("/mappings-template.csv")
def mapping_template(request: Request, db: Session = Depends(get_db)) -> Response:
    _admin(request, db)
    content = "employee_number,timenex_employee_id,full_name,department_name\n"
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="timenex-mapping-template.csv"'},
    )


@router.get("/outbox")
def list_outbox(request: Request, db: Session = Depends(get_db)) -> dict:
    _admin(request, db)
    rows = db.execute(
        select(AttendanceOutbox, AttendanceEvent, Employee)
        .join(AttendanceEvent, AttendanceEvent.id == AttendanceOutbox.event_id)
        .join(Employee, Employee.id == AttendanceEvent.employee_id)
        .order_by(AttendanceOutbox.created_at.desc())
        .limit(100)
    ).all()
    items = []
    confirmed = 0
    attention = 0
    erp_pending = 0
    erp_accepted = 0
    timenex_pending = 0
    for outbox, event, employee in rows:
        accepted = outbox.status == "CONFIRMED" and bool(outbox.timenex_reference)
        erp_ok = outbox.erp_status == "ACCEPTED" and bool(outbox.erp_reference)
        if accepted:
            confirmed += 1
        elif erp_ok:
            timenex_pending += 1
        if outbox.erp_status == "PENDING":
            erp_pending += 1
        elif erp_ok:
            erp_accepted += 1
        elif outbox.erp_status in {"NEEDS_ERP_ID", "NOT_SENT", "FAILED"}:
            attention += 1
        items.append(
            {
                "id": str(outbox.id),
                "event_id": str(event.id),
                "employee_number": employee.employee_number,
                "employee_name": employee.full_name,
                "event_type": event.event_type,
                "source_label": source_label(event.source),
                "timenex_employee_id": outbox.timenex_employee_id,
                "status": outbox.status,
                "status_label": _queue_label(outbox.status, outbox.timenex_reference),
                "erp_status": outbox.erp_status,
                "erp_status_label": _erp_label(outbox.erp_status, outbox.erp_reference),
                "confirmed": accepted,
                "erp_accepted": erp_ok,
                "submitted": False,
                "attempt_count": outbox.attempt_count,
                "reference": outbox.timenex_reference if accepted else None,
                "message": outbox.message,
                "effective_at": time_view(event.effective_at_utc),
            }
        )
    return {
        "items": items,
        "local_recorded": len(items),
        "attention_count": attention,
        "erp_pending_count": erp_pending,
        "erp_accepted_count": erp_accepted,
        "timenex_pending_count": timenex_pending,
        "confirmed_count": confirmed,
        "note": "TimeNex is the attendance destination. A local record is not TimeNex confirmation. ERP acceptance is not TimeNex confirmation. This queue does not call the ERP page or TimeNex.",
    }


def _erp_label(status: str, reference: str | None) -> str:
    if status == "ACCEPTED" and reference:
        return "ERP attendance accepted. TimeNex synchronization pending."
    if status == "PENDING":
        return "ERP submission pending"
    return "Failed / requires administrator attention"


def _queue_label(status: str, reference: str | None) -> str:
    if status == "CONFIRMED" and reference:
        return "Confirmed by TimeNex"
    labels = {
        "NOT_MAPPED": "Saved here. Mapping is not verified. Nothing was sent.",
        "AWAITING_CONTRACT": "Saved here. TimeNex has not confirmed it. No request was sent.",
        "MISCONFIGURED": "Saved here. No verified TimeNex contract is implemented. No request was sent.",
        "FAILED": "Saved here. TimeNex did not accept it.",
        "NOT_SUBMITTED": "Saved here. This record was not queued.",
    }
    return labels.get(status, "Saved here. TimeNex has not confirmed it.")


class ErpIdBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    erp_employee_id: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=10, max_length=500)


@router.post("/mappings/{mapping_id}/erp-id")
def record_erp_id(
    mapping_id: uuid.UUID,
    body: ErpIdBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    principal = _admin(request, db)
    row = set_erp_employee_id(
        db,
        actor_id=principal.user.id,
        mapping_id=mapping_id,
        erp_employee_id=body.erp_employee_id,
        reason=body.reason,
        ip_address=client_ip(request),
    )
    saved = present_mapping(db, row)
    saved["sent"] = False
    saved["timenex_confirmed"] = False
    if row.erp_employee_id == row.timenex_employee_id:
        saved["message"] = (
            "Stored, but this matches the TimeNex ID. The ERP form uses a different internal key. Nothing was sent."
        )
    else:
        saved["message"] = "ERP internal ID stored. Nothing was sent to the ERP or to TimeNex."
    return saved


@router.post("/mappings/{mapping_id}/decision")
def mapping_decision(
    mapping_id: uuid.UUID,
    body: DecisionBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    principal = _admin(request, db)
    row = decide_mapping(
        db,
        actor_id=principal.user.id,
        mapping_id=mapping_id,
        decision=body.decision,
        reason=body.reason,
        ip_address=client_ip(request),
    )
    return present_mapping(db, row)


@router.post("/employees/preview")
async def employee_preview(
    request: Request,
    db: Session = Depends(get_db),
    file: UploadFile = File(),
) -> dict:
    _admin(request, db)
    raw = await file.read()
    if len(raw) > 1_000_000:
        raise AppError("VALIDATION_ERROR", "The file is too large.", 422)
    return preview_timenex_employees(db, raw)


@router.post("/employees/import")
async def employee_import(
    request: Request,
    db: Session = Depends(get_db),
    confirmation: str = Form(),
    reason: str = Form(),
    file: UploadFile = File(),
) -> dict:
    principal = _admin(request, db)
    raw = await file.read()
    if len(raw) > 1_000_000:
        raise AppError("VALIDATION_ERROR", "The file is too large.", 422)
    return import_timenex_employees(
        db,
        raw=raw,
        reason=reason,
        confirmation=confirmation,
        actor_id=principal.user.id,
        ip_address=client_ip(request),
    )


@router.post("/mappings/import")
async def mapping_import(
    request: Request,
    db: Session = Depends(get_db),
    confirmation: str = Form(),
    reason: str = Form(),
    file: UploadFile = File(),
) -> dict:
    principal = _admin(request, db)
    raw = await file.read()
    if len(raw) > 1_000_000:
        raise AppError("VALIDATION_ERROR", "The file is too large.", 422)
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError("VALIDATION_ERROR", "The file must be UTF-8 CSV.", 422) from exc
    return import_mapping_csv(
        db,
        actor_id=principal.user.id,
        content=content,
        confirmation=confirmation,
        reason=reason,
        ip_address=client_ip(request),
    )
