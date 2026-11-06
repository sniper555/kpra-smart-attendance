import uuid

from sqlalchemy.orm import Session

from app.models import AuditLog
from app.timeutil import utcnow

_FORBIDDEN = ("password", "secret", "token", "cookie", "authorization", "recovery", "totp")


def clean_details(details: dict | None) -> dict:
    clean: dict = {}
    for key, value in (details or {}).items():
        lowered = str(key).lower()
        if any(word in lowered for word in _FORBIDDEN):
            continue
        if isinstance(value, str):
            clean[key] = value[:500]
        elif isinstance(value, (bool, int, float)) or value is None:
            clean[key] = value
        elif isinstance(value, list):
            clean[key] = [str(item)[:200] for item in value[:50]]
        else:
            clean[key] = str(value)[:500]
    return clean


def write_audit(
    db: Session,
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    ip_address: str | None,
    details: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            ip_address=(ip_address or "")[:64] or None,
            details=clean_details(details),
            created_at=utcnow(),
        )
    )
