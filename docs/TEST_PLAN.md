# Test Plan

## Executed on 3 Nov 2026
API tests in `backend/tests/test_api.py`: 14 passed. They cover labels, MFA, server time, idempotency, concurrent check-in, CSRF, corrections, CSV, policy, physical CSV import, the worker, rate limiting, and device status.

Browser checks covered manager check-in and check-out, history, a correction request, administrator approval, the audit list, CSV export, and a phone-sized layout. Playwright is not installed, so there is no automated browser suite yet.

## Manager
- Login success/failure.
- MFA success/failure/recovery.
- Manager can only view and mark own attendance.
- Check-In and Check-Out.
- Duplicate and concurrent requests.
- Missing Check-Out handling.
- Mobile viewport and PWA installation.
- Session expiry.

## Admin
- Create/deactivate manager.
- RBAC enforcement.
- Correction request, approval and rejection.
- Audit trail.
- CSV export permission.

## Integrity/security
- Client timestamp tampering does not alter server timestamp.
- Duplicate idempotency key.
- Race conditions.
- CSRF, XSS, SQL injection checks.
- Rate limiting.
- No secret leakage.
- Test physical/remote source separation.

## Integration
- Test with vendor-approved test endpoint/device where available.
- Device offline/reconnect.
- Duplicate external event.
- Unknown employee PIN.
- TimeNex unavailable.
- Failed sync/retry/reconciliation.
- Do not test by altering production attendance.
