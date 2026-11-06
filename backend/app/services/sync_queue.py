"""Queue remote attendance for a future verified TimeNex contract.

The queue never marks a row confirmed. The current adapter does not open a connection.
"""

import uuid
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.adapters.erp import ACCEPTED, FAILED, PENDING, ErpAttendanceAdapter
from app.adapters.timenex import TimeNexAdapter
from app.models import AttendanceEvent, AttendanceOutbox, Employee, TimenexIdentityMapping
from app.timeutil import utcnow

NOT_MAPPED_MESSAGE = (
    "Saved in this application. TimeNex was not contacted because this account has no verified TimeNex employee mapping."
)
TEST_RECORD_MESSAGE = "Saved in this application. Test records are not submitted to TimeNex."
LOCAL_MESSAGE = "Saved in this application. TimeNex has not confirmed this attendance."


def mapping_for(db: Session, employee: Employee) -> TimenexIdentityMapping | None:
    return db.scalar(
        select(TimenexIdentityMapping).where(TimenexIdentityMapping.employee_id == employee.id)
    )


def verified_mapping(db: Session, employee: Employee) -> TimenexIdentityMapping | None:
    if not employee.is_active:
        return None
    return db.scalar(
        select(TimenexIdentityMapping).where(
            TimenexIdentityMapping.employee_id == employee.id,
            TimenexIdentityMapping.verification_status == "VERIFIED",
        )
    )


def plan_submission(db: Session, employee: Employee) -> tuple[str, str | None, str]:
    from app.config import get_settings

    mapping = verified_mapping(db, employee)
    if get_settings().timenex_portal_enabled and mapping is not None:
        return "AWAITING_CONTRACT", mapping.timenex_employee_id, (
            "Saved locally. Syncing to TimeNex after check-out."
        )
    if employee.is_test_record:
        return "NOT_SUBMITTED", None, TEST_RECORD_MESSAGE
    mapping = verified_mapping(db, employee)
    if mapping is None:
        return "NOT_MAPPED", None, NOT_MAPPED_MESSAGE
    return "AWAITING_CONTRACT", mapping.timenex_employee_id, (
        "Saved in this application. TimeNex has not confirmed this attendance. "
        "No authorized write interface is configured, so no request was sent."
    )


def attach_outbox(
    db: Session,
    event: AttendanceEvent,
    *,
    status: str,
    timenex_employee_id: str | None,
    message: str,
) -> None:
    if status == "NOT_SUBMITTED":
        return
    employee = db.get(Employee, event.employee_id)
    mapping = verified_mapping(db, employee) if employee is not None else None
    erp_id = mapping.erp_employee_id if mapping is not None else None
    erp = ErpAttendanceAdapter().submit(
        {"erp_employee_id": erp_id, "timenex_employee_id": timenex_employee_id}
    )
    now = utcnow()
    db.add(
        AttendanceOutbox(
            event_id=event.id,
            timenex_employee_id=timenex_employee_id,
            status=status,
            attempt_count=0,
            next_attempt_at=now,
            message=message,
            timenex_reference=None,
            erp_status=erp.status,
            erp_reference=None,
            created_at=now,
            updated_at=now,
        )
    )


def receipt_for(db: Session, event: AttendanceEvent) -> dict:
    row = db.scalar(select(AttendanceOutbox).where(AttendanceOutbox.event_id == event.id))
    if row is None:
        message = TEST_RECORD_MESSAGE if (event.metadata_json or {}).get("is_test_record") else LOCAL_MESSAGE
        return {
            "confirmed": False,
            "submitted": False,
            "status": event.timenex_sync_status,
            "timenex_employee_id": None,
            "reference": None,
            "message": message,
        }
    confirmed = row.status == "CONFIRMED" and bool(row.timenex_reference)
    erp = ErpAttendanceAdapter()._view(row.erp_status, row.erp_reference)
    return {
        "confirmed": confirmed,
        "submitted": False,
        "status": row.status,
        "timenex_employee_id": row.timenex_employee_id,
        "reference": row.timenex_reference if confirmed else None,
        "message": row.message,
        "erp": erp,
    }


def _accept_result(row: AttendanceOutbox, result) -> None:
    claimed = result.confirmed or result.submitted or result.reference or result.status == "CONFIRMED"
    if claimed:
        row.status = "AWAITING_CONTRACT"
        row.timenex_reference = None
        row.message = (
            "A connector claimed TimeNex confirmation without a verified contract. "
            "The claim was discarded. No request was sent."
        )
        return
    row.status = result.status
    row.timenex_reference = None
    row.message = result.message


def apply_erp_outcome(db: Session, row: AttendanceOutbox, outcome: str, reference: str | None = None) -> None:
    """Record an ERP result without treating it as TimeNex confirmation.

    The production adapter never calls this. Tests use it to prove that an
    accepted, rejected, or delayed ERP result still leaves TimeNex unconfirmed.
    """
    now = utcnow()
    row.timenex_reference = None
    row.updated_at = now
    if outcome == "ACCEPTED":
        if not (reference or "").strip():
            raise ValueError("An accepted ERP result needs the ERP reference.")
        row.erp_status = "ACCEPTED"
        row.erp_reference = reference.strip()[:128]
        row.status = "AWAITING_CONTRACT"
        row.message = ACCEPTED
    elif outcome == "FAILED":
        row.erp_status = "FAILED"
        row.erp_reference = None
        row.message = FAILED
    elif outcome == "PENDING":
        row.erp_status = "PENDING"
        row.erp_reference = None
        row.next_attempt_at = now + timedelta(hours=1)
        row.message = PENDING
    else:
        raise ValueError("Unknown ERP outcome.")
    db.commit()


def process_outbox(db: Session, *, limit: int = 20) -> int:
    now = utcnow()
    rows = db.scalars(
        select(AttendanceOutbox)
        .where(
            AttendanceOutbox.status.in_(("NOT_MAPPED", "AWAITING_CONTRACT", "MISCONFIGURED")),
            or_(AttendanceOutbox.next_attempt_at.is_(None), AttendanceOutbox.next_attempt_at <= now),
            AttendanceOutbox.attempt_count < 8,
        )
        .limit(limit)
    ).all()
    adapter = TimeNexAdapter()
    handled = 0
    for row in rows:
        event = db.get(AttendanceEvent, row.event_id)
        if event is None:
            row.status = "FAILED"
            row.message = "The local attendance event is missing. Nothing was sent."
            row.updated_at = now
            continue
        employee = db.get(Employee, event.employee_id)
        mapping = None
        if employee is not None and not employee.is_test_record:
            mapping = verified_mapping(db, employee)
            if mapping is None:
                row.status = "NOT_MAPPED"
                row.timenex_employee_id = None
                row.message = NOT_MAPPED_MESSAGE
                row.attempt_count += 1
                row.next_attempt_at = now + timedelta(hours=6)
                row.updated_at = now
                handled += 1
                continue
            row.timenex_employee_id = mapping.timenex_employee_id
        result = adapter.submit(
            {
                "event_id": str(event.id),
                "event_type": event.event_type,
                "source": event.source,
                "effective_at_utc": event.effective_at_utc.isoformat(),
                "authentication_method": event.authentication_method,
                "timenex_employee_id": row.timenex_employee_id,
            }
        )
        _accept_result(row, result)
        erp = ErpAttendanceAdapter().submit(
            {
                "timenex_employee_id": row.timenex_employee_id,
                "erp_employee_id": mapping.erp_employee_id if mapping is not None else None,
            }
        )
        if erp.submitted or erp.confirmed or erp.reference or erp.status == "ACCEPTED":
            row.erp_status = "NOT_SENT"
            row.erp_reference = None
        elif row.erp_status != "ACCEPTED":
            row.erp_status = erp.status
            row.erp_reference = None
        row.attempt_count += 1
        row.next_attempt_at = now + timedelta(hours=6)
        row.updated_at = now
        handled += 1
    return handled


def latest_for_employee(db: Session, employee_id: uuid.UUID) -> AttendanceOutbox | None:
    return db.scalar(
        select(AttendanceOutbox)
        .join(AttendanceEvent, AttendanceEvent.id == AttendanceOutbox.event_id)
        .where(AttendanceEvent.employee_id == employee_id)
        .order_by(AttendanceOutbox.created_at.desc())
    )
