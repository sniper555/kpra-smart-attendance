import os

os.environ["APP_ENV"] = "test"
os.environ["APP_SECRET_KEY"] = "test-secret-key-not-for-production-0123456789"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://attendance:local-dev-db-password@127.0.0.1:5432/attendance_test",
)
os.environ["SEED_DEV_DATA"] = "false"
os.environ["COOKIE_SECURE"] = "false"
os.environ["TRUST_PROXY"] = "false"
os.environ["LOGIN_RATE_LIMIT"] = "10000"
os.environ["ATTENDANCE_RATE_LIMIT"] = "10000"
os.environ["TIMENEX_INTEGRATION_ENABLED"] = "false"
os.environ["TIMENEX_PORTAL_ENABLED"] = "false"
os.environ["TIMENEX_USERNAME"] = ""
os.environ["TIMENEX_PASSWORD"] = ""
os.environ["SENSEFACE_ADMS_ENABLED"] = "false"
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = ""
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = ""

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings

get_settings.cache_clear()

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AttendancePolicy  # noqa: E402
from app.rate_limit import limiter  # noqa: E402
from app.timeutil import utcnow  # noqa: E402


def _admin_dsn() -> str:
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg", "postgresql", 1)
    base, _db = url.rsplit("/", 1)
    return base + "/postgres"


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    import psycopg

    admin = psycopg.connect(_admin_dsn(), autocommit=True)
    admin.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = 'attendance_test' AND pid <> pg_backend_pid()"
    )
    admin.execute("DROP DATABASE IF EXISTS attendance_test")
    admin.execute("CREATE DATABASE attendance_test")
    admin.close()
    cfg = Config()
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
def db(migrated_database):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def clean_db(migrated_database):
    session = SessionLocal()
    try:
        session.execute(text("SET lock_timeout = '5s'"))
        session.execute(
            text(
                """
                TRUNCATE TABLE
                    audit_logs,
                    sync_jobs,
                    attendance_outbox,
                    correction_requests,
                    attendance_sessions,
                    attendance_events,
                    timenex_identity_mappings,
                    webauthn_credentials,
                    webauthn_challenges,
                    employee_device_mappings,
                    auth_sessions,
                    login_challenges,
                    user_mfa,
                    employees,
                    users,
                    departments,
                    attendance_policies
                RESTART IDENTITY
                """
            )
        )
        session.execute(
            text(
                """
                INSERT INTO attendance_policies (
                    id, remote_attendance_approved, approved_at, approved_by, working_days,
                    work_start_local, work_end_local, grace_minutes, inference_enabled,
                    enforce_working_days, policy_note, updated_at
                ) VALUES (
                    1, false, NULL, NULL, '[]'::jsonb,
                    NULL, NULL, NULL, false, false,
                    'Remote attendance is off until an administrator records approval.',
                    now()
                )
                """
            )
        )
        policy = session.get(AttendancePolicy, 1)
        policy.remote_attendance_approved = False
        policy.inference_enabled = False
        policy.enforce_working_days = False
        policy.working_days = []
        policy.work_start_local = None
        policy.work_end_local = None
        policy.grace_minutes = None
        policy.approved_at = None
        policy.approved_by = None
        policy.policy_note = "Remote attendance is off until an administrator records approval."
        policy.updated_at = utcnow()
        session.commit()
    finally:
        session.close()
    limiter.reset()
    get_settings().login_rate_limit = 10000
    get_settings().attendance_rate_limit = 10000
    get_settings().timenex_integration_enabled = False
    get_settings().timenex_portal_enabled = False
    get_settings().timenex_base_url = ""
    get_settings().timenex_api_token = ""
    get_settings().timenex_write_mode = "none"
    get_settings().senseface_adms_enabled = False
    yield


@pytest.fixture
def client(clean_db):
    with TestClient(app) as test_client:
        yield test_client

