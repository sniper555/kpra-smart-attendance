import logging
import time

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import IntegrationConnection
from app.services.sync_queue import process_outbox
from app.timeutil import utcnow

logger = logging.getLogger("kpra.worker")

TIMENEX_OFF = (
    "The KPRA TimeNex tenant is a signed-in web application with CSV backup and a manual "
    "Mark Attendance screen. No API contract was found, so this app sends nothing and marks nothing synced."
)
TIMENEX_BAD = (
    "TIMENEX_INTEGRATION_ENABLED is true, but no supported TimeNex API is implemented. "
    "No request was sent."
)
SENSEFACE_OFF = (
    "ADMS listener is off. The production terminal stays on its current server. "
    "TCP 4370 is the device COMM port in the vendor manual, not the observed ADMS port 80."
)
SENSEFACE_BAD = (
    "SENSEFACE_ADMS_ENABLED is true, but no verified push implementation is included. "
    "The listener was not started."
)


def pulse() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        rows = {row.code: row for row in db.scalars(select(IntegrationConnection)).all()}
        now = utcnow()
        timenex = rows.get("timenex")
        senseface = rows.get("senseface")
        if timenex is not None:
            if settings.timenex_integration_enabled:
                timenex.status = "MISCONFIGURED"
                timenex.summary = TIMENEX_BAD
                timenex.last_error = "No request was sent."
            else:
                timenex.status = "NO_SUPPORTED_INTERFACE"
                timenex.summary = TIMENEX_OFF
                timenex.last_error = None
            timenex.last_heartbeat_at = now
            timenex.updated_at = now
        if senseface is not None:
            if settings.senseface_adms_enabled:
                senseface.status = "MISCONFIGURED"
                senseface.summary = SENSEFACE_BAD
                senseface.last_error = "Listener was not started."
            else:
                senseface.status = "LISTENER_DISABLED"
                senseface.summary = SENSEFACE_OFF
                senseface.last_error = None
            senseface.last_heartbeat_at = now
            senseface.updated_at = now
        process_outbox(db)
        db.commit()
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    if settings.app_env == "production" and settings.seed_dev_data:
        raise SystemExit("Refusing to start: SEED_DEV_DATA is set in production")
    logger.info("Integration worker started. No external sync client is enabled.")
    while True:
        try:
            pulse()
        except Exception:
            logger.exception("worker pulse failed")
        time.sleep(60)


if __name__ == "__main__":
    main()
