from datetime import datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from app.constants import ALLOW_PASSWORD_ATTENDANCE, APPROVE_REMOTE, DAY_CODES, IMPORT_CONFIRMATION, REQUIRE_PASSKEY, REVOKE_REMOTE
from app.labels import source_label
from app.models import (
    AttendanceEvent,
    AttendanceOutbox,
    AttendancePolicy,
    AttendanceSession,
    CorrectionRequest,
    Department,
    Device,
    Employee,
    EmployeeDeviceMapping,
    IntegrationConnection,
    TimenexIdentityMapping,
    User,
    UserMfa,
    WebAuthnCredential,
)
from app.services.attendance import load_policy
from app.timeutil import PKT, pkt_day_bounds, time_view, utcnow


def present_employee(employee: Employee, department: Department | None) -> dict:
    return {
        "id": str(employee.id),
        "employee_number": employee.employee_number,
        "full_name": employee.full_name,
        "designation": employee.designation,
        "department": department.name if department else None,
        "department_id": str(employee.department_id) if employee.department_id else None,
        "device_pin": employee.device_pin,
        "remote_attendance_enabled": employee.remote_attendance_enabled,
        "is_active": employee.is_active,
        "is_test_record": employee.is_test_record,
    }


def present_account(db: Session, user: User) -> dict:
    employee = db.scalar(select(Employee).where(Employee.user_id == user.id))
    department = db.get(Department, employee.department_id) if employee and employee.department_id else None
    mfa = db.get(UserMfa, user.id)
    passkeys = db.scalar(
        select(func.count()).select_from(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id)
    )
    mapping = db.scalar(select(TimenexIdentityMapping).where(TimenexIdentityMapping.user_id == user.id))
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "mfa_enabled": bool(mfa and mfa.enabled),
        "passkey_count": int(passkeys or 0),
        "timenex_mapping": None
        if mapping is None
        else {
            "timenex_employee_id": mapping.timenex_employee_id,
            "verification_status": mapping.verification_status,
            "account_status": mapping.account_status,
            "authenticates_by_itself": False,
        },
        "timenex_confirmed": False,
        **_remote_check_in(user, employee, bool(mfa and mfa.enabled), load_policy(db)),
        "employee": present_employee(employee, department) if employee else None,
    }


def _remote_check_in(user: User, employee: Employee | None, mfa_enabled: bool, policy: AttendancePolicy) -> dict:
    if not user.is_active:
        label = "Account inactive"
    elif user.must_change_password:
        label = "Password change required"
    elif not mfa_enabled:
        label = "Authenticator not enrolled"
    elif employee is None:
        label = "No employee profile"
    elif not employee.is_active:
        label = "Employee inactive"
    elif not policy.remote_attendance_approved:
        label = "Remote attendance off"
    elif not employee.remote_attendance_enabled:
        label = "Remote off for this person"
    elif policy.enforce_working_days and DAY_CODES[utcnow().astimezone(PKT).weekday()] not in set(policy.working_days or []):
        label = "Outside working days"
    else:
        return {"remote_check_in_ready": True, "remote_check_in": "Can check in here"}
    return {"remote_check_in_ready": False, "remote_check_in": label}


def _authentication_label(method: str | None) -> str | None:
    if method == "PASSWORD_MFA":
        return "Password and authenticator code"
    if method == "WEBAUTHN":
        return "Passkey on this device"
    return None


def _mapping_label(row: TimenexIdentityMapping | None) -> str:
    if row is None:
        return "Not mapped"
    names = {"VERIFIED": "Verified", "PENDING": "Pending", "REJECTED": "Rejected"}
    name = names.get(row.verification_status, row.verification_status)
    return f"{name} · {row.timenex_employee_id}"


def _day_has_check_out(db: Session, event: AttendanceEvent) -> bool:
    local_day = event.effective_at_utc.astimezone(PKT).date()
    start = datetime.combine(local_day, time.min, tzinfo=PKT)
    start_utc, end_utc = pkt_day_bounds(start)
    attendance = db.scalar(
        select(AttendanceSession).where(
            AttendanceSession.employee_id == event.employee_id,
            AttendanceSession.opened_at >= start_utc,
            AttendanceSession.opened_at < end_utc,
        )
    )
    if attendance is not None:
        return attendance.check_out_event_id is not None
    if event.event_type == "CHECK_OUT":
        return True
    found = db.scalar(
        select(AttendanceEvent.id).where(
            AttendanceEvent.employee_id == event.employee_id,
            AttendanceEvent.event_type == "CHECK_OUT",
            AttendanceEvent.status == "RECORDED",
            AttendanceEvent.effective_at_utc >= start_utc,
            AttendanceEvent.effective_at_utc < end_utc,
        )
    )
    return found is not None


def _timenex_result_label(status: str | None, confirmed: bool, *, out_still_open: bool = False) -> str:
    if confirmed and out_still_open:
        return "In only – Out still open"
    if confirmed:
        return "Confirmed by TimeNex"
    if status == "NOT_MAPPED":
        return "Saved here · mapping not verified"
    if status == "AWAITING_CONTRACT":
        return "Saved here · TimeNex has not confirmed"
    if status == "MISCONFIGURED":
        return "Saved here · no verified TimeNex contract"
    if status == "FAILED":
        return "TimeNex sync failed"
    return "Saved here · not submitted"


def present_event(event, employee: Employee | None = None, department: Department | None = None) -> dict:
    metadata = event.metadata_json or {}
    sync = None
    mapping = None
    session = object_session(event)
    if session is not None:
        sync = session.scalar(select(AttendanceOutbox).where(AttendanceOutbox.event_id == event.id))
        if employee is not None:
            mapping = session.scalar(
                select(TimenexIdentityMapping).where(TimenexIdentityMapping.employee_id == employee.id)
            )
    sync_status = sync.status if sync is not None else event.timenex_sync_status
    reference = sync.timenex_reference if sync is not None else event.timenex_reference
    confirmed = sync_status == "CONFIRMED" and bool(reference)
    out_still_open = bool(confirmed and session is not None and not _day_has_check_out(session, event))
    return {
        "id": str(event.id),
        "employee_id": str(event.employee_id),
        "employee_number": employee.employee_number if employee else None,
        "employee_name": employee.full_name if employee else None,
        "department": department.name if department else None,
        "is_test_record": employee.is_test_record if employee else bool(metadata.get("is_test_record")),
        "event_type": event.event_type,
        "source": event.source,
        "source_label": source_label(event.source),
        "status": event.status,
        "server_received_at": time_view(event.server_received_at_utc),
        "effective_at": time_view(event.effective_at_utc),
        "client_reported_at": time_view(event.client_reported_at),
        "external_event_id": event.external_event_id,
        "reconciliation_status": metadata.get("reconciliation_status"),
        "authentication_method": event.authentication_method,
        "authentication_label": _authentication_label(event.authentication_method),
        "client_platform": metadata.get("client_platform") or None,
        "user_verification": metadata.get("user_verification") or None,
        "location_checked": False,
        "timenex_employee_id": mapping.timenex_employee_id if mapping else None,
        "timenex_mapping_status": mapping.verification_status if mapping else "UNMAPPED",
        "timenex_mapping_label": _mapping_label(mapping),
        "timenex_sync_status": sync_status,
        "timenex_reference": reference if confirmed else None,
        "timenex_confirmed": confirmed,
        "timenex_error": event.timenex_error,
        "timenex_pushed_at": time_view(event.timenex_pushed_at),
        "timenex_label": _timenex_result_label(sync_status, confirmed, out_still_open=out_still_open),
    }


def present_session(session: AttendanceSession | None) -> dict | None:
    if session is None:
        return None
    opened_local = session.opened_at.astimezone(PKT).date()
    today_local = utcnow().astimezone(PKT).date()
    return {
        "id": str(session.id),
        "status": session.status,
        "opened_at": time_view(session.opened_at),
        "closed_at": time_view(session.closed_at),
        "started_on_previous_day": opened_local < today_local,
        "check_in_event_id": str(session.check_in_event_id),
        "local_date": opened_local.isoformat(),
    }


_CORRECTION_KINDS = {
    "MISSED_CHECK_IN": "Missed check-in",
    "MISSED_CHECK_OUT": "Missed check-out",
    "HISTORICAL_PAIR": "Historical check-in and check-out",
}
_CORRECTION_STATUS = {
    "PENDING": "Waiting for an administrator",
    "APPROVED": "Approved. Saved here as an administrator correction",
    "REJECTED": "Rejected. No attendance record was added",
}
_CORRECTION_NOTE = (
    "An approved request adds an administrator correction in this application. It is not sent to TimeNex."
)


def present_correction(db: Session, row: CorrectionRequest) -> dict:
    employee = db.get(Employee, row.employee_id)
    mapping = None
    if employee is not None:
        mapping = db.scalar(
            select(TimenexIdentityMapping).where(TimenexIdentityMapping.employee_id == employee.id)
        )
    return {
        "id": str(row.id),
        "employee_id": str(row.employee_id),
        "employee_number": employee.employee_number if employee else None,
        "employee_name": employee.full_name if employee else None,
        "is_test_record": employee.is_test_record if employee else False,
        "kind": row.kind,
        "kind_label": _CORRECTION_KINDS.get(row.kind, row.kind),
        "reason": row.reason,
        "requested_start_at": time_view(row.requested_start_at),
        "requested_end_at": time_view(row.requested_end_at),
        "status": row.status,
        "status_label": _CORRECTION_STATUS.get(row.status, row.status),
        "review_reason": row.review_reason,
        "reviewed_at": time_view(row.reviewed_at),
        "created_at": time_view(row.created_at),
        "resulting_session_id": str(row.resulting_session_id) if row.resulting_session_id else None,
        "timenex_mapping_label": _mapping_label(mapping),
        "timenex_confirmed": False,
        "timenex_note": _CORRECTION_NOTE,
    }


def present_policy(policy: AttendancePolicy) -> dict:
    return {
        "remote_attendance_approved": policy.remote_attendance_approved,
        "working_days": list(policy.working_days or []),
        "work_start_local": policy.work_start_local.strftime("%H:%M") if policy.work_start_local else None,
        "work_end_local": policy.work_end_local.strftime("%H:%M") if policy.work_end_local else None,
        "grace_minutes": policy.grace_minutes,
        "inference_enabled": policy.inference_enabled,
        "enforce_working_days": policy.enforce_working_days,
        "passkey_required_for_attendance": policy.passkey_required_for_attendance,
        "policy_note": policy.policy_note,
        "approval_phrase": APPROVE_REMOTE,
        "revoke_phrase": REVOKE_REMOTE,
        "passkey_phrase": REQUIRE_PASSKEY,
        "password_attendance_phrase": ALLOW_PASSWORD_ATTENDANCE,
        "location_policy": (
            "Remote attendance is allowed away from KPRA offices. "
            "This application does not check GPS, office Wi-Fi, or an office IP address. "
            "A passkey does not prove where the employee is."
        ),
        "import_confirmation": IMPORT_CONFIRMATION,
    }


_AUDIT_LABELS = {
    "LOGIN_SUCCESS": "Signed in",
    "LOGOUT": "Signed out",
    "PASSKEY_REGISTERED": "Passkey registered",
    "PASSKEY_REVOKED": "Passkey revoked",
    "ATTENDANCE_PASSKEY_VERIFIED": "Passkey verified for attendance",
    "ERP_INTERNAL_ID_RECORDED": "ERP internal ID stored",
    "TIMENEX_MAPPING_CREATED": "TimeNex mapping saved as pending",
    "TIMENEX_MAPPING_REPLACED": "Rejected TimeNex mapping replaced with a pending link",
    "TIMENEX_MAPPING_APPROVED": "TimeNex mapping verified",
    "TIMENEX_MAPPING_REJECTED": "TimeNex mapping rejected",
    "TIMENEX_MAPPING_IMPORTED": "TimeNex mappings verified from an authorized file",
    "TIMENEX_EMPLOYEES_IMPORTED": "TimeNex employee file stored in this application",
    "ATTENDANCE_CHECK_IN": "Check-in saved in this application",
    "ATTENDANCE_CHECK_OUT": "Check-out saved in this application",
    "ATTENDANCE_MARKED": "Administrator mark saved in this application",
    "ATTENDANCE_EXPORTED": "Attendance exported from this application",
}


def audit_summary(action: str, details: dict | None) -> str:
    details = details or {}
    base = _AUDIT_LABELS.get(action, action.replace("_", " ").capitalize())
    parts: list[str] = []
    number = details.get("employee_number")
    if isinstance(number, str) and number:
        parts.append(f"employee {number[:32]}")
    timenex_id = details.get("timenex_employee_id")
    if isinstance(timenex_id, str) and timenex_id:
        parts.append(f"TimeNex ID {timenex_id[:32]}")
    method = details.get("authentication_method")
    if method == "PASSWORD_MFA":
        parts.append("password and authenticator code")
    elif method == "WEBAUTHN":
        parts.append("passkey")
    if action in {"ATTENDANCE_CHECK_IN", "ATTENDANCE_CHECK_OUT"}:
        platform = {
            "android": "Android browser",
            "ios": "Apple browser",
            "windows": "Windows browser",
            "mac": "Mac browser",
        }.get(str(details.get("client_platform") or ""))
        if platform:
            parts.append(platform)
        parts.append("location was not checked")
    sync = details.get("timenex_sync_status")
    if sync == "CONFIRMED":
        parts.append("TimeNex confirmed")
    elif isinstance(sync, str) and sync:
        parts.append("TimeNex has not confirmed")
    if action == "PASSKEY_REGISTERED":
        parts.append("no fingerprint or face template was stored")
    if action == "TIMENEX_EMPLOYEES_IMPORTED":
        imported = details.get("imported")
        if isinstance(imported, int):
            parts.append(f"{imported} stored")
        parts.append("IDs are not passwords")
        parts.append("no attendance was sent")
    if not parts:
        return base
    return f"{base} · {', '.join(parts)}"


def _device_status_label(status: str) -> str:
    if status == "DISABLED":
        return "Disabled. This application is not receiving events."
    return "Not connected. This application is not receiving events."


def _integration_status_label(status: str) -> str:
    labels = {
        "NO_SUPPORTED_INTERFACE": "No supported connection. No request was sent.",
        "MISCONFIGURED": "The switch is on without a verified contract. No request was sent.",
        "DISABLED": "Disabled. No request was sent.",
    }
    return labels.get(status, "Not connected. No request was sent.")


def present_device(device: Device, mappings: list[EmployeeDeviceMapping] | None = None) -> dict:
    return {
        "id": str(device.id),
        "name": device.name,
        "model": device.model,
        "serial_number": device.serial_number,
        "firmware": device.firmware,
        "platform": device.platform,
        "mac_address": device.mac_address,
        "location": device.location,
        "protocol": device.protocol,
        "status": device.status,
        "status_label": _device_status_label(device.status),
        "receiving_events": False,
        "last_seen_at": time_view(device.last_seen_at),
        "notes": device.notes,
        "mappings": [
            {
                "id": str(item.id),
                "employee_id": str(item.employee_id),
                "device_user_pin": item.device_user_pin,
            }
            for item in (mappings or [])
        ],
    }


def present_integration(row: IntegrationConnection, sync_job_count: int) -> dict:
    heartbeat = time_view(row.last_heartbeat_at)
    stale = True
    if row.last_heartbeat_at is not None:
        stale = (utcnow() - row.last_heartbeat_at).total_seconds() > 180
    return {
        "code": row.code,
        "display_name": row.display_name,
        "status": row.status,
        "status_label": _integration_status_label(row.status),
        "summary": row.summary,
        "last_error": row.last_error,
        "last_heartbeat_at": heartbeat,
        "heartbeat_stale": stale,
        "sync_job_count": sync_job_count,
        "synced": False,
    }
