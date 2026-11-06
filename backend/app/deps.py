import hmac
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import CSRF_HEADER, SESSION_COOKIE, SOURCE_LABELS
from app.errors import AppError
from app.models import AttendanceEvent, AuthSession, Department, Employee, User, UserMfa
from app.security import hash_token
from app.timeutil import PKT, utcnow


@dataclass
class Principal:
    user: User
    session: AuthSession
    employee: Employee | None


def client_ip(request: Request) -> str:
    if get_settings().trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def load_principal(request: Request, db: Session, *, require_ready: bool) -> Principal:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise AppError("AUTH_REQUIRED", "Sign in to continue.", 401)
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(raw)))
    now = utcnow()
    if row is None or row.revoked_at is not None or row.expires_at < now:
        raise AppError("AUTH_REQUIRED", "Your session expired. Sign in again.", 401)
    if now - row.last_seen_at > timedelta(hours=get_settings().idle_hours):
        row.revoked_at = now
        db.commit()
        raise AppError("AUTH_REQUIRED", "Your session expired. Sign in again.", 401)
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AppError("ACCOUNT_INACTIVE", "This account is deactivated. Contact an administrator.", 403)
    if require_ready:
        mfa = db.get(UserMfa, user.id)
        if user.must_change_password or mfa is None or not mfa.enabled:
            raise AppError("SETUP_INCOMPLETE", "Finish password and MFA setup before continuing.", 403)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or len(sent) != len(row.csrf_token) or not hmac.compare_digest(sent, row.csrf_token):
            raise AppError(
                "CSRF_FAILED",
                "The form token was missing or expired. Refresh the page and try again.",
                403,
            )
    row.last_seen_at = now
    db.commit()
    employee = db.scalar(select(Employee).where(Employee.user_id == user.id))
    return Principal(user=user, session=row, employee=employee)


def require_admin(principal: Principal) -> Principal:
    if principal.user.role != "ADMIN":
        raise AppError("FORBIDDEN", "Administrator access is required.", 403)
    return principal


def parse_dates(date_from: date, date_to: date) -> tuple:
    if date_to < date_from:
        raise AppError("VALIDATION_ERROR", "The end date must be on or after the start date.", 422)
    if (date_to - date_from).days > 366:
        raise AppError("VALIDATION_ERROR", "Choose a range of 366 days or less.", 422)
    from datetime import datetime, time
    from datetime import timezone

    start = datetime.combine(date_from, time.min, tzinfo=PKT).astimezone(timezone.utc)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=PKT).astimezone(timezone.utc)
    return start, end


def query_events(
    db: Session,
    *,
    date_from: date,
    date_to: date,
    employee_id: uuid.UUID | None = None,
    department_id: uuid.UUID | None = None,
    source: str | None = None,
    include_flagged: bool = False,
    restrict_employee_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
):
    start, end = parse_dates(date_from, date_to)
    filters = [AttendanceEvent.effective_at_utc >= start, AttendanceEvent.effective_at_utc < end]
    if not include_flagged:
        filters.append(AttendanceEvent.status == "RECORDED")
    if restrict_employee_id is not None:
        filters.append(AttendanceEvent.employee_id == restrict_employee_id)
    elif employee_id is not None:
        filters.append(AttendanceEvent.employee_id == employee_id)
    if department_id is not None:
        filters.append(Employee.department_id == department_id)
    if source:
        if source not in SOURCE_LABELS:
            raise AppError("VALIDATION_ERROR", "Unknown attendance source.", 422)
        filters.append(AttendanceEvent.source == source)
    joined = select(AttendanceEvent, Employee, Department).join(
        Employee, AttendanceEvent.employee_id == Employee.id
    ).outerjoin(Department, Employee.department_id == Department.id)
    total = db.scalar(
        select(func.count())
        .select_from(AttendanceEvent)
        .join(Employee, AttendanceEvent.employee_id == Employee.id)
        .where(*filters)
    )
    rows = db.execute(
        joined.where(*filters).order_by(AttendanceEvent.effective_at_utc.desc()).limit(limit).offset(offset)
    ).all()
    return rows, int(total or 0)
