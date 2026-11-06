# Data Model (Initial)

Suggested entities:
- users
- employees
- roles / permissions
- user_mfa
- attendance_events
- attendance_sessions (or derived sessions)
- correction_requests
- devices
- employee_device_mappings
- integration_connections
- sync_jobs
- audit_logs

Attendance event:
- id UUID
- employee_id
- event_type CHECK_IN / CHECK_OUT
- source REMOTE_WEB / REMOTE_MOBILE / PHYSICAL_BIOMETRIC / ADMIN_CORRECTION
- server_received_at_utc
- client_reported_at (optional, diagnostic only)
- device_id nullable
- external_event_id nullable
- idempotency_key
- status
- created_by
- metadata JSON with allowlisted fields

Add constraints/indexes for duplicate event prevention, employee/time lookups, external event uniqueness, and open attendance session logic. Use transactional operations to avoid concurrent double check-ins.

Implemented additions:

- `timenex_identity_mappings` links one local user and employee to one TimeNex employee ID. Verification is `PENDING`, `VERIFIED`, or `REJECTED`. A TimeNex ID does not authenticate.
- `webauthn_credentials` stores a passkey public key, credential id, and signature counter. It does not store a fingerprint or face template.
- `webauthn_challenges` stores a short-lived registration or sign-in challenge.
- `attendance_outbox` is the retry queue for a future verified TimeNex write. `CONFIRMED` is allowed only when a reference exists. The current adapter never sets it.
- Attendance events record `authentication_method` (`PASSWORD_MFA` or `WEBAUTHN`) and an initial `timenex_sync_status`. The outbox is the status that can change. Attendance events stay append-only.
