# MASTER BUILD PROMPT — KPRA SMART ATTENDANCE LITE
## Greenfield, end-to-end implementation for Claude Code / Cursor

You are an experienced principal software architect, senior full-stack engineer, application security engineer, database engineer, QA engineer, and ZKTeco SenseFace 7 Series integration specialist.

Your task is to build a lightweight, secure, usable attendance application from scratch for authorized KPRA managers. The first release must prioritize getting managers to mark their own attendance from laptops and mobile phones quickly and reliably.

This is a GREENFIELD project. The repository already contains project instructions and documentation. Read `CLAUDE.md`, every relevant file in `.cursor/rules/`, and the documents under `docs/` before taking action. Do not ask the user to manually create folders, directories, or instruction files. Create and maintain the required project structure yourself.

## 1. CONFIRMED PRODUCT DECISIONS

- Product name: KPRA Smart Attendance Lite.
- Initial users: Directors, Deputy Directors, and specifically authorized managers only.
- Access: laptop, desktop, Android, iPhone, and tablet browser.
- App type: responsive web application and installable PWA.
- Hosting: hybrid access; public HTTPS entry point, application/backend and database hosted inside KPRA-controlled infrastructure.
- Existing biometric hardware: ZKTeco SenseFace 7 Series; the supplied terminal is SenseFace 7A.
- Existing portal: `https://www.timenex.net:8443/`.
- Existing device screen showed ADMS mode, server address `173.208.167.249`, port `80`, serial `VDE2244500097`, MAC `00:17:61:10:ad:46`, firmware/platform details shown in the supplied photographs. Treat these as observed configuration, not permission to change production settings.
- Integration objective: new manager attendance application plus a verified path to coexist/synchronize with TimeNex.
- Keep the first release small. Do not build a universal all-ZKTeco protocol suite, HR/payroll system, facial recognition service, or simulated biometric device unless later explicitly requested.

## 2. NON-NEGOTIABLE ATTENDANCE INTEGRITY

The application must distinguish at least:
- `REMOTE_WEB`
- `REMOTE_MOBILE`
- `PHYSICAL_BIOMETRIC`
- `ADMIN_CORRECTION`

A browser Check-In is remote attendance. It must never be described, stored, or exported as a fingerprint scan, face scan, or physical biometric verification.

Do not generate fake fingerprint/face data, impersonate a SenseFace device, fabricate physical attendance logs, bypass TimeNex authentication, or silently write into a production attendance database.

Remote attendance must be enabled only for accounts and policies explicitly authorized by KPRA. Keep the policy configurable and document that organizational approval is required.

The backend generates the authoritative event-received timestamp. Store timestamps in UTC and display Pakistan Standard Time (`Asia/Karachi`). Preserve client-reported time only as diagnostic metadata, never as the authoritative attendance time.

Use idempotency keys and database constraints to prevent duplicate events. Handle duplicate check-ins, missing check-outs, overlapping events, and simultaneous submissions safely.

## 3. FIRST RELEASE — REQUIRED FEATURES

### Manager portal
- Secure sign-in and sign-out.
- MFA (TOTP preferred; recovery codes stored hashed).
- Clear current attendance state.
- Large Check-In / Check-Out action.
- Confirmation receipt with event ID, server timestamp, source, and status.
- Today's attendance timeline.
- Personal attendance history.
- Current month summary.
- Mobile-first responsive UI and installable PWA.
- Clear error messages and loading states.
- No ability to mark attendance for another employee.

### Administrator portal
- Admin-only login and MFA.
- Create, deactivate, and manage authorized manager accounts.
- Map application account to official employee number/PIN.
- Assign department/designation and attendance permissions.
- View attendance records and source.
- Review and approve/reject correction requests.
- Require a reason for every correction.
- Export basic CSV report.
- View audit log and integration status.
- No unrestricted ability to edit or delete attendance history.

### Attendance rules
- One open attendance session per employee at a time.
- Check-Out requires a valid open session unless an authorized exception workflow is used.
- Duplicate Check-In and duplicate Check-Out must be rejected or flagged deterministically.
- Server-side validation and transaction handling.
- Configurable working-day and schedule settings; do not hardcode unconfirmed KPRA rules.
- Do not infer late/early status until authorized schedules and grace periods are configured.
- Support admin correction workflow with immutable audit entries.
- All records include source and actor metadata.

### SenseFace 7 Series integration
Treat device integration as a separate, optional module from remote attendance. The remote manager portal must work even if the SenseFace device or TimeNex is offline.

The first device integration target is the SenseFace 7 Series, especially SenseFace 7A. Official product materials indicate the series supports ADMS and protocol switching between AC Push and TA Push; exact behavior and configuration must be verified against the exact model, firmware, manual, and installed software.

Do not assume that the photographed device's ADMS configuration means the new app is already receiving its events. First document the current route and existing TimeNex service.

Implement only a verified, authorized integration path:
1. Prefer documented SenseFace 7 ADMS/TA Push integration where supported and compatible.
2. Preserve the current TimeNex path until a tested migration/change plan is approved.
3. If TimeNex owns the current ADMS endpoint, do not repoint the device to the new app without approval and a rollback plan.
4. If event forwarding or supported export/import is available, implement that.
5. If no supported interface is available, report the limitation and provide a safe manual CSV import/export fallback.
6. Do not invent undocumented packet formats or claim compatibility without testing.

The device module should minimally provide:
- device registry: name, model, serial, firmware, location, protocol, last-seen, status;
- event ingestion only for verified supported protocol;
- mapping from device user PIN to application employee;
- raw event metadata retained for diagnostics;
- normalized physical attendance event;
- duplicate detection and reconciliation;
- connection/heartbeat status where protocol supports it;
- integration error log.

Do not expose device ports or device management interfaces to the public internet. Restrict device traffic to internal networks or an approved secure tunnel. Do not assume port 4370 is used by the SenseFace 7 ADMS service; identify and document each protocol's actual port and direction.

### TimeNex integration
TimeNex integration is a feasibility gate, not an assumption.

Research only through a user-authorized browser session, official documentation, approved export facilities, or an API explicitly authorized for use. Do not bypass login, scrape private endpoints without authorization, defeat controls, or write directly to a production database.

Determine whether TimeNex provides a supported API, webhook, supported import/export, or vendor-assisted integration. Document evidence and limitations.

If a supported write interface exists, implement a controlled adapter with idempotency, retries, reconciliation, and explicit sync status. If only read/export is supported, implement that honestly. If no supported interface exists, keep the new platform operational independently and provide a controlled report/export workflow. Never show "Synced" unless confirmed by the destination.

## 4. HYBRID DEPLOYMENT

Recommended deployment:
- Cloud edge: DNS, managed TLS, WAF/rate limiting if available.
- KPRA DMZ: reverse proxy / secure access gateway.
- Internal application VLAN: frontend/backend and background worker.
- Private database VLAN: PostgreSQL, no public route.
- Internal device VLAN: SenseFace terminal, reachable only through required restricted rules.
- Simulator is out of scope for v1.

Prefer an outbound-initiated secure tunnel or approved protected reverse-proxy arrangement between KPRA and the cloud edge. Do not expose the application server or database directly to the internet.

The application must be deployable with Docker Compose for the first release. Include health checks, persistent volumes, restart policies, secrets via environment variables/secret store, backup instructions, and rollback instructions.

## 5. RECOMMENDED STACK

Use a maintainable modular monolith:
- Frontend: Next.js + TypeScript.
- UI: Tailwind CSS + shadcn/ui or equivalent accessible component system.
- Backend: Python FastAPI.
- Database: PostgreSQL.
- ORM/migrations: SQLAlchemy 2 + Alembic.
- Authentication: secure server-side sessions, password hashing with Argon2id, TOTP MFA.
- Worker: simple background worker for integration/sync jobs; do not add Kafka or Kubernetes.
- Reverse proxy: Nginx.
- Deployment: Docker Compose.
- Tests: Pytest, API tests, Playwright end-to-end tests.

You may adjust versions and package choices based on current stable releases, but pin dependencies and document decisions. Avoid unnecessary microservices.

## 6. SECURITY REQUIREMENTS

- Never request, hardcode, log, commit, or expose production credentials.
- Create `.env.example` with placeholders only.
- Add `.env`, secrets, local DB files, and private exports to `.gitignore`.
- Passwords hashed with Argon2id or another current approved password hashing scheme.
- MFA for all privileged users; strongly recommend MFA for all managers.
- Secure, HttpOnly, SameSite cookies; CSRF protection for cookie-authenticated state-changing requests.
- TLS in transit.
- RBAC enforced on backend, not just frontend.
- Rate-limit login and attendance endpoints.
- Audit authentication, attendance, admin changes, corrections, exports, and sync operations.
- Avoid collecting precise GPS unless a documented policy requires it. If used, obtain notice/consent, minimize retention, and treat browser location as an untrusted signal rather than proof of identity.
- Do not collect or store face images, fingerprint images, or biometric templates.
- Restrict CORS to approved origins.
- Validate all inputs and use parameterized database access.
- Add security headers and safe error handling.
- Keep secrets out of logs and exception traces.
- Provide backup encryption and restore testing guidance.
- Make audit records append-only at application level; corrections create compensating records rather than erasing history.

## 7. UX REQUIREMENTS

Create a polished, uncluttered enterprise UI. Use a restrained navy/blue and white palette. The manager workflow must be usable in seconds.

Manager home screen:
- Greeting and employee identity.
- Today's date/time in PKT.
- Current status.
- One primary Check-In or Check-Out action.
- Today's event history.
- Recent attendance and monthly summary.
- Clear indication whether event is remote or physical.

Admin screen:
- Managers list and account status.
- Attendance overview.
- Filters by date, department, employee, and source.
- Correction requests.
- Device/integration status.
- CSV export.
- Audit trail.

Avoid decorative complexity, fake charts, placeholder buttons, and dead links. Every visible control must work or be clearly marked as not yet available.

## 8. DATA MODEL

Design and migrate a normalized schema at minimum:
- users
- employees
- departments (only if needed for initial scope)
- roles / permissions
- user_mfa
- attendance_events
- attendance_sessions or a safely derived session model
- correction_requests
- devices
- employee_device_mappings
- integration_connections
- sync_jobs
- audit_logs

Attendance event minimum fields:
- UUID
- employee_id
- event_type (CHECK_IN/CHECK_OUT)
- source (REMOTE_WEB/REMOTE_MOBILE/PHYSICAL_BIOMETRIC/ADMIN_CORRECTION)
- server_received_at_utc
- client_reported_at (optional diagnostic)
- device_id (nullable)
- external_event_id (nullable)
- idempotency_key
- status
- created_by
- metadata JSON with strictly controlled non-sensitive fields

Add unique constraints and indexes for employee/time/source/external event identifiers as appropriate. Use transactions to prevent race conditions.

## 9. REQUIRED PROJECT STRUCTURE

Create and maintain a clear structure similar to:

```
/
  CLAUDE.md
  MASTER_BUILD_PROMPT.md
  README.md
  .gitignore
  .env.example
  .cursor/rules/
    00-project.mdc
    10-security.mdc
    20-attendance-integrity.mdc
    30-zkteco-integration.mdc
  docs/
    REQUIREMENTS.md
    ARCHITECTURE.md
    SECURITY.md
    TIMENEX_FEASIBILITY.md
    SENSEFACE7_INTEGRATION.md
    DATA_MODEL.md
    DEPLOYMENT.md
    TEST_PLAN.md
    DECISIONS.md
    PROGRESS.md
  frontend/
  backend/
  deployment/
  tests/
```

You may refine the structure, but do not omit documentation or create unnecessary folders.

## 10. EXECUTION PLAN — WORK IN GATES

### Gate 0: Inspect and prepare
- Read all supplied instructions and docs.
- Inspect repository.
- Create any missing directories/files.
- Initialize Git if appropriate.
- Create `.gitignore` and `.env.example`.
- Do not overwrite existing user files without checking.
- Report the detected project state.

### Gate 1: Verify integration feasibility
- Read official SenseFace 7 Series product information and current manual.
- Confirm exact model/firmware and protocol mode from user-supplied information.
- Prepare a safe device-side configuration checklist without changing the live device.
- Investigate TimeNex supported integration methods using authorized access only.
- Create evidence-based feasibility report.
- Clearly separate verified facts from unknowns.
- Ask the user only for essential decisions that cannot be safely inferred.
- Do not start live device reconfiguration or production synchronization.

### Gate 2: Build the working standalone attendance MVP
Build and test:
- login/MFA;
- manager accounts;
- manager attendance page;
- Check-In/Check-Out;
- event receipt;
- attendance history;
- admin manager management;
- correction requests;
- audit log;
- CSV export;
- PWA installation.

The standalone remote attendance workflow must work without TimeNex or device connectivity.

### Gate 3: Add verified integrations
Only implement the SenseFace and TimeNex adapters after Gate 1 has established the supported interface. Keep adapters isolated from the core attendance workflow.

### Gate 4: Test and deploy
- unit tests;
- API integration tests;
- Playwright desktop/mobile tests;
- duplicate/race-condition tests;
- authorization tests;
- backup/restore test plan;
- container health checks;
- deployment and rollback guide.

## 11. ACCEPTANCE CRITERIA

Do not call the MVP complete until:
1. An authorized manager can log in from desktop and mobile.
2. A manager can mark only their own attendance.
3. The server creates the authoritative timestamp.
4. Duplicate check-in/out is prevented.
5. Attendance source is visible and accurately labelled.
6. An administrator can manage authorized manager accounts.
7. Corrections require a reason and approval.
8. Audit history is retained.
9. CSV export works and respects permissions.
10. PWA works on supported mobile browsers.
11. All tests pass.
12. Secrets are not present in the repository.
13. The app runs from documented Docker Compose commands.
14. Integration status is truthful and verifiable.
15. Test data cannot be confused with official production records.

## 12. AGENT WORKING RULES

- Do not ask the user to create directories or write instruction files; do it yourself.
- Do not stop after generating a plan or static mockup. Build working features.
- Work in small, testable increments.
- Run tests and fix failures before moving to the next gate.
- Do not invent vendor APIs, packet formats, device capabilities, TimeNex features, or KPRA attendance policy.
- Do not make production changes without explicit approval.
- Keep `docs/PROGRESS.md` updated.
- Record important decisions in `docs/DECISIONS.md`.
- At each gate, report completed work, tests, remaining blockers, and next action.
- If browser login is needed, ask the user to open/sign in through their own authorized browser session. Never ask them to paste a production password into chat or code.
- Do not claim production readiness until the acceptance criteria and deployment security review are complete.

## 13. START NOW

Begin at Gate 0, then Gate 1. Create the missing project files and folders automatically. Read the included documentation and official vendor materials. Produce the integration feasibility findings before touching production configuration.

Once the feasibility report is ready, proceed with the standalone MVP foundation and manager attendance workflow. Do not block the core MVP on TimeNex integration.
