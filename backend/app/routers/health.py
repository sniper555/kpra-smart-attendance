from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import IntegrationConnection, SyncJob, User
from app.presentation import present_integration
from app.services.attendance import load_policy
from app.timeutil import time_view, utcnow
from sqlalchemy import func

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    settings = get_settings()
    policy = load_policy(db)
    rows = db.scalars(select(IntegrationConnection).order_by(IntegrationConnection.code)).all()
    job_count = int(db.scalar(select(func.count()).select_from(SyncJob)) or 0)
    payload = {
        "status": "ok",
        "environment": settings.app_env,
        "database": "ok",
        "time_utc": time_view(utcnow())["utc"],
        "time_pkt": time_view(utcnow())["pkt_display"],
        "remote_attendance_policy_approved": policy.remote_attendance_approved,
        "integrations": [present_integration(row, job_count) for row in rows],
    }
    if settings.app_env == "development":
        emails = db.scalars(select(User.email)).all()
        payload["development_accounts"] = sorted(email for email in emails if email.endswith("@kpra.example"))
    return payload
