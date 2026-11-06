import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import client_ip, load_principal, require_admin
from app.models import CorrectionRequest
from app.presentation import present_correction
from app.schemas import CorrectionCreate, ReviewBody
from app.services.corrections import approve_request, create_request, reject_request

router = APIRouter(tags=["corrections"])


def _ready(request: Request, db: Session):
    return load_principal(request, db, require_ready=True)


@router.get("/api/corrections/mine")
def mine(request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _ready(request, db)
    if principal.employee is None:
        return {"requests": []}
    rows = db.scalars(
        select(CorrectionRequest)
        .where(CorrectionRequest.employee_id == principal.employee.id)
        .order_by(CorrectionRequest.created_at.desc())
    ).all()
    return {"requests": [present_correction(db, row) for row in rows]}


@router.post("/api/corrections")
def create(body: CorrectionCreate, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = _ready(request, db)
    if principal.employee is None:
        from app.errors import AppError

        raise AppError("FORBIDDEN", "No employee profile is linked to this account.", 403)
    row = create_request(
        db,
        employee=principal.employee,
        actor_id=principal.user.id,
        kind=body.kind,
        reason=body.reason,
        requested_start_at=body.requested_start_at,
        requested_end_at=body.requested_end_at,
        ip_address=client_ip(request),
    )
    return present_correction(db, row)


@router.get("/api/admin/corrections")
def admin_list(
    request: Request,
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    principal = require_admin(_ready(request, db))
    stmt = select(CorrectionRequest).order_by(CorrectionRequest.created_at.desc())
    if status:
        stmt = stmt.where(CorrectionRequest.status == status)
    rows = db.scalars(stmt.limit(200)).all()
    return {"requests": [present_correction(db, row) for row in rows], "actor_id": str(principal.user.id)}


@router.post("/api/admin/corrections/{request_id}/approve")
def approve(request_id: uuid.UUID, body: ReviewBody, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = require_admin(_ready(request, db))
    row = approve_request(
        db,
        request_id=request_id,
        reviewer_id=principal.user.id,
        review_reason=body.review_reason,
        ip_address=client_ip(request),
    )
    return present_correction(db, row)


@router.post("/api/admin/corrections/{request_id}/reject")
def reject(request_id: uuid.UUID, body: ReviewBody, request: Request, db: Session = Depends(get_db)) -> dict:
    principal = require_admin(_ready(request, db))
    row = reject_request(
        db,
        request_id=request_id,
        reviewer_id=principal.user.id,
        review_reason=body.review_reason,
        ip_address=client_ip(request),
    )
    return present_correction(db, row)
