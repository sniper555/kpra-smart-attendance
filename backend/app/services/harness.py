"""Isolated QA data. Events use TEST_DATA and never leave this database."""

import secrets
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import get_settings
from app.constants import HARNESS_CONFIRMATION
from app.errors import AppError
from app.models import AttendanceSession, Department, Employee, User
from app.security import hash_password
from app.services.attendance import _new_event
from app.services.directory import _department
from app.timeutil import utcnow


def run_harness(
    db: Session,
    *,
    actor_id: uuid.UUID,
    employees: int,
    pairs: int,
    confirmation: str,
    ip_address: str | None,
) -> dict:
    if get_settings().app_env == "production":
        raise AppError("FORBIDDEN", "The QA harness is disabled in production.", 403)
    if confirmation.strip() != HARNESS_CONFIRMATION:
        raise AppError("VALIDATION_ERROR", f"Type {HARNESS_CONFIRMATION} to create isolated test data.", 422)
    if not 1 <= employees <= 20:
        raise AppError("VALIDATION_ERROR", "Choose 1 to 20 synthetic employees.", 422)
    if not 1 <= pairs <= 5:
        raise AppError("VALIDATION_ERROR", "Choose 1 to 5 closed attendance pairs per employee.", 422)

    now = utcnow()
    department = _department(db, "QA Harness")
    created_numbers: list[str] = []
    event_count = 0
    for _ in range(employees):
        employee = _synthetic_employee(db, department, now)
        created_numbers.append(employee.employee_number)
        event_count += _closed_pairs(db, employee, actor_id, pairs, now)
    write_audit(
        db,
        actor_user_id=actor_id,
        action="QA_HARNESS_RUN",
        entity_type="attendance_event",
        entity_id=None,
        ip_address=ip_address,
        details={"employees": employees, "pairs": pairs, "source": "TEST_DATA"},
    )
    db.commit()
    return {
        "source": "TEST_DATA",
        "transmitted": False,
        "employee_numbers": created_numbers,
        "events": event_count,
        "message": "Synthetic rows were stored in this database only. Nothing was sent to TimeNex or a terminal.",
    }


def _synthetic_employee(db: Session, department: Department | None, now) -> Employee:
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"harness-{suffix}@kpra.example",
        display_name=f"QA Employee {suffix}",
        password_hash=hash_password(secrets.token_urlsafe(24) + "A1"),
        role="MANAGER",
        is_active=True,
        must_change_password=True,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.flush()
    number = f"QA-{suffix.upper()}"
    employee = Employee(
        user_id=user.id,
        department_id=department.id if department else None,
        employee_number=number,
        full_name=user.display_name,
        designation="QA harness",
        remote_attendance_enabled=False,
        is_active=True,
        is_test_record=True,
        created_at=now,
        updated_at=now,
    )
    db.add(employee)
    db.flush()
    return employee


def _closed_pairs(db: Session, employee: Employee, actor_id: uuid.UUID, pairs: int, now) -> int:
    count = 0
    for index in range(pairs):
        opened = now - timedelta(days=index + 1, hours=8)
        closed = opened + timedelta(hours=8)
        check_in = _new_event(
            employee=employee,
            event_type="CHECK_IN",
            source="TEST_DATA",
            server_time=now,
            effective_at=opened,
            client_reported_at=None,
            idempotency_key=f"harness:{employee.id}:{index}:in",
            created_by=actor_id,
            status="RECORDED",
            metadata={"is_test_record": True},
            external_event_id=f"QA-{employee.employee_number}-{index}-IN",
        )
        db.add(check_in)
        db.flush()
        check_out = _new_event(
            employee=employee,
            event_type="CHECK_OUT",
            source="TEST_DATA",
            server_time=now,
            effective_at=closed,
            client_reported_at=None,
            idempotency_key=f"harness:{employee.id}:{index}:out",
            created_by=actor_id,
            status="RECORDED",
            metadata={"is_test_record": True},
            external_event_id=f"QA-{employee.employee_number}-{index}-OUT",
        )
        db.add(check_out)
        db.flush()
        db.add(
            AttendanceSession(
                employee_id=employee.id,
                status="CLOSED",
                check_in_event_id=check_in.id,
                check_out_event_id=check_out.id,
                opened_at=opened,
                closed_at=closed,
                created_at=now,
                updated_at=now,
            )
        )
        count += 2
    return count


def harness_employee_count(db: Session) -> int:
    return int(
        db.scalar(select(func.count()).select_from(Employee).where(Employee.designation == "QA harness")) or 0
    )
