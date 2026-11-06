import os
import threading
import uuid
from datetime import datetime, time, timezone

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db import SessionLocal
from app.labels import classify_remote_source, source_label
from app.main import app
from app.models import (
    AttendanceEvent,
    AttendanceOutbox,
    AttendanceSession,
    AuditLog,
    Employee,
    IntegrationConnection,
    TimenexIdentityMapping,
    User,
    SyncJob,
    WebAuthnCredential,
)
from app.adapters.senseface import SenseFaceAdapter
from app.adapters.timenex import TimeNexAdapter
from app.errors import AppError
from app.services.attendance import evaluate_early, evaluate_late
from app.services.harness import run_harness
from app.worker import pulse
from tests.factories import PASSWORD, add_user, enable_remote_policy, sign_in


def test_source_labels_never_call_remote_a_biometric_scan():
    assert "fingerprint" not in source_label("REMOTE_WEB").lower()
    assert "face" not in source_label("REMOTE_WEB").lower()
    assert classify_remote_source("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", None) == "REMOTE_MOBILE"
    assert classify_remote_source("Mozilla/5.0 (Windows NT 10.0)", None) == "REMOTE_WEB"
    assert classify_remote_source("Mozilla/5.0", "?1") == "REMOTE_MOBILE"
    assert "fingerprint" not in source_label("TEST_DATA").lower()
    assert "biometric" not in source_label("TEST_DATA").lower()


def test_late_and_early_stay_off_until_configured():
    policy = type("P", (), {})()
    policy.inference_enabled = False
    policy.work_start_local = time(9, 0)
    policy.work_end_local = time(17, 0)
    policy.grace_minutes = 10
    moment = datetime(2026, 11, 3, 4, 30, tzinfo=timezone.utc)
    assert evaluate_late(moment, policy) is None
    policy.inference_enabled = True
    assert evaluate_late(moment, policy) is True
    on_time = datetime(2026, 11, 3, 4, 0, tzinfo=timezone.utc)
    assert evaluate_late(on_time, policy) is False
    early = datetime(2026, 11, 3, 11, 0, tzinfo=timezone.utc)
    assert evaluate_early(early, policy) is True


def test_login_failure_is_generic_and_mfa_enrollment_works(client: TestClient):
    add_user(email="new.manager@kpra.example", secret=None, employee_number="M-100", must_change=False)
    bad = client.post("/api/auth/login", json={"email": "missing@kpra.example", "password": PASSWORD})
    assert bad.status_code == 401
    assert bad.json()["error"]["message"] == "Email or password is incorrect."
    started = client.post(
        "/api/auth/login",
        json={"email": "new.manager@kpra.example", "password": PASSWORD},
    )
    assert started.json()["status"] == "enrollment_required"
    enroll = client.post("/api/auth/mfa/enroll/start")
    assert enroll.status_code == 200
    secret = enroll.json()["secret"]
    assert enroll.json()["qr_svg_base64"]
    confirmed = client.post("/api/auth/mfa/enroll/confirm", json={"code": pyotp.TOTP(secret).now()})
    assert confirmed.status_code == 200, confirmed.text
    codes = confirmed.json()["recovery_codes"]
    assert len(codes) == 10
    csrf = confirmed.json()["csrf_token"]
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["employee"]["employee_number"] == "M-100"
    logout = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    again = client.post(
        "/api/auth/login",
        json={"email": "new.manager@kpra.example", "password": PASSWORD},
    )
    assert again.json()["status"] == "mfa_required"
    recovered = client.post("/api/auth/mfa/verify", json={"code": codes[0]})
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["used_backup_code"] is True


def test_password_change_is_required_before_mfa(client: TestClient):
    add_user(email="admin@kpra.example", role="ADMIN", must_change=True)
    response = client.post("/api/auth/login", json={"email": "admin@kpra.example", "password": PASSWORD})
    assert response.json()["status"] == "password_change_required"
    changed = client.post("/api/auth/password", json={"new_password": "Replacement-Password-9"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["status"] == "mfa_required"
    old = client.post("/api/auth/login", json={"email": "admin@kpra.example", "password": PASSWORD})
    assert old.status_code == 401


def test_manager_check_in_and_check_out_use_server_time(client: TestClient):
    add_user(email="manager@kpra.example", employee_number="KPRA-42")
    enable_remote_policy()
    csrf = sign_in(client, "manager@kpra.example")
    blocked = client.post(
        "/api/attendance/check-in",
        json={"idempotency_key": str(uuid.uuid4()), "employee_id": str(uuid.uuid4())},
        headers={"X-CSRF-Token": csrf},
    )
    assert blocked.status_code == 422
    first_key = str(uuid.uuid4())
    created = client.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": csrf, "User-Agent": "Mozilla/5.0"},
        json={"idempotency_key": first_key, "client_reported_at": "1999-01-01T00:00:00+00:00"},
    )
    assert created.status_code == 200, created.text
    event = created.json()["event"]
    assert event["source"] == "REMOTE_WEB"
    assert "web browser" in event["source_label"]
    assert "fingerprint" not in event["source_label"].lower()
    assert event["effective_at"]["utc"] == event["server_received_at"]["utc"]
    assert event["client_reported_at"]["utc"].startswith("1999-")
    assert not event["effective_at"]["utc"].startswith("1999-")
    assert "PKT" in event["server_received_at"]["pkt_display"]
    replay = client.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": first_key},
    )
    assert replay.json()["replayed"] is True
    assert replay.json()["event"]["id"] == event["id"]
    duplicate = client.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": str(uuid.uuid4())},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "DUPLICATE_CHECK_IN"
    mobile = TestClient(app)
    mobile.cookies.set("kpra_session", client.cookies.get("kpra_session"))
    checked_out = mobile.post(
        "/api/attendance/check-out",
        headers={
            "X-CSRF-Token": csrf,
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel)",
        },
        json={"idempotency_key": str(uuid.uuid4())},
    )
    assert checked_out.status_code == 200, checked_out.text
    assert checked_out.json()["event"]["source"] == "REMOTE_MOBILE"
    missing = client.post(
        "/api/attendance/check-out",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": str(uuid.uuid4())},
    )
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "NO_OPEN_SESSION"
    summary = client.get("/api/attendance/me/summary")
    assert summary.json()["late_check_ins"] is None
    assert summary.json()["check_ins"] == 1
    assert summary.json()["timenex_confirmed"] == 0


def test_concurrent_check_in_allows_one_open_session(client: TestClient):
    add_user(email="race@kpra.example", employee_number="RACE-1")
    enable_remote_policy()
    csrf = sign_in(client, "race@kpra.example")
    cookie = client.cookies.get("kpra_session")
    barrier = threading.Barrier(2)
    results = []

    def once():
        local = TestClient(app)
        local.cookies.set("kpra_session", cookie)
        barrier.wait(timeout=5)
        response = local.post(
            "/api/attendance/check-in",
            headers={"X-CSRF-Token": csrf},
            json={"idempotency_key": str(uuid.uuid4())},
        )
        results.append(response.status_code)

    threads = [threading.Thread(target=once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [200, 409]
    session = SessionLocal()
    try:
        opens = session.scalar(
            select(func.count()).select_from(AttendanceSession).where(AttendanceSession.status == "OPEN")
        )
        assert opens == 1
    finally:
        session.close()


def test_csrf_and_cross_employee_access(client: TestClient):
    add_user(email="one@kpra.example", employee_number="ONE-1", full_name="First Manager")
    add_user(email="two@kpra.example", employee_number="TWO-2", full_name="Second Manager")
    enable_remote_policy()
    csrf = sign_in(client, "one@kpra.example")
    missing = client.post("/api/attendance/check-in", json={"idempotency_key": str(uuid.uuid4())})
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "CSRF_FAILED"
    client.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": str(uuid.uuid4())},
    )
    other = TestClient(app)
    other_csrf = sign_in(other, "two@kpra.example")
    history = other.get("/api/attendance/me/history", params={"date_from": "2026-01-01", "date_to": "2026-12-31"})
    assert history.status_code == 200
    assert history.json()["events"] == []
    forbidden = other.get("/api/admin/managers", headers={"X-CSRF-Token": other_csrf})
    assert forbidden.status_code == 403


def test_admin_correction_keeps_history_and_export_labels(client: TestClient):
    add_user(email="manager@kpra.example", employee_number="KPRA-7", full_name='=HYPERLINK("x")')
    add_user(email="admin@kpra.example", role="ADMIN")
    enable_remote_policy()
    manager_csrf = sign_in(client, "manager@kpra.example")
    client.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": manager_csrf},
        json={"idempotency_key": str(uuid.uuid4())},
    )
    short = client.post(
        "/api/corrections",
        headers={"X-CSRF-Token": manager_csrf},
        json={"kind": "MISSED_CHECK_OUT", "reason": "short", "requested_end_at": datetime.now(timezone.utc).isoformat()},
    )
    assert short.status_code == 422
    created = client.post(
        "/api/corrections",
        headers={"X-CSRF-Token": manager_csrf},
        json={
            "kind": "MISSED_CHECK_OUT",
            "reason": "Left the office and the browser was closed.",
            "requested_end_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["kind_label"] == "Missed check-out"
    assert created.json()["status_label"] == "Waiting for an administrator"
    assert created.json()["timenex_mapping_label"] == "Not mapped"
    assert created.json()["timenex_confirmed"] is False
    assert "not sent to TimeNex" in created.json()["timenex_note"]
    request_id = created.json()["id"]
    admin = TestClient(app)
    admin_csrf = sign_in(admin, "admin@kpra.example")
    denied = client.post(
        f"/api/admin/corrections/{request_id}/approve",
        headers={"X-CSRF-Token": manager_csrf},
        json={"review_reason": "Manager cannot approve this request."},
    )
    assert denied.status_code == 403
    approved = admin.post(
        f"/api/admin/corrections/{request_id}/approve",
        headers={"X-CSRF-Token": admin_csrf},
        json={"review_reason": "Confirmed with the manager the same afternoon."},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    assert approved.json()["timenex_confirmed"] is False
    assert "administrator correction" in approved.json()["status_label"].lower()
    assert "fingerprint" not in approved.text.lower()
    state = client.get("/api/attendance/me/state")
    assert state.json()["status"] == "NOT_CHECKED_IN"
    session = SessionLocal()
    try:
        with pytest.raises(DBAPIError):
            session.execute(text("DELETE FROM attendance_events"))
            session.commit()
        session.rollback()
        with pytest.raises(DBAPIError):
            session.execute(text("UPDATE audit_logs SET action = 'TAMPER'"))
            session.commit()
        session.rollback()
        remaining = session.scalar(select(func.count()).select_from(AttendanceEvent))
        assert remaining == 2
    finally:
        session.close()
    export = admin.post(
        "/api/admin/attendance/export",
        headers={"X-CSRF-Token": admin_csrf},
        json={"date_from": "2026-01-01", "date_to": "2026-12-31"},
    )
    assert export.status_code == 200, export.text
    body = export.text
    assert "Remote attendance · web browser" in body
    assert "Administrator correction" in body
    assert "fingerprint" not in body.lower()
    assert "TEST RECORD" in body
    assert "Not mapped" in body
    assert "Saved here" in body
    assert "Confirmed by TimeNex" not in body
    assert "'=HYPERLINK" in body
    manager_export = client.post(
        "/api/admin/attendance/export",
        headers={"X-CSRF-Token": manager_csrf},
        json={"date_from": "2026-01-01", "date_to": "2026-12-31"},
    )
    assert manager_export.status_code == 403


def test_admin_daily_board_labels_real_sources(client: TestClient):
    enable_remote_policy()
    add_user(email="board.admin@kpra.example", role="ADMIN")
    add_user(email="board.present@kpra.example", employee_number="BOARD-1", full_name="Board Present")
    add_user(email="board.quiet@kpra.example", employee_number="BOARD-2", full_name="Board Quiet")
    present = TestClient(app)
    present_csrf = sign_in(present, "board.present@kpra.example")
    punched = present.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": present_csrf},
        json={"idempotency_key": str(uuid.uuid4()), "client_reported_at": None},
    )
    assert punched.status_code == 200, punched.text
    denied = present.get("/api/admin/attendance/today")
    assert denied.status_code == 403
    admin_csrf = sign_in(client, "board.admin@kpra.example")
    assert admin_csrf
    board = client.get("/api/admin/attendance/today")
    assert board.status_code == 200, board.text
    rows = {row["employee_number"]: row for row in board.json()["rows"]}
    assert rows["BOARD-1"]["status"] == "CHECKED_IN"
    assert rows["BOARD-1"]["source"] == "REMOTE_WEB"
    assert rows["BOARD-1"]["authentication_method"] == "PASSWORD_MFA"
    assert rows["BOARD-1"]["timenex_confirmed"] is False
    assert "fingerprint" not in rows["BOARD-1"]["source_label"].lower()
    assert "fingerprint" not in (rows["BOARD-1"]["authentication_label"] or "").lower()
    assert rows["BOARD-1"]["timenex_mapping_status"] == "UNMAPPED"
    assert rows["BOARD-1"]["timenex_mapping_label"] == "Not mapped"
    assert rows["BOARD-2"]["status"] == "NO_RECORD"
    assert board.json()["timenex_confirmed"] == 0
    assert board.json()["counts"]["checked_in"] == 1
    assert board.json()["counts"]["no_record"] == 1


def test_manager_exports_only_own_attendance(client: TestClient):
    enable_remote_policy()
    add_user(email="owner@kpra.example", employee_number="OWN-1")
    add_user(email="other@kpra.example", employee_number="OWN-2", full_name="Other Manager")
    csrf = sign_in(client, "owner@kpra.example")
    punch = client.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": str(uuid.uuid4()), "client_reported_at": None},
    )
    assert punch.status_code == 200, punch.text
    exported = client.post(
        "/api/attendance/me/export",
        headers={"X-CSRF-Token": csrf},
        json={"date_from": "2026-01-01", "date_to": "2026-12-31"},
    )
    assert exported.status_code == 200, exported.text
    assert "OWN-1" in exported.text
    assert "OWN-2" not in exported.text
    assert "Remote attendance · web browser" in exported.text
    assert "fingerprint" not in exported.text.lower()
    assert "timenex_mapping_label" in exported.text
    assert "Not mapped" in exported.text
    assert "Saved here" in exported.text
    history = client.get("/api/attendance/me/history?date_from=2026-01-01&date_to=2026-12-31")
    assert history.status_code == 200, history.text
    own_event = history.json()["events"][0]
    assert own_event["employee_number"] == "OWN-1"
    assert own_event["timenex_mapping_label"] == "Not mapped"
    assert own_event["timenex_confirmed"] is False
    assert own_event["timenex_label"].startswith("Saved here")


def test_admin_monthly_sheet_counts_check_in_days(client: TestClient):
    enable_remote_policy()
    add_user(email="month.admin@kpra.example", role="ADMIN")
    add_user(email="month.present@kpra.example", employee_number="MONTH-1", full_name="Month Present")
    add_user(email="month.quiet@kpra.example", employee_number="MONTH-2", full_name="Month Quiet")
    present = TestClient(app)
    present_csrf = sign_in(present, "month.present@kpra.example")
    punched = present.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": present_csrf},
        json={"idempotency_key": str(uuid.uuid4()), "client_reported_at": None},
    )
    assert punched.status_code == 200, punched.text
    sign_in(client, "month.admin@kpra.example")
    denied = present.get("/api/admin/attendance/month?month=2026-11")
    assert denied.status_code == 403
    sheet = client.get("/api/admin/attendance/month?month=2026-11")
    assert sheet.status_code == 200, sheet.text
    rows = {row["employee_number"]: row for row in sheet.json()["rows"]}
    assert rows["MONTH-1"]["days_with_check_in"] == 1
    assert rows["MONTH-1"]["check_ins"] == 1
    assert rows["MONTH-1"]["timenex_confirmed_check_ins"] == 0
    assert rows["MONTH-1"]["timenex_mapping_status"] == "UNMAPPED"
    assert rows["MONTH-1"]["timenex_mapping_label"] == "Not mapped"
    assert rows["MONTH-2"]["days_with_check_in"] == 0
    assert sheet.json()["timenex_confirmed"] == 0
    assert sheet.json()["employees_with_check_in"] == 1
    assert sheet.json()["employees_without_check_in"] == 1
    assert "absent" not in sheet.text.lower()


def test_admin_mark_is_a_correction_not_a_fingerprint(client: TestClient):
    add_user(email="mark.admin@kpra.example", role="ADMIN")
    add_user(email="mark.person@kpra.example", employee_number="MARK-1", full_name="Mark Person")
    csrf = sign_in(client, "mark.admin@kpra.example")
    person = TestClient(app)
    person_csrf = sign_in(person, "mark.person@kpra.example")
    denied = person.post(
        "/api/admin/attendance/mark",
        headers={"X-CSRF-Token": person_csrf},
        json={
            "employee_number": "MARK-1",
            "reason": "Manual day entered by an administrator.",
            "check_in_at": "2026-11-02T04:00:00+00:00",
            "check_out_at": "2026-11-02T12:00:00+00:00",
        },
    )
    assert denied.status_code == 403
    marked = client.post(
        "/api/admin/attendance/mark",
        headers={"X-CSRF-Token": csrf},
        json={
            "employee_number": "MARK-1",
            "reason": "Manual day entered by an administrator.",
            "check_in_at": "2026-11-02T04:00:00+00:00",
            "check_out_at": "2026-11-02T12:00:00+00:00",
        },
    )
    assert marked.status_code == 200, marked.text
    body = marked.json()
    assert body["transmitted"] is False
    assert body["timenex_confirmed"] is False
    assert body["source"] == "ADMIN_CORRECTION"
    assert "fingerprint" not in body["source_label"].lower()
    assert "Confirmed by TimeNex" not in marked.text
    assert {event["event_type"] for event in body["events"]} == {"CHECK_IN", "CHECK_OUT"}
    assert {event["timenex_mapping_label"] for event in body["events"]} == {"Not mapped"}
    assert all(event["timenex_confirmed"] is False for event in body["events"])
    assert all(event["source"] == "ADMIN_CORRECTION" for event in body["events"])


def test_office_timings_do_not_turn_on_late_status(client: TestClient):
    add_user(email="timing.admin@kpra.example", role="ADMIN")
    csrf = sign_in(client, "timing.admin@kpra.example")
    saved = client.patch(
        "/api/admin/policy",
        headers={"X-CSRF-Token": csrf},
        json={
            "working_days": ["FRI", "MON", "WED"],
            "work_start_local": "09:00",
            "work_end_local": "17:00",
            "grace_minutes": 15,
            "enforce_working_days": False,
            "inference_enabled": False,
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["working_days"] == ["MON", "WED", "FRI"]
    assert body["work_start_local"] == "09:00"
    assert body["work_end_local"] == "17:00"
    assert body["grace_minutes"] == 15
    assert body["inference_enabled"] is False
    assert body["enforce_working_days"] is False
    assert body["remote_attendance_approved"] is False


def test_admin_manages_accounts_and_policy_blocks_remote_until_approved(client: TestClient):
    add_user(email="admin@kpra.example", role="ADMIN")
    add_user(email="waiting@kpra.example", employee_number="WAIT-1")
    csrf = sign_in(client, "admin@kpra.example")
    waiting = TestClient(app)
    waiting_csrf = sign_in(waiting, "waiting@kpra.example")
    blocked_remote = waiting.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": waiting_csrf},
        json={"idempotency_key": str(uuid.uuid4())},
    )
    assert blocked_remote.status_code == 403
    assert blocked_remote.json()["error"]["code"] == "POLICY_BLOCKED"
    created = client.post(
        "/api/admin/managers",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "new.manager@kpra.example",
            "temporary_password": "Temporary-Pass-99",
            "display_name": "New Manager",
            "role": "MANAGER",
            "employee_number": "NEW-1",
            "designation": "Director",
            "department_name": "Finance",
            "device_pin": "1001",
            "remote_attendance_enabled": True,
            "is_test_record": True,
        },
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    assert created.json()["employee"]["is_test_record"] is True
    assert "password_hash" not in created.text
    manager = TestClient(app)
    login = manager.post(
        "/api/auth/login",
        json={"email": "new.manager@kpra.example", "password": "Temporary-Pass-99"},
    )
    assert login.json()["status"] == "password_change_required"
    blocked = client.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": str(uuid.uuid4())},
    )
    assert blocked.status_code == 403
    approved = client.patch(
        "/api/admin/policy",
        headers={"X-CSRF-Token": csrf},
        json={
            "remote_attendance_approved": True,
            "confirmation": "APPROVE REMOTE ATTENDANCE",
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["remote_attendance_approved"] is True
    deactivated = client.post(f"/api/admin/managers/{user_id}/deactivate", headers={"X-CSRF-Token": csrf})
    assert deactivated.json()["is_active"] is False
    inactive = manager.post(
        "/api/auth/login",
        json={"email": "new.manager@kpra.example", "password": "Temporary-Pass-99"},
    )
    assert inactive.status_code == 403
    assert "deactivated" in inactive.json()["error"]["message"]


def test_physical_import_pairs_and_flags_duplicates(client: TestClient):
    add_user(email="admin@kpra.example", role="ADMIN")
    add_user(email="person@kpra.example", employee_number="EMP-100", remote=False)
    csrf = sign_in(client, "admin@kpra.example")
    csv_text = (
        "employee_number,event_type,event_time_utc,external_event_id,source\n"
        "EMP-100,CHECK_IN,2026-11-01T04:00:00+00:00,DEV-1,PHYSICAL_BIOMETRIC\n"
        "EMP-100,CHECK_OUT,2026-11-01T12:00:00+00:00,DEV-2,PHYSICAL_BIOMETRIC\n"
        "EMP-100,CHECK_IN,2026-11-01T12:30:00+00:00,DEV-1,PHYSICAL_BIOMETRIC\n"
        "EMP-100,CHECK_IN,2026-11-01T13:00:00+00:00,DEV-3,PHYSICAL_BIOMETRIC\n"
        "EMP-100,CHECK_IN,2026-11-01T13:30:00+00:00,DEV-4,PHYSICAL_BIOMETRIC\n"
    )
    response = client.post(
        "/api/admin/imports/attendance",
        headers={"X-CSRF-Token": csrf},
        data={"reason": "Authorized office export for November", "confirmation": "AUTHORIZED EXPORT"},
        files={"file": ("attendance.csv", csv_text, "text/csv")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 3
    assert response.json()["flagged"] == 1
    assert response.json()["skipped_duplicates"] == 1
    session = SessionLocal()
    try:
        sources = set(session.scalars(select(AttendanceEvent.source)).all())
        assert sources == {"PHYSICAL_BIOMETRIC"}
        closed = session.scalar(
            select(func.count()).select_from(AttendanceSession).where(AttendanceSession.status == "CLOSED")
        )
        assert closed == 1
        flagged = session.scalar(
            select(func.count()).select_from(AttendanceEvent).where(AttendanceEvent.status == "FLAGGED")
        )
        assert flagged == 1
    finally:
        session.close()
    bad = client.post(
        "/api/admin/imports/attendance",
        headers={"X-CSRF-Token": csrf},
        data={"reason": "This file is not confirmed", "confirmation": "no"},
        files={"file": ("attendance.csv", csv_text, "text/csv")},
    )
    assert bad.status_code == 422


def test_worker_does_not_mark_anything_synced(db):
    pulse()
    db.rollback()
    rows = db.scalars(select(IntegrationConnection)).all()
    assert rows
    assert all(row.status != "SYNCED" for row in rows)
    assert db.scalar(select(func.count()).select_from(SyncJob)) == 0
    get_settings().timenex_integration_enabled = True
    try:
        pulse()
        db.rollback()
        timenex = db.scalar(select(IntegrationConnection).where(IntegrationConnection.code == "timenex"))
        assert timenex.status == "MISCONFIGURED"
        assert "No request was sent" in (timenex.last_error or "")
        assert db.scalar(select(func.count()).select_from(SyncJob)) == 0
    finally:
        get_settings().timenex_integration_enabled = False
        pulse()


def test_login_rate_limit(client: TestClient):
    get_settings().login_rate_limit = 2
    limiter = __import__("app.rate_limit", fromlist=["limiter"]).limiter
    limiter.reset()
    for _ in range(2):
        client.post("/api/auth/login", json={"email": "nobody@kpra.example", "password": "wrong-password-1"})
    blocked = client.post("/api/auth/login", json={"email": "nobody@kpra.example", "password": "wrong-password-1"})
    assert blocked.status_code == 429


def test_health_hides_development_accounts_outside_development(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "ok"
    assert "development_accounts" not in body
    assert all(item["synced"] is False for item in body["integrations"])


def test_audit_contains_login_and_devices_stay_disconnected(client: TestClient):
    add_user(email="admin@kpra.example", role="ADMIN")
    csrf = sign_in(client, "admin@kpra.example")
    audit = client.get("/api/admin/audit")
    actions = {entry["action"] for entry in audit.json()["entries"]}
    assert "LOGIN_SUCCESS" in actions
    devices = client.get("/api/admin/devices")
    observed = devices.json()["devices"][0]
    assert observed["serial_number"] == "VDE2244500097"
    assert observed["status"] == "NOT_CONNECTED"
    assert observed["receiving_events"] is False
    assert "not receiving" in observed["status_label"].lower()
    assert observed["last_seen_at"] is None
    integrations = client.get("/api/admin/integrations")
    assert integrations.json()["sync_available"] is False
    assert integrations.json()["integrations"]
    assert all(item["synced"] is False for item in integrations.json()["integrations"])
    assert all("No request was sent" in item["status_label"] for item in integrations.json()["integrations"])
    online = client.patch(
        f"/api/admin/devices/{observed['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"status": "ONLINE"},
    )
    assert online.status_code == 422
    session = SessionLocal()
    try:
        leaked = session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.details.contains({"password": "x"})))
        assert leaked == 0
    finally:
        session.close()


def test_adapters_refuse_network_calls():
    assert SenseFaceAdapter().status()["connected"] is False
    assert TimeNexAdapter().status()["synced"] is False
    with pytest.raises(AppError):
        SenseFaceAdapter().ingest(b"not-a-terminal")
    with pytest.raises(AppError):
        TimeNexAdapter().push_attendance("event")


def test_harness_stores_isolated_test_data(client: TestClient):
    add_user(email="qa.admin@kpra.example", role="ADMIN")
    csrf = sign_in(client, "qa.admin@kpra.example")
    response = client.post(
        "/api/admin/harness/run",
        headers={"X-CSRF-Token": csrf},
        json={"employees": 1, "pairs": 1, "confirmation": "RUN QA HARNESS"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "TEST_DATA"
    assert body["transmitted"] is False
    assert body["events"] == 2
    session = SessionLocal()
    try:
        events = session.scalars(select(AttendanceEvent).where(AttendanceEvent.source == "TEST_DATA")).all()
        assert len(events) == 2
        assert {event.event_type for event in events} == {"CHECK_IN", "CHECK_OUT"}
        assert all(event.metadata_json.get("is_test_record") is True for event in events)
        jobs = session.scalar(select(func.count()).select_from(SyncJob))
        assert jobs == 0
    finally:
        session.close()


def test_harness_is_disabled_in_production(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "app_env", "production")
    with pytest.raises(AppError) as caught:
        run_harness(
            None,  # type: ignore[arg-type]
            actor_id=uuid.uuid4(),
            employees=1,
            pairs=1,
            confirmation="RUN QA HARNESS",
            ip_address=None,
        )
    assert caught.value.status == 403


def test_employee_import_rejects_duplicates_and_accepts_new_rows(client: TestClient):
    add_user(email="kept.person@kpra.example", role="MANAGER", employee_number="EMP-9")
    add_user(email="import.admin@kpra.example", role="ADMIN")
    csrf = sign_in(client, "import.admin@kpra.example")
    duplicate = (
        "employee_number,full_name,email,temporary_password,designation,department_name,is_test_record\n"
        "EMP-9,Existing Person,kept.person@kpra.example,Correct-Horse-9,Officer,QA,true\n"
    )
    rejected = client.post(
        "/api/admin/imports/employees",
        headers={"X-CSRF-Token": csrf},
        data={"reason": "Authorized directory check", "confirmation": "AUTHORIZED EXPORT"},
        files={"file": ("people.csv", duplicate, "text/csv")},
    )
    assert rejected.status_code == 422
    assert "duplicate" in rejected.text.lower()
    fresh = (
        "employee_number,full_name,email,temporary_password,designation,department_name,is_test_record\n"
        "EMP-77,New Person,new.person@kpra.example,Correct-Horse-9,Officer,QA,true\n"
    )
    accepted = client.post(
        "/api/admin/imports/employees",
        headers={"X-CSRF-Token": csrf},
        data={"reason": "Authorized directory check", "confirmation": "AUTHORIZED EXPORT"},
        files={"file": ("people.csv", fresh, "text/csv")},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["imported"] == 1
    session = SessionLocal()
    try:
        created = session.scalar(select(Employee).where(Employee.employee_number == "EMP-77"))
        assert created is not None
        assert created.is_test_record is True
        assert created.remote_attendance_enabled is False
    finally:
        session.close()


def test_timenex_mapping_does_not_confirm_attendance(client: TestClient):
    add_user(email="ali@kpra.example", employee_number="20043", full_name="Ali", test_record=False)
    add_user(email="map.admin@kpra.example", role="ADMIN", employee_number=None)
    enable_remote_policy()
    denied = client.post("/api/auth/login", json={"email": "20043", "password": PASSWORD})
    assert denied.status_code == 422
    admin_csrf = sign_in(client, "map.admin@kpra.example")
    created = client.post(
        "/api/admin/timenex/mappings",
        headers={"X-CSRF-Token": admin_csrf},
        json={"employee_number": "20043", "timenex_employee_id": "20043"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["verification_status"] == "PENDING"
    assert created.json()["authenticates_by_itself"] is False
    listed = client.get("/api/admin/managers")
    assert listed.status_code == 200, listed.text
    ali = next(row for row in listed.json()["managers"] if (row.get("employee") or {}).get("employee_number") == "20043")
    assert ali["timenex_mapping"]["verification_status"] == "PENDING"
    assert ali["timenex_mapping"]["timenex_employee_id"] == "20043"
    assert ali["timenex_mapping"]["authenticates_by_itself"] is False
    assert ali["remote_check_in_ready"] is True
    assert ali["remote_check_in"] == "Can check in here"
    assert ali["timenex_confirmed"] is False
    unlinked = next(row for row in listed.json()["managers"] if row["email"] == "map.admin@kpra.example")
    assert unlinked["remote_check_in_ready"] is False
    assert unlinked["remote_check_in"] == "No employee profile"
    audit = client.get("/api/admin/audit")
    summaries = [entry["summary"] for entry in audit.json()["entries"]]
    pending = next(item for item in summaries if "TimeNex mapping saved as pending" in item)
    assert "TimeNex ID 20043" in pending
    assert "fingerprint" not in pending.lower()
    assert "synced" not in pending.lower()
    mapping_id = created.json()["id"]
    csrf = sign_in(client, "ali@kpra.example")
    ready = client.get("/api/attendance/me/state")
    assert ready.status_code == 200, ready.text
    assert ready.json()["authentication_method"] == "PASSWORD_MFA"
    assert ready.json()["timenex"]["mapping_status"] == "PENDING"
    assert ready.json()["timenex"]["timenex_employee_id"] == "20043"
    assert ready.json()["timenex"]["authenticates_by_itself"] is False
    assert ready.json()["timenex"]["confirmed"] is False
    assert ready.json()["timenex"]["submitted"] is False
    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["remote_check_in"] == "Can check in here"
    assert me.json()["user"]["timenex_confirmed"] is False
    assert me.json()["user"]["timenex_mapping"]["verification_status"] == "PENDING"
    blocked = client.post(
        "/api/admin/timenex/mappings",
        headers={"X-CSRF-Token": csrf},
        json={"employee_number": "20043", "timenex_employee_id": "20044"},
    )
    assert blocked.status_code == 403
    checked_in = client.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": str(uuid.uuid4())},
    )
    assert checked_in.status_code == 200, checked_in.text
    assert checked_in.json()["timenex"]["confirmed"] is False
    assert checked_in.json()["timenex"]["submitted"] is False
    assert checked_in.json()["timenex"]["status"] == "NOT_MAPPED"
    assert "fingerprint" not in checked_in.json()["event"]["source_label"].lower()
    admin_csrf = sign_in(client, "map.admin@kpra.example")
    approved = client.post(
        f"/api/admin/timenex/mappings/{mapping_id}/decision",
        headers={"X-CSRF-Token": admin_csrf},
        json={"decision": "APPROVE", "reason": "Matched the authorized employee list."},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["verification_status"] == "VERIFIED"
    csrf = sign_in(client, "ali@kpra.example")
    checked_out = client.post(
        "/api/attendance/check-out",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": str(uuid.uuid4())},
    )
    assert checked_out.status_code == 200, checked_out.text
    body = checked_out.json()
    assert body["event"]["source"] == "REMOTE_WEB"
    assert body["event"]["authentication_method"] == "PASSWORD_MFA"
    assert body["timenex"]["confirmed"] is False
    assert body["timenex"]["submitted"] is False
    assert body["timenex"]["status"] == "AWAITING_CONTRACT"
    assert body["timenex"]["timenex_employee_id"] == "20043"
    assert "not confirmed" in body["timenex"]["message"].lower()
    own = client.get("/api/attendance/me/history?date_from=2026-01-01&date_to=2026-12-31")
    assert own.status_code == 200, own.text
    assert {event["timenex_mapping_label"] for event in own.json()["events"]} == {"Verified · 20043"}
    assert all(event["timenex_confirmed"] is False for event in own.json()["events"])
    after = client.get("/api/attendance/me/state")
    assert after.json()["timenex"]["mapping_status"] == "VERIFIED"
    assert after.json()["timenex"]["confirmed"] is False
    month = client.get("/api/attendance/me/summary")
    assert month.json()["check_ins"] >= 1
    assert month.json()["timenex_confirmed"] == 0
    admin_csrf = sign_in(client, "map.admin@kpra.example")
    board = client.get("/api/admin/attendance/today")
    linked = next(row for row in board.json()["rows"] if row["employee_number"] == "20043")
    assert linked["timenex_mapping_status"] == "VERIFIED"
    assert linked["timenex_mapping_label"] == "Verified · 20043"
    assert linked["timenex_confirmed"] is False
    assert "fingerprint" not in linked["timenex_mapping_label"].lower()
    exported = client.post(
        "/api/admin/attendance/export",
        headers={"X-CSRF-Token": admin_csrf},
        json={"date_from": "2026-01-01", "date_to": "2026-12-31"},
    )
    assert exported.status_code == 200, exported.text
    assert "Verified · 20043" in exported.text
    assert "Saved here · TimeNex has not confirmed" in exported.text
    assert "Confirmed by TimeNex" not in exported.text
    pulse()
    session = SessionLocal()
    try:
        statuses = set(session.scalars(select(AttendanceOutbox.status)).all())
        assert "CONFIRMED" not in statuses
        assert session.scalar(select(func.count()).select_from(SyncJob)) == 0
        references = session.scalars(select(AttendanceOutbox.timenex_reference)).all()
        assert all(item is None for item in references)
    finally:
        session.close()


def test_passkey_options_store_no_biometric_template(client: TestClient):
    add_user(email="passkey@kpra.example", employee_number="PK-1")
    csrf = sign_in(client, "passkey@kpra.example")
    options = client.post("/api/auth/webauthn/register/options", headers={"X-CSRF-Token": csrf})
    assert options.status_code == 200, options.text
    blocked = client.post(
        "/api/auth/webauthn/register/options",
        headers={"X-CSRF-Token": csrf, "Origin": "http://127.0.0.1:3000"},
    )
    assert blocked.status_code == 400, blocked.text
    assert "localhost:3000" in blocked.json()["error"]["message"]
    payload = options.json()["public_key"]
    assert payload["challenge"]
    assert payload["rp"]["id"]
    selection = payload["authenticatorSelection"]
    assert selection.get("authenticatorAttachment") not in {"platform", "cross-platform"}
    assert selection["userVerification"] == "required"
    assert selection["residentKey"] == "required"
    assert payload["hints"][0] == "client-device"
    dumped = options.text.lower()
    assert "template" not in dumped
    assert "fingerprint" not in dumped
    forged = client.post(
        "/api/auth/webauthn/register/verify",
        headers={"X-CSRF-Token": csrf},
        json={"challenge_id": options.json()["challenge_id"], "credential": {"id": "not-a-passkey"}},
    )
    assert forged.status_code == 401
    login_options = client.post("/api/auth/webauthn/login/options")
    assert login_options.status_code == 200, login_options.text
    rejected = client.post(
        "/api/auth/webauthn/login/verify",
        json={"challenge_id": login_options.json()["challenge_id"], "credential": {"id": "not-a-passkey"}},
    )
    assert rejected.status_code == 401
    session = SessionLocal()
    try:
        assert session.scalar(select(func.count()).select_from(WebAuthnCredential)) == 0
    finally:
        session.close()


def test_localhost_attendance_prompt_uses_this_computer(client: TestClient):
    add_user(email="hello@kpra.example", employee_number="HELLO-1")
    session = SessionLocal()
    try:
        user = session.scalar(select(User).where(User.email == "hello@kpra.example"))
        assert user is not None
        session.add(
            WebAuthnCredential(
                user_id=user.id,
                credential_id=b"phone-and-computer",
                public_key=b"public-key",
                sign_count=0,
                transports=["hybrid", "internal"],
                device_type="multi_device",
                backed_up=True,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    finally:
        session.close()
    csrf = sign_in(client, "hello@kpra.example")
    options = client.post(
        "/api/attendance/passkey/options",
        headers={"X-CSRF-Token": csrf, "Origin": "http://localhost:3000"},
    )
    assert options.status_code == 200, options.text
    allow = options.json()["public_key"]["allowCredentials"]
    assert len(allow) == 1
    assert allow[0]["transports"] == ["internal"]
    assert "no fingerprint or face template is stored." in options.json()["message"]


def test_timenex_adapter_never_submits_when_switch_is_on():
    get_settings().timenex_integration_enabled = True
    get_settings().timenex_base_url = "https://www.timenex.net:8443"
    try:
        result = TimeNexAdapter().submit({"event_id": "local-only"})
        assert result.confirmed is False
        assert result.submitted is False
        assert result.reference is None
        assert result.status == "MISCONFIGURED"
        assert "No request was sent" in result.message
        assert TimeNexAdapter().status()["acceptance_test_passed"] is False
    finally:
        get_settings().timenex_integration_enabled = False
        get_settings().timenex_base_url = ""


def test_mapping_file_verifies_without_sending(client: TestClient):
    add_user(email="sara@kpra.example", employee_number="EMP-MAP", full_name="Sara", test_record=False)
    add_user(email="file.admin@kpra.example", role="ADMIN")
    enable_remote_policy()
    csrf = sign_in(client, "file.admin@kpra.example")
    template = client.get("/api/admin/timenex/mappings-template.csv")
    assert template.status_code == 200
    assert "timenex_employee_id" in template.text
    csv_text = "employee_number,timenex_employee_id,full_name,department_name\nEMP-MAP,MAP-9,Wrong Name,Unassigned\n"
    rejected = client.post(
        "/api/admin/timenex/mappings/import",
        headers={"X-CSRF-Token": csrf},
        data={"reason": "Authorized mapping file", "confirmation": "AUTHORIZED EXPORT"},
        files={"file": ("map.csv", csv_text, "text/csv")},
    )
    assert rejected.status_code == 422
    fresh = "employee_number,timenex_employee_id,full_name,department_name\nEMP-MAP,MAP-9,Sara,Unassigned\n"
    accepted = client.post(
        "/api/admin/timenex/mappings/import",
        headers={"X-CSRF-Token": csrf},
        data={"reason": "Authorized mapping file", "confirmation": "AUTHORIZED EXPORT"},
        files={"file": ("map.csv", fresh, "text/csv")},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["created"] == 1
    assert "Nothing was read from or sent to TimeNex" in accepted.json()["message"]
    manager = sign_in(client, "sara@kpra.example")
    hidden = client.get("/api/admin/timenex/outbox")
    assert hidden.status_code == 403
    checked = client.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": manager},
        json={"idempotency_key": str(uuid.uuid4())},
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["timenex"]["confirmed"] is False
    assert checked.json()["timenex"]["status"] == "AWAITING_CONTRACT"
    admin = sign_in(client, "file.admin@kpra.example")
    queue = client.get("/api/admin/timenex/outbox", headers={"X-CSRF-Token": admin})
    assert queue.status_code == 200, queue.text
    body = queue.json()
    assert body["confirmed_count"] == 0
    assert body["items"][0]["employee_number"] == "EMP-MAP"
    assert body["items"][0]["confirmed"] is False
    assert "No request was sent" in body["items"][0]["status_label"]


def test_rejected_timenex_mapping_can_be_replaced(client: TestClient):
    add_user(email="replace.person@kpra.example", employee_number="REPLACE-1", full_name="Replace Person")
    add_user(email="replace.admin@kpra.example", role="ADMIN", employee_number=None)
    csrf = sign_in(client, "replace.admin@kpra.example")
    created = client.post(
        "/api/admin/timenex/mappings",
        headers={"X-CSRF-Token": csrf},
        json={"employee_number": "REPLACE-1", "timenex_employee_id": "OLD-1"},
    )
    assert created.status_code == 201, created.text
    mapping_id = created.json()["id"]
    rejected = client.post(
        f"/api/admin/timenex/mappings/{mapping_id}/decision",
        headers={"X-CSRF-Token": csrf},
        json={"decision": "REJECT", "reason": "The first ID was not the authorized one."},
    )
    assert rejected.status_code == 200, rejected.text
    replaced = client.post(
        "/api/admin/timenex/mappings",
        headers={"X-CSRF-Token": csrf},
        json={"employee_number": "REPLACE-1", "timenex_employee_id": "NEW-1"},
    )
    assert replaced.status_code == 201, replaced.text
    assert replaced.json()["id"] == mapping_id
    assert replaced.json()["verification_status"] == "PENDING"
    assert replaced.json()["timenex_employee_id"] == "NEW-1"
    assert replaced.json()["authenticates_by_itself"] is False
    blocked = client.post(
        "/api/admin/timenex/mappings",
        headers={"X-CSRF-Token": csrf},
        json={"employee_number": "REPLACE-1", "timenex_employee_id": "NEW-2"},
    )
    assert blocked.status_code == 409
    audit = client.get("/api/admin/audit")
    summary = next(item["summary"] for item in audit.json()["entries"] if "replaced" in item["summary"].lower())
    assert "TimeNex ID NEW-1" in summary
    assert "synced" not in summary.lower()
    rejected_again = client.post(
        f"/api/admin/timenex/mappings/{mapping_id}/decision",
        headers={"X-CSRF-Token": csrf},
        json={"decision": "REJECT", "reason": "Hold this ID so the file can replace it."},
    )
    assert rejected_again.status_code == 200, rejected_again.text
    csv_text = "employee_number,timenex_employee_id,full_name,department_name\nREPLACE-1,FILE-1,Replace Person,Unassigned\n"
    imported = client.post(
        "/api/admin/timenex/mappings/import",
        headers={"X-CSRF-Token": csrf},
        data={"reason": "Authorized replacement file", "confirmation": "AUTHORIZED EXPORT"},
        files={"file": ("map.csv", csv_text, "text/csv")},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["created"] == 1
    listed = client.get("/api/admin/timenex/mappings")
    row = next(item for item in listed.json()["mappings"] if item["local_employee_number"] == "REPLACE-1")
    assert row["id"] == mapping_id
    assert row["verification_status"] == "VERIFIED"
    assert row["timenex_employee_id"] == "FILE-1"
    assert row["authenticates_by_itself"] is False


def test_admin_can_revoke_a_passkey_and_erp_is_not_called(client: TestClient):
    add_user(email="revoke.person@kpra.example", employee_number="REVOKE-1")
    add_user(email="revoke.admin@kpra.example", role="ADMIN", employee_number=None)
    enable_remote_policy()
    session = SessionLocal()
    try:
        user = session.scalar(select(User).where(User.email == "revoke.person@kpra.example"))
        assert user is not None
        session.add(
            WebAuthnCredential(
                user_id=user.id,
                credential_id=b"revoke-test-credential",
                public_key=b"public-key",
                sign_count=0,
                transports=["hybrid"],
                backed_up=False,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
        user_id = str(user.id)
    finally:
        session.close()
    person = sign_in(client, "revoke.person@kpra.example")
    denied = client.post(
        f"/api/admin/managers/{user_id}/passkeys/revoke",
        headers={"X-CSRF-Token": person},
        json={"reason": "A manager cannot revoke passkeys."},
    )
    assert denied.status_code == 403
    admin = sign_in(client, "revoke.admin@kpra.example")
    revoked = client.post(
        f"/api/admin/managers/{user_id}/passkeys/revoke",
        headers={"X-CSRF-Token": admin},
        json={"reason": "Administrator revoked this passkey."},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked"] == 1
    session = SessionLocal()
    try:
        assert session.scalar(select(func.count()).select_from(WebAuthnCredential).where(WebAuthnCredential.credential_id == b"revoke-test-credential")) == 0
    finally:
        session.close()
    csrf = sign_in(client, "revoke.person@kpra.example")
    checked = client.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": str(uuid.uuid4())},
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["erp"]["submitted"] is False
    assert checked.json()["erp"]["confirmed"] is False
    assert checked.json()["erp"]["acceptance_test_passed"] is False
    assert "No request was sent" in checked.json()["erp"]["message"]
    assert checked.json()["timenex"]["confirmed"] is False


ROSTER = (
    '"S#","ID","Name","Department","Designation","File #","Gender","Bps","In","Out","Status","Remarks"\n'
    "1,9001,Sample Person,Finance,Officer,--,Male,--,09:00,17:00,absent,\n"
    "2,9002,Second Person,,Clerk,--,Female,--,--,--,present,\n"
)


def test_timenex_employee_export_is_reviewed_before_it_is_stored(client: TestClient):
    add_user(email="roster.admin@kpra.example", role="ADMIN", employee_number=None)
    add_user(email="roster.manager@kpra.example", employee_number="ROSTER-MGR")
    manager = sign_in(client, "roster.manager@kpra.example")
    denied = client.post(
        "/api/admin/timenex/employees/preview",
        headers={"X-CSRF-Token": manager},
        files={"file": ("roster.csv", ROSTER, "text/csv")},
    )
    assert denied.status_code == 403
    csrf = sign_in(client, "roster.admin@kpra.example")
    repeated = ROSTER + "3,9001,Different Person,Finance,Officer,--,Male,--,--,--,late,\n"
    blocked = client.post(
        "/api/admin/timenex/employees/preview",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("roster.csv", repeated, "text/csv")},
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["blocked"] == 1
    assert blocked.json()["attendance_import_supported"] is False
    refused = client.post(
        "/api/admin/timenex/employees/import",
        headers={"X-CSRF-Token": csrf},
        data={"reason": "Authorized TimeNex employee export", "confirmation": "AUTHORIZED EXPORT"},
        files={"file": ("roster.csv", repeated, "text/csv")},
    )
    assert refused.status_code == 422
    preview = client.post(
        "/api/admin/timenex/employees/preview",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("roster.csv", ROSTER, "text/csv")},
    )
    body = preview.json()
    assert body["ready"] == 2
    assert body["blocked"] == 0
    assert body["missing_department"] == 1
    assert body["used_columns"] == ["ID", "Name", "Department", "Designation"]
    assert "In" in body["ignored_columns"] and "Status" in body["ignored_columns"]
    assert "password" in body["attendance_note"].lower() or "attendance" in body["attendance_note"].lower()
    wrong = client.post(
        "/api/admin/timenex/employees/import",
        headers={"X-CSRF-Token": csrf},
        data={"reason": "Authorized TimeNex employee export", "confirmation": "IMPORT"},
        files={"file": ("roster.csv", ROSTER, "text/csv")},
    )
    assert wrong.status_code == 422
    stored = client.post(
        "/api/admin/timenex/employees/import",
        headers={"X-CSRF-Token": csrf},
        data={"reason": "Authorized TimeNex employee export", "confirmation": "AUTHORIZED EXPORT"},
        files={"file": ("roster.csv", ROSTER, "text/csv")},
    )
    assert stored.status_code == 200, stored.text
    assert stored.json()["imported"] == 2
    assert stored.json()["attendance_imported"] == 0
    assert stored.json()["id_is_password"] is False
    assert stored.json()["accounts_can_sign_in"] is False
    session = SessionLocal()
    try:
        employee = session.scalar(select(Employee).where(Employee.employee_number == "9001"))
        assert employee is not None
        assert employee.full_name == "Sample Person"
        assert employee.device_pin is None
        assert employee.is_active is False
        assert employee.remote_attendance_enabled is False
        assert employee.is_test_record is False
        user = session.get(User, employee.user_id)
        assert user is not None and user.is_active is False
        assert user.email != "9001"
        mapping = session.scalar(select(TimenexIdentityMapping).where(TimenexIdentityMapping.employee_id == employee.id))
        assert mapping is not None
        assert mapping.timenex_employee_id == "9001"
        assert mapping.verification_status == "VERIFIED"
        assert mapping.verification_source == "AUTHORIZED_EXPORT"
        blank = session.scalar(select(Employee).where(Employee.employee_number == "9002"))
        assert blank is not None and blank.department_id is None
        punches = session.scalar(select(func.count()).select_from(AttendanceEvent).where(AttendanceEvent.employee_id == employee.id))
        assert punches == 0
    finally:
        session.close()
    not_a_login = client.post("/api/auth/login", json={"email": "9001", "password": "9001"})
    assert not_a_login.status_code == 422
    not_the_pin = client.post("/api/auth/login", json={"email": "timenex.9001@kpra.example", "password": "9001"})
    assert not_the_pin.status_code == 401
    again = client.post(
        "/api/admin/timenex/employees/import",
        headers={"X-CSRF-Token": csrf},
        data={"reason": "Authorized TimeNex employee export", "confirmation": "AUTHORIZED EXPORT"},
        files={"file": ("roster.csv", ROSTER, "text/csv")},
    )
    assert again.status_code == 200, again.text
    assert again.json()["imported"] == 0
    assert again.json()["already_stored"] == 2
    changed = ROSTER.replace("Sample Person", "Renamed Person", 1)
    conflict = client.post(
        "/api/admin/timenex/employees/import",
        headers={"X-CSRF-Token": csrf},
        data={"reason": "Authorized TimeNex employee export", "confirmation": "AUTHORIZED EXPORT"},
        files={"file": ("roster.csv", changed, "text/csv")},
    )
    assert conflict.status_code == 422
    audit = client.get("/api/admin/audit")
    summaries = [item["summary"] for item in audit.json()["entries"] if "employee file" in item["summary"].lower()]
    assert any("2 stored" in item and "not passwords" in item and "no attendance was sent" in item for item in summaries)


def test_sheet_id_is_not_sent_to_erp_or_marked_synced(client: TestClient):
    from app.services.sync_queue import apply_erp_outcome, process_outbox

    add_user(email="sheet.person@kpra.example", employee_number="22091", full_name="Sheet Person", test_record=False)
    add_user(email="sheet.admin@kpra.example", role="ADMIN", employee_number=None)
    enable_remote_policy()
    admin = sign_in(client, "sheet.admin@kpra.example")
    created = client.post(
        "/api/admin/timenex/mappings",
        headers={"X-CSRF-Token": admin},
        json={"employee_number": "22091", "timenex_employee_id": "22091"},
    )
    assert created.status_code == 201, created.text
    mapping_id = created.json()["id"]
    approved = client.post(
        f"/api/admin/timenex/mappings/{mapping_id}/decision",
        headers={"X-CSRF-Token": admin},
        json={"decision": "APPROVE", "reason": "Map the sheet ID before any ERP submission."},
    )
    assert approved.status_code == 200, approved.text
    person = sign_in(client, "sheet.person@kpra.example")
    key = str(uuid.uuid4())
    checked = client.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": person},
        json={"idempotency_key": key},
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["erp"]["erp_accepted"] is False
    assert checked.json()["erp"]["timenex_confirmed"] is False
    assert checked.json()["erp"]["status"] == "NEEDS_ERP_ID"
    assert checked.json()["timenex"]["confirmed"] is False
    replay = client.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": person},
        json={"idempotency_key": key},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    admin = sign_in(client, "sheet.admin@kpra.example")
    same = client.post(
        f"/api/admin/timenex/mappings/{mapping_id}/erp-id",
        headers={"X-CSRF-Token": admin},
        json={"erp_employee_id": "22091", "reason": "Try the sheet ID as the ERP key."},
    )
    assert same.status_code == 200, same.text
    assert "different internal key" in same.json()["message"]
    assert same.json()["sent"] is False
    distinct = client.post(
        f"/api/admin/timenex/mappings/{mapping_id}/erp-id",
        headers={"X-CSRF-Token": admin},
        json={"erp_employee_id": "21", "reason": "Store the ERP form key, not the sheet ID."},
    )
    assert distinct.status_code == 200, distinct.text
    assert distinct.json()["erp_employee_id"] == "21"
    assert distinct.json()["timenex_employee_id"] == "22091"
    assert distinct.json()["timenex_confirmed"] is False
    person = sign_in(client, "sheet.person@kpra.example")
    checked_out = client.post(
        "/api/attendance/check-out",
        headers={"X-CSRF-Token": person},
        json={"idempotency_key": str(uuid.uuid4())},
    )
    assert checked_out.status_code == 200, checked_out.text
    assert checked_out.json()["erp"]["status"] == "NOT_SENT"
    assert checked_out.json()["erp"]["submitted"] is False
    assert checked_out.json()["timenex"]["confirmed"] is False
    assert "No request was sent" in checked_out.json()["erp"]["message"]
    session = SessionLocal()
    try:
        rows = session.scalars(select(AttendanceOutbox).order_by(AttendanceOutbox.created_at.asc())).all()
        sheet_rows = [row for row in rows if row.timenex_employee_id == "22091"]
        assert len(sheet_rows) == 2
        apply_erp_outcome(session, sheet_rows[1], "ACCEPTED", "erp-ref-1")
        session.refresh(sheet_rows[1])
        assert sheet_rows[1].erp_status == "ACCEPTED"
        assert sheet_rows[1].timenex_reference is None
        apply_erp_outcome(session, sheet_rows[0], "FAILED")
        session.refresh(sheet_rows[0])
        assert sheet_rows[0].erp_status == "FAILED"
        assert sheet_rows[0].timenex_reference is None
        apply_erp_outcome(session, sheet_rows[0], "PENDING")
        process_outbox(session)
        session.commit()
        session.refresh(sheet_rows[0])
        session.refresh(sheet_rows[1])
        assert sheet_rows[0].timenex_reference is None
        assert sheet_rows[1].erp_status == "ACCEPTED"
        assert sheet_rows[1].timenex_reference is None
    finally:
        session.close()
    admin = sign_in(client, "sheet.admin@kpra.example")
    queue = client.get("/api/admin/timenex/outbox", headers={"X-CSRF-Token": admin})
    assert queue.status_code == 200, queue.text
    body = queue.json()
    assert body["confirmed_count"] == 0
    assert body["erp_accepted_count"] >= 1
    assert body["timenex_pending_count"] >= 1
    assert "not TimeNex confirmation" in body["note"]
    assert "TimeNex is the attendance destination" in body["note"]


def test_passkey_rule_blocks_a_password_session_and_ignores_location(client: TestClient):
    from app.models import AttendancePolicy, AuthSession

    add_user(email="passkey.rule@kpra.example", employee_number="PK-RULE")
    enable_remote_policy()
    csrf = sign_in(client, "passkey.rule@kpra.example")
    state = client.get("/api/attendance/me/state")
    assert state.status_code == 200, state.text
    assert state.json()["passkey_required_for_attendance"] is False
    assert state.json()["passkey_count"] == 0
    assert state.json()["location_checked"] is False
    located = client.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": str(uuid.uuid4()), "latitude": 34.0, "longitude": 71.5},
    )
    assert located.status_code == 422
    session = SessionLocal()
    try:
        policy = session.get(AttendancePolicy, 1)
        assert policy is not None
        policy.passkey_required_for_attendance = True
        policy.updated_at = datetime.now(timezone.utc)
        session.commit()
    finally:
        session.close()
    blocked = client.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": str(uuid.uuid4())},
    )
    assert blocked.status_code == 403, blocked.text
    assert "passkey" in blocked.json()["error"]["message"].lower()
    assert "does not prove where you are" in blocked.json()["error"]["message"]
    missing = client.post("/api/attendance/passkey/options", headers={"X-CSRF-Token": csrf})
    assert missing.status_code == 409, missing.text
    forged = client.post(
        "/api/attendance/passkey/verify",
        headers={"X-CSRF-Token": csrf},
        json={"challenge_id": str(uuid.uuid4()), "credential": {"id": "not-a-passkey"}},
    )
    assert forged.status_code == 401
    session = SessionLocal()
    try:
        user = session.scalar(select(User).where(User.email == "passkey.rule@kpra.example"))
        assert user is not None
        auth = session.scalar(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)))
        assert auth is not None
        auth.auth_method = "WEBAUTHN"
        session.commit()
    finally:
        session.close()
    allowed = client.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": str(uuid.uuid4())},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["event"]["authentication_method"] == "WEBAUTHN"
    assert allowed.json()["event"]["user_verification"] == "required"
    assert allowed.json()["event"]["location_checked"] is False
    assert allowed.json()["timenex"]["confirmed"] is False
    session = SessionLocal()
    try:
        audit_row = session.scalar(
            select(AuditLog).where(AuditLog.action == "ATTENDANCE_CHECK_IN").order_by(AuditLog.created_at.desc())
        )
        assert audit_row is not None
        assert audit_row.details["user_verification"] == "required"
        assert audit_row.details["proves_location"] is False
        assert "latitude" not in audit_row.details
    finally:
        session.close()
    assert allowed.json()["event"]["source"] in {"REMOTE_WEB", "REMOTE_MOBILE"}
    session = SessionLocal()
    try:
        policy = session.get(AttendancePolicy, 1)
        assert policy is not None
        policy.passkey_required_for_attendance = False
        policy.updated_at = datetime.now(timezone.utc)
        session.commit()
    finally:
        session.close()


def test_portal_client_posts_the_mark_attendance_form():
    import httpx

    from app.services.timenex_portal import TimenexPortalClient

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/":
            return httpx.Response(200, text='<input name="_token" value="login-token">')
        if path == "/loginaction":
            seen["login"] = True
            return httpx.Response(200, text="<title>Dashboard</title>")
        if path == "/marks_attendance/mark_attendance/22063":
            return httpx.Response(
                200,
                text='<input name="_token" value="form-token"> Attendance Date <input name="time_in" value="08:45:47"> <input name="time_out" value="">',
            )
        if path == "/marks_attendance/markattendance":
            seen.setdefault("bodies", []).append(request.content.decode())
            seen["body"] = seen["bodies"][-1]
            return httpx.Response(200, text="Attendance saved")
        return httpx.Response(404, text=path)

    settings = get_settings()
    settings.timenex_username = "person@example"
    settings.timenex_password = "not-the-real-password"
    try:
        http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://portal.test", follow_redirects=True)
        client = TimenexPortalClient(client=http)
        opened = client.open_form("22063")
        assert opened["ok"] is True
        existing = client.current_times("22063")
        assert existing == {"time_in": "08:45:47", "time_out": None}
        result = client.mark_day("22063", datetime(2026, 11, 5).date(), datetime(2026, 11, 5, 9, 0).time(), datetime(2026, 11, 5, 17, 0).time())
        blank = client.mark_day("22063", datetime(2026, 11, 5).date(), datetime(2026, 11, 5, 6, 0).time(), None)
    finally:
        settings.timenex_username = ""
        settings.timenex_password = ""
    full, empty = seen["bodies"]
    assert seen["login"] is True
    assert "id=22063" in full
    assert "attendance_date=2026-11-05" in full
    assert "time_in=09%3A00%3A00" in full
    assert "time_out=17%3A00%3A00" in full
    assert "ack=on" in full
    assert "time_in=06%3A00%3A00" in empty
    assert "time_out=&ack=on" in empty
    assert blank["ok"] is True
    assert blank["payload"]["time_out"] == ""
    assert "password" not in seen["body"]
    assert result["ok"] is True
    assert result["reference"] == "portal:22063:2026-11-05"


def _portal_fake(monkeypatch):
    class FakePortal:
        def __init__(self) -> None:
            self.calls: list[tuple] = []
            self.existing = {"time_in": None, "time_out": None}
            self.ok = True
            self.error = "TimeNex rejected the day."

        def current_times(self, employee_id: str) -> dict:
            return self.existing

        def mark_day(self, employee_id, day, time_in, time_out):
            self.calls.append(
                (
                    str(employee_id),
                    day.isoformat(),
                    time_in.strftime("%H:%M:%S"),
                    None if time_out is None else time_out.strftime("%H:%M:%S"),
                )
            )
            if not self.ok:
                return {"ok": False, "error": self.error, "reference": None, "payload": {"id": str(employee_id)}, "status_code": 200}
            return {
                "ok": True,
                "error": None,
                "reference": f"portal:{employee_id}:{day.isoformat()}",
                "payload": {"id": str(employee_id), "attendance_date": day.isoformat()},
                "status_code": 200,
            }

        def close(self) -> None:
            return None

    fake = FakePortal()
    monkeypatch.setattr("app.services.timenex_push.TimenexPortalClient", lambda: fake)
    monkeypatch.setattr(get_settings(), "timenex_portal_enabled", True)
    return fake


def _verify_mapping(client: TestClient, csrf: str, employee_number: str, timenex_id: str) -> None:
    created = client.post(
        "/api/admin/timenex/mappings",
        headers={"X-CSRF-Token": csrf},
        json={"employee_number": employee_number, "timenex_employee_id": timenex_id},
    )
    assert created.status_code == 201, created.text
    approved = client.post(
        f"/api/admin/timenex/mappings/{created.json()['id']}/decision",
        headers={"X-CSRF-Token": csrf},
        json={"decision": "APPROVE", "reason": "Matched the authorized employee list."},
    )
    assert approved.status_code == 200, approved.text


def test_admin_mark_can_push_the_saved_day(client: TestClient, monkeypatch):
    add_user(email="push.admin@kpra.example", role="ADMIN")
    add_user(email="push.person@kpra.example", employee_number="PUSH-1", full_name="Push Person")
    csrf = sign_in(client, "push.admin@kpra.example")
    _verify_mapping(client, csrf, "PUSH-1", "88021")
    fake = _portal_fake(monkeypatch)
    marked = client.post(
        "/api/admin/attendance/mark",
        headers={"X-CSRF-Token": csrf},
        json={
            "employee_number": "PUSH-1",
            "reason": "Manual day entered by an administrator.",
            "check_in_at": "2026-11-02T09:00:00+05:00",
            "check_out_at": "2026-11-02T17:00:00+05:00",
            "push_to_timenex": True,
        },
    )
    assert marked.status_code == 200, marked.text
    body = marked.json()
    assert body["timenex_push"] == "confirmed"
    assert body["timenex_confirmed"] is True
    assert body["transmitted"] is True
    assert fake.calls == [("88021", "2026-11-02", "09:00:00", "17:00:00")]
    assert "fingerprint" not in body["source_label"].lower()


def test_admin_mark_keeps_the_local_day_when_timenex_rejects(client: TestClient, monkeypatch):
    add_user(email="reject.admin@kpra.example", role="ADMIN")
    add_user(email="reject.person@kpra.example", employee_number="PUSH-2", full_name="Reject Person")
    csrf = sign_in(client, "reject.admin@kpra.example")
    _verify_mapping(client, csrf, "PUSH-2", "88022")
    fake = _portal_fake(monkeypatch)
    fake.ok = False
    fake.error = "The given data was invalid."
    marked = client.post(
        "/api/admin/attendance/mark",
        headers={"X-CSRF-Token": csrf},
        json={
            "employee_number": "PUSH-2",
            "reason": "Manual day entered by an administrator.",
            "check_in_at": "2026-11-02T09:15:00+05:00",
            "check_out_at": "2026-11-02T17:15:00+05:00",
            "push_to_timenex": True,
        },
    )
    assert marked.status_code == 200, marked.text
    body = marked.json()
    assert body["timenex_push"] == "failed"
    assert body["timenex_confirmed"] is False
    assert body["timenex_error"] == "The given data was invalid."
    assert body["message"] == "The given data was invalid."
    assert {event["event_type"] for event in body["events"]} == {"CHECK_IN", "CHECK_OUT"}
    assert all(event["timenex_sync_status"] == "FAILED" for event in body["events"])


def test_admin_can_push_in_only_and_add_the_out_time_later(client: TestClient, monkeypatch):
    add_user(email="open.admin@kpra.example", role="ADMIN")
    add_user(email="open.person@kpra.example", employee_number="PUSH-5", full_name="Open Person")
    csrf = sign_in(client, "open.admin@kpra.example")
    _verify_mapping(client, csrf, "PUSH-5", "88025")
    fake = _portal_fake(monkeypatch)
    opened = client.post(
        "/api/admin/attendance/mark",
        headers={"X-CSRF-Token": csrf},
        json={
            "employee_number": "PUSH-5",
            "reason": "Check-in entered without an out time.",
            "check_in_at": "2026-11-02T09:00:00+05:00",
            "push_to_timenex": True,
        },
    )
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["timenex_push"] == "confirmed"
    assert fake.calls == [("88025", "2026-11-02", "09:00:00", None)]
    assert body["events"][0]["timenex_label"] == "In only – Out still open"
    closed = client.post(
        "/api/admin/attendance/mark",
        headers={"X-CSRF-Token": csrf},
        json={
            "employee_number": "PUSH-5",
            "reason": "Out time added for the same day.",
            "check_in_at": "2026-11-02T09:00:00+05:00",
            "check_out_at": "2026-11-02T17:00:00+05:00",
            "push_to_timenex": True,
        },
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["timenex_push"] == "confirmed"
    assert fake.calls[-1] == ("88025", "2026-11-02", "09:00:00", "17:00:00")
    assert {event["event_type"] for event in closed.json()["events"]} == {"CHECK_IN", "CHECK_OUT"}
    assert all(event["timenex_label"] == "Confirmed by TimeNex" for event in closed.json()["events"])


def test_admin_override_replaces_an_existing_day_with_the_form_times(client: TestClient, monkeypatch):
    add_user(email="replace.admin@kpra.example", role="ADMIN")
    add_user(email="replace.person@kpra.example", employee_number="PUSH-6", full_name="Replace Person")
    csrf = sign_in(client, "replace.admin@kpra.example")
    _verify_mapping(client, csrf, "PUSH-6", "88026")
    fake = _portal_fake(monkeypatch)
    first = client.post(
        "/api/admin/attendance/mark",
        headers={"X-CSRF-Token": csrf},
        json={
            "employee_number": "PUSH-6",
            "reason": "First mark for this day.",
            "check_in_at": "2026-11-02T09:00:00+05:00",
            "check_out_at": "2026-11-02T17:00:00+05:00",
            "push_to_timenex": True,
        },
    )
    assert first.status_code == 200, first.text
    replaced = client.post(
        "/api/admin/attendance/mark",
        headers={"X-CSRF-Token": csrf},
        json={
            "employee_number": "PUSH-6",
            "reason": "Replace the day with different times.",
            "check_in_at": "2026-11-02T10:15:00+05:00",
            "check_out_at": "2026-11-02T16:45:00+05:00",
            "push_to_timenex": True,
        },
    )
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()
    assert body["message"] == "Day replaced in TimeNex"
    assert body["timenex_push"] == "confirmed"
    assert body["timenex_confirmed"] is True
    assert fake.calls[-1] == ("88026", "2026-11-02", "10:15:00", "16:45:00")
    opened = client.post(
        "/api/admin/attendance/mark",
        headers={"X-CSRF-Token": csrf},
        json={
            "employee_number": "PUSH-6",
            "reason": "Replace the closed day with check-in only.",
            "check_in_at": "2026-11-02T11:00:00+05:00",
            "push_to_timenex": True,
        },
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["message"] == "Day replaced in TimeNex"
    assert fake.calls[-1] == ("88026", "2026-11-02", "11:00:00", None)


def test_admin_in_only_push_replaces_a_day_while_another_session_is_open(client: TestClient, monkeypatch):
    add_user(email="open.admin@kpra.example", role="ADMIN")
    add_user(email="open.person@kpra.example", employee_number="PUSH-7", full_name="Open Person")
    csrf = sign_in(client, "open.admin@kpra.example")
    _verify_mapping(client, csrf, "PUSH-7", "88027")
    fake = _portal_fake(monkeypatch)
    closed = client.post(
        "/api/admin/attendance/mark",
        headers={"X-CSRF-Token": csrf},
        json={
            "employee_number": "PUSH-7",
            "reason": "Record a full day first.",
            "check_in_at": "2026-11-04T09:00:00+05:00",
            "check_out_at": "2026-11-04T17:00:00+05:00",
            "push_to_timenex": True,
        },
    )
    assert closed.status_code == 200, closed.text
    opened = client.post(
        "/api/admin/attendance/mark",
        headers={"X-CSRF-Token": csrf},
        json={
            "employee_number": "PUSH-7",
            "reason": "Leave one day open.",
            "check_in_at": "2026-11-03T09:00:00+05:00",
            "push_to_timenex": True,
        },
    )
    assert opened.status_code == 200, opened.text
    replaced = client.post(
        "/api/admin/attendance/mark",
        headers={"X-CSRF-Token": csrf},
        json={
            "employee_number": "PUSH-7",
            "reason": "Replace another day with in time only.",
            "check_in_at": "2026-11-04T06:00:00+05:00",
            "push_to_timenex": True,
        },
    )
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()
    assert body["message"] == "Day replaced in TimeNex"
    assert body["timenex_push"] == "confirmed"
    assert body["timenex_confirmed"] is True
    assert fake.calls[-1] == ("88027", "2026-11-04", "06:00:00", None)
    assert "open session" not in body["message"].lower()


def test_sync_of_another_day_asks_before_overwrite(client: TestClient, monkeypatch):
    add_user(email="force.admin@kpra.example", role="ADMIN")
    add_user(email="force.person@kpra.example", employee_number="PUSH-3", full_name="Force Person")
    enable_remote_policy()
    csrf = sign_in(client, "force.admin@kpra.example")
    _verify_mapping(client, csrf, "PUSH-3", "88023")
    marked = client.post(
        "/api/admin/attendance/mark",
        headers={"X-CSRF-Token": csrf},
        json={
            "employee_number": "PUSH-3",
            "reason": "Manual day entered by an administrator.",
            "check_in_at": "2026-11-01T09:00:00+05:00",
            "check_out_at": "2026-11-01T17:00:00+05:00",
        },
    )
    assert marked.status_code == 200, marked.text
    event_id = marked.json()["events"][0]["id"]
    fake = _portal_fake(monkeypatch)
    person = TestClient(app)
    person_csrf = sign_in(person, "force.person@kpra.example")
    blocked = person.post(f"/api/attendance/sync-to-timenex/{event_id}", headers={"X-CSRF-Token": person_csrf})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "FORCE_REQUIRED"
    assert "Overwrite the TimeNex day 2026-11-01" in blocked.json()["error"]["message"]
    assert fake.calls == []
    sent = person.post(
        f"/api/attendance/sync-to-timenex/{event_id}?confirm_overwrite=true",
        headers={"X-CSRF-Token": person_csrf},
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["ok"] is True
    assert fake.calls == [("88023", "2026-11-01", "09:00:00", "17:00:00")]


def test_sync_asks_when_timenex_already_has_the_day(client: TestClient, monkeypatch):
    add_user(email="exists.admin@kpra.example", role="ADMIN")
    add_user(email="exists.person@kpra.example", employee_number="PUSH-4", full_name="Exists Person")
    enable_remote_policy()
    csrf = sign_in(client, "exists.admin@kpra.example")
    _verify_mapping(client, csrf, "PUSH-4", "88024")
    fake = _portal_fake(monkeypatch)
    fake.existing = {"time_in": "08:45:47", "time_out": None}
    person = TestClient(app)
    person_csrf = sign_in(person, "exists.person@kpra.example")
    checked = person.post(
        "/api/attendance/check-in",
        headers={"X-CSRF-Token": person_csrf},
        json={"idempotency_key": str(uuid.uuid4())},
    )
    assert checked.status_code == 200, checked.text
    event_id = checked.json()["event"]["id"]
    blocked = person.post(f"/api/attendance/sync-to-timenex/{event_id}", headers={"X-CSRF-Token": person_csrf})
    assert blocked.status_code == 409, blocked.text
    assert "08:45:47" in blocked.json()["error"]["message"]
    assert fake.calls == []
    sent = person.post(
        f"/api/attendance/sync-to-timenex/{event_id}?confirm_overwrite=true",
        headers={"X-CSRF-Token": person_csrf},
    )
    assert sent.status_code == 200, sent.text
    assert fake.calls[0][0] == "88024"
    assert fake.calls[0][3] is None


@pytest.mark.manual
def test_portal_live_login_is_manual():
    if not os.environ.get("TIMENEX_PORTAL_MANUAL"):
        pytest.skip("Set TIMENEX_PORTAL_MANUAL=1 to open the live TimeNex form.")
    from app.services.timenex_portal import TimenexPortalClient

    client = TimenexPortalClient()
    try:
        client.login()
        opened = client.open_form("22063")
    finally:
        client.close()
    assert opened["ok"] is True

