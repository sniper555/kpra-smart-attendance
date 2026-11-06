# Architecture Decision Log

## ADR-001 — Lightweight manager-first scope
Decision: Build only authorized manager attendance first.
Reason: Short time to useful deployment and reduced operational risk.

## ADR-002 — Remote attendance is distinct from biometrics
Decision: Browser attendance uses REMOTE_WEB / REMOTE_MOBILE sources.
Reason: Preserve accuracy and auditability.

## ADR-003 — Standalone core
Decision: Core manager attendance does not depend on TimeNex or SenseFace availability.
Reason: Avoid integration outage blocking attendance.

## ADR-004 — Modular monolith
Decision: Next.js + FastAPI + PostgreSQL with isolated integration adapters.
Reason: Simple deployment and maintenance.

## ADR-005 — Hybrid hosting
Decision: Public HTTPS entry point, application/backend/database inside KPRA-controlled network.
Reason: Remote manager access while retaining data control.

## ADR-006 — Argon2id and hashed recovery codes
Decision: Passwords use Argon2id (time cost 2, memory 19 MiB, parallelism 1). Recovery codes are HMAC-SHA256 with the application secret as pepper. TOTP secrets are encrypted with Fernet before storage.
Reason: Passwords need a memory-hard hash. Recovery codes are high-entropy, so an HMAC is a hash that can be checked in constant time. TOTP secrets must be recoverable to verify codes, so they are encrypted rather than hashed.

## ADR-007 — Same-origin cookies via the reverse proxy
Decision: The browser talks to one origin. Nginx sends `/api` to FastAPI and pages to Next.js. Session cookies are HttpOnly and SameSite=Lax. State-changing requests send an `X-CSRF-Token` that is stored on the server session and returned to the signed-in page.
Reason: Cookie authentication stays same-site and does not depend on cross-origin CORS for the manager workflow.

## ADR-008 — Remote policy is off until an administrator records approval
Decision: Remote check-in and check-out require both an employee flag and the attendance-policy flag. The policy flag defaults to off. Turning it on requires the confirmation phrase `APPROVE REMOTE ATTENDANCE` and writes an audit row. Development seed data can turn it on once, and the interface labels that environment as development.
Reason: The product requires organizational authorization, and the core workflow must still be testable.

## ADR-009 — No ADMS listener and no TimeNex client in v1
Decision: After the Gate 1 review, device integration is a registry plus manual CSV import. TimeNex integration status stays unverified. Environment flags that claim either integration is enabled do not open sockets or mark records synced.
Reason: The vendor manual documents where a device points and names ZKTeco software ports, but it does not publish a push payload this project can implement honestly. No TimeNex API contract was found.

## ADR-010 — Effective time and server time are both stored
Decision: `server_received_at_utc` is when this server accepted the row. `effective_at_utc` is the attendance instant. For remote events they are equal and are set by the server. For an approved correction or a physical CSV row, effective time is the stated punch time and server time is the import or approval time. Client-reported time is diagnostic only.
Reason: Remote attendance must not trust the laptop clock. Physical and corrected punches still need the time they actually refer to, labelled as such.

## ADR-011 — Append-only attendance and audit tables
Decision: PostgreSQL triggers reject UPDATE and DELETE on `attendance_events` and `audit_logs`. Closing a session updates `attendance_sessions` only. A correction inserts new events.
Reason: Application code alone can be bypassed by a future bug. The trigger is the backstop.

## ADR-012 — Pin Next.js 15.5.28 and React 19.1.2
Decision: The frontend uses Next.js 15.5.28 and React 19.1.2, exact versions, with Tailwind CSS. Next.js 15.5.4 was replaced because that release is in the advisory range for the React Server Components issue.
Reason: Stay on the Next 15 line the project chose, on a patched release. `npm audit` still reports PostCSS and sharp issues nested inside Next.js. Forcing the suggested Next.js 16 upgrade was not taken in this pass.

## ADR-013 — Development email domain
Decision: Seed and test accounts use `@kpra.example`.
Reason: The installed email validator rejects the special-use `.local` suffix, so those addresses cannot sign in.

## ADR-014 — TimeNex stays a manual export path after the authorized session
Decision: The 3 November 2026 administrator session is evidence that TimeNex is the KPRA web tenant and that serial VDE2244500097 is registered there. This application still does not post attendance to TimeNex. CSV backup and the manual Mark Attendance screen are operator features, not an API.
Reason: Calling the private web form would write official production attendance without a contract for duplicates, source labels, or remote-versus-terminal events.

## ADR-015 — QA data stays inside this application
Decision: Synthetic employees and attendance pairs are created only by the development QA harness. Their source is `TEST_DATA`. The harness is disabled when `APP_ENV` is production. SenseFace and TimeNex adapters expose status only and reject ingest or push calls.
Reason: Calculations, duplicates, and monthly reports need sample rows. Those rows must stay distinguishable from official attendance and must not be sent to a terminal or to TimeNex.

## ADR-016 — TimeNex write waits for a contract
Decision: Employee mapping, passkey sign-in, and an attendance outbox are part of the application. The TimeNex adapter still sends nothing. A row is not shown as synchronized, and the Ali employee-20043 acceptance test is not passed.
Reason: The authorized TimeNex session showed a web application, CSV backup, and a manual mark screen. It did not show an API, token, or import contract. Turning on `TIMENEX_INTEGRATION_ENABLED` or filling a base URL does not invent that contract. WebAuthn stores a public key for sign-in. It is not a fingerprint or face template, and it is not a terminal punch.

## ADR-017 — TimeNex employee export is directory data
Decision: An administrator reviews a TimeNex employee CSV, then stores ID, name, department, and designation. The original ID is the local employee number and the verified TimeNex mapping. The ID is not a password. Accounts stay inactive. In, Out, and Status on that file are a snapshot and are not attendance. No attendance file is generated for TimeNex until an approved import format or API is verified.
Reason: The supplied export is a staff list with a one-day attendance snapshot. It is not a contract for writing new punches back to TimeNex.

## ADR-018 — ERP manual attendance was investigated and is not the architecture
Decision: The ERP manual attendance page was inspected and is not called. It is not the application's integration path. Superseded for architecture by ADR-019.
Reason: An authorized administrator saving that form was reported to appear later in TimeNex. The page is a signed-in browser post, not an application contract. A later decision keeps TimeNex as the destination and leaves the ERP page unused.

## ADR-019 — TimeNex is the attendance destination
Decision: TimeNex remains the final attendance destination. This application records attendance locally and queues it for a vendor-authorized TimeNex mechanism. It does not submit attendance through the KPRA ERP manual page. A local event is not marked synchronized until an independent authorized read confirms the same employee, date, and times in TimeNex.
Reason: The confirmed architecture uses the vendor path. The ERP form is a human workflow, and calling it would depend on a browser session this service must not reuse.

## ADR-020 — Passkeys across desktop, Android, and Apple devices
Decision: Sign-in and attendance verification use WebAuthn passkeys. The browser may offer Windows Hello, Android biometric authentication, Face ID, Touch ID, a cross-device passkey, or a compatible security key. The server stores the credential id, COSE public key, and signature counter. It does not collect or store fingerprint or facial templates, and it cannot tell a biometric check from a device PIN.
Reason: Those authenticators are what the browsers already expose through WebAuthn. Storing a template would be a different and unnecessary biometric system.

## ADR-021 — Remote attendance has no location gate
Decision: Employees may check in and check out away from KPRA offices. The application does not enforce office geofencing, office Wi-Fi, an office IP range, or GPS proximity. Location is not collected. A future optional location note requires a separate privacy approval and must not be described as proof of presence. A passkey does not prove physical location.
Reason: Remote attendance is an approved work mode. Browser location and a passkey are untrusted signals for where a person is.

## ADR-022 — Attendance can require a fresh passkey
Decision: Check-in and check-out require the current session to have completed WebAuthn user verification. The local application enforces that rule. An administrator can turn it off only by typing `ALLOW PASSWORD SESSION ATTENDANCE`, and can turn it back on with `REQUIRE PASSKEY FOR ATTENDANCE`. Automated tests keep the rule off inside the test database so existing password-session checks still run. A forged passkey response is rejected. The verification does not prove location. On localhost, the attendance prompt uses a passkey saved on this computer. A phone QR code cannot finish a localhost passkey.
Reason: Attendance needs a stronger check than a long-lived password session, and the check has to be switchable without inventing a biometric store.

## ADR-023 — Portal mark form is a day replacement, not a remote punch
Decision: The TimeNex Mark Attendance form posts one date with time in and time out, and the page says that save replaces the previous attendance for that date. A laptop check-in is not sent by impersonating SenseFace serial VDE2244500097 or by labeling the event as a face or fingerprint scan. The terminal ADMS address stays unchanged. Details are in `docs/TIMENEX_PORTAL_MARK_ATTENDANCE.md`. Calling the form is covered by ADR-024.
Reason: The form has no remote-source field. Spoofing the terminal would imitate a biometric event.

## ADR-024 — TimeNex day replacement is explicit
Decision: The official Mark Attendance form is called only after a local check-out, from the Sync button, or when an administrator ticks “Also push this day to TimeNex” on a verified mapping. Check-in alone does not call it. A personal Sync whose local date is not today, or whose day already exists in TimeNex, waits for “Overwrite TimeNex day with these times”. The administrator checkbox does not ask again: the times on the form are posted and replace that employee’s TimeNex day, including a device punch or an earlier mark. The checkbox stays off until it is ticked. The punch row stays append-only. Only the TimeNex status, reference, error, and push time on that row may change. A rejected portal response keeps the local correction and stores the exact error.
Reason: The form replaces a whole official day. The person saving it has to choose that replacement. The local attendance time is not rewritten when the portal answers.

## Pending
- Confirm KPRA-approved remote attendance policy (the in-app switch is the local record of that decision; it is not a substitute for KPRA's own authorization).
- The Mark Attendance form replaces a whole day. It is called only for a finished or explicitly confirmed day, as in ADR-024. A live portal response for employee 22063 has not been captured yet. The ERP manual page is not a substitute.
- Confirm SenseFace 7A firmware build, T&A versus A&C mode, and who operates 173.208.167.249:80.
- Confirm schedules, grace periods, correction approvers, and retention. Late/early inference stays off until those are entered.
