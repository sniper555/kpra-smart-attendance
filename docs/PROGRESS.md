# Project Progress

Update this file at the end of each meaningful work session.

## Mobile handoff
On 6 Nov 2026, `KPRA_SMART_MOBILE_HANDOFF.md` was written for a separate phone-client folder. It describes this web app, the TimeNex mark-form writer, and the rule that a phone tap stays a manual TimeNex entry with Device ID N/A. The phone cannot use localhost. No mobile client was added in this folder.

## Current stage
Standalone manager and administrator attendance works in local development. Identity mapping, passkey sign-in, and a TimeNex outbox are in place. TimeNex is the attendance destination. The ERP manual page is not the integration path and is not called. Remote attendance has no GPS, office Wi-Fi, or office IP check. A passkey does not prove location. Check-in and check-out in this database require a passkey with user verification. An administrator can change that only with the confirmation phrase. TimeNex has not confirmed any attendance. SenseFace stays disconnected. This is not production-ready. Acceptance tests 1 through 8 are not passed.

## Gates
- [x] Gate 0 — Repository inspection and scaffolding
- [x] Gate 1 — TimeNex and SenseFace feasibility (public sources only; no live device change)
- [x] Gate 2 — Standalone manager attendance MVP, exercised in the browser
- [ ] Gate 3 — Verified integrations. Blocked: no supported TimeNex write or import contract, and no verified SenseFace push payload. The outbox queues local attendance and the adapter sends nothing. Authorized employee CSV import and physical attendance CSV import are in place.
- [ ] Gate 4 — API tests pass and the Compose images build. Playwright is not written. Compose was not started because PostgreSQL is already bound on 127.0.0.1:5432 by the local database container.

## What was verified on 5 Nov 2026
- The signed-in TimeNex portal has no request that clears one employee’s day. Mark Attendance only has Cancel and Save. The individual report can add or change a punch. A clear-day checkbox was not added, because there is no portal request to copy.
- Administration → Mark attendance can replace a TimeNex day with only an in time even when that employee already has an open session. Personal check-in and check-out still require the employee’s own open session.
- Administration → Mark attendance waits for the TimeNex reply. A slow portal answer is no longer shown as “Attendance was not recorded.” The button stays disabled until that reply arrives.
- Administration → Mark attendance can replace a day that already exists. With the checkbox ticked, the times on the form are sent to the TimeNex mark form and overwrite that employee and date. The success message is “Day replaced in TimeNex”. The checkbox stays off until the administrator ticks it. Personal check-in and check-out sync is unchanged.
- Administration → Mark attendance accepts a blank out time. A push with only an in time is stored as “In only – Out still open”. Submitting that same day again with an out time closes the local day and sends both times to TimeNex. The out time field is a 24-hour HH:MM box so it can stay empty.
- Administration → Mark attendance for TimeNex ID 22061 on 5 Nov 2026 sent 09:00:00 and 17:00:00 through the portal form. The TimeNex attendance report for that date shows 09:00:00 AM and 17:00:00 PM for that employee, status present. The local rows are administrator corrections with TimeNex status confirmed. The checkbox stays off until it is ticked. A second submit of the same times was rejected as an overlap.
- Administration → Mark attendance keeps the correction local. For a verified TimeNex link, a checkbox can also send that day through the portal form. The checkbox says the send overwrites the day in TimeNex. A rejected portal response leaves the local correction and shows the error. Dashboard Sync uses an open session from an earlier day, and asks “Force sync this day” before overwriting when that date is not today or TimeNex already has the day. `pytest`: 38 passed, 1 live test skipped.
- The TimeNex portal client can sign in and open the mark form for TimeNex ID 22063. Save was not clicked during that check. Check-out of a verified mapped employee queues a day replacement with the local time in and time out. A check-in by itself is not sent. The manual sync endpoint is `POST /api/attendance/sync-to-timenex/{attendance_id}`.
- The TimeNex Mark Attendance screens were opened with the portal account. Save was not clicked. Search by TimeNex ID `22091` opens `GET /marks_attendance/mark_attendance/22091`. Save is `POST /marks_attendance/markattendance` with `_token`, `id`, `attendance_date`, `time_in`, optional `time_out`, and an `ack` checkbox. The form says the save replaces that date. No attendance was submitted, and the local app still does not call the form. The SenseFace serial was not used to push an attendance log. See `docs/TIMENEX_PORTAL_MARK_ATTENDANCE.md`.
- The local administrator's Windows Hello passkey is enrolled. Windows Security offered "Passkey for localhost" for that account. The sign count is still 0, so the passkey has not authenticated a session or a punch. No fingerprint or face template is stored.
- The open attendance row is still the 03 Nov 2026 12:21:27 PKT check-in for TEST-ADMIN. Source is remote web. The authentication method is empty. TimeNex status is not submitted, and there is no TimeNex reference. It is a test record and it is not mapped. No passkey check-in or check-out was completed. A Windows Hello PIN prompt was shown and was not answered here.
- The signed-in TimeNex monthly report screen still offers Generate Report, Print, and export to CSV, Excel, and PDF. The menu still has one-person Mark Attendance. No import, token, or API control was used, and no attendance was submitted. A public search did not find a write contract for this tenant. `pytest`: 33 passed.

## What was verified on 4 Nov 2026
- The local administrator saved a Windows Hello passkey. The public key and sign count are stored. No fingerprint or face template is stored. On localhost, check-in and check-out offer only the passkey saved on this computer, so the phone QR code is not used. The attendance challenge purpose is stored. TimeNex stays unconfirmed. `pytest`: 33 passed, including `test_localhost_attendance_prompt_uses_this_computer`.
- TimeNex is the attendance destination. The ERP manual page is documented as an investigation and is not called. Remote attendance does not read GPS or restrict office network location. A password session is rejected when the passkey attendance rule is on, and a WebAuthn session is accepted. A forged passkey and a latitude field are rejected. The saved event records the browser class and that location was not checked. The local database requires a passkey for check-in and check-out. The test database keeps that rule off.
- The sheet ID 22091 is stored as a TimeNex ID only. The ERP internal key is a separate field. A check-in with that ID is saved here and is not sent to the ERP page. An ERP-accepted result in the status machine still leaves TimeNex unconfirmed. `pytest`: 31 passed.
- Passkey registration no longer forces a built-in authenticator, so a browser can offer a phone. User verification is still required. An administrator can revoke the passkeys on an account. `pytest`: 30 passed.

## What was verified on 3 Nov 2026
- The supplied TimeNex file was read in full as a combined directory and one-day attendance report: 186 employees, 65 present, 109 absent, 12 late, 76 missing checkouts, and 1 recorded departure. All 186 IDs match the stored directory. No date was in the file, and no missing departure was filled in. Analysis files are in the Downloads attendance-analysis folder. The signed-in TimeNex portal has report export and one-person mark attendance, and no attendance import control. Nothing was uploaded.
- An authorized TimeNex employee export can be reviewed before it is stored. The supplied file had 186 unique IDs, no duplicate IDs, and 5 rows with no department. Those rows are stored as Unassigned. In, Out, and Status were not copied as attendance. The 186 accounts stay inactive, the original IDs are the TimeNex links, and a second review leaves them unchanged. Nothing was sent to TimeNex. `pytest`: 29 passed.
- A rejected TimeNex row has a Replace action. It saves a new pending ID and leaves the earlier rejection in the audit history.
- A rejected TimeNex link can be replaced with a new pending ID. The earlier rejection stays in the audit history, and the new link is not a TimeNex confirmation.
- The attendance report counts TimeNex-confirmed check-ins for the selected dates. That count stays zero.
- The home month summary and the week chart count TimeNex-confirmed check-ins separately. That count stays zero.
- The signed-in person's profile shows the same browser check-in readiness and TimeNex link. Confirmation stays no.
- Employee Management shows whether each person can check in from a browser. Ready still leaves TimeNex confirmation false.
- The device screen says the terminal is not connected and each integration has sent no request. Confirmed attendance from those connections stays no.
- Recent activity on the home screen shows the current TimeNex link and whether that punch was confirmed.
- Mark attendance shows the selected person's TimeNex link before saving. The saved row is an administrator correction and stays unconfirmed.
- A correction request says an approval is an administrator correction saved here. The request shows the TimeNex link and stays unconfirmed.
- A person's own history shows the current TimeNex link beside whether each punch was confirmed. A verified link still leaves confirmation false.
- Attendance CSV exports include the TimeNex link and a plain confirmation column. A verified link is not written as confirmed.
- Today's board and the monthly sheet show each person's TimeNex link beside the confirmation count. A verified link still leaves the confirmation count at zero.
- The home screen shows the TimeNex link as pending, verified, rejected, or not mapped before check-in. Confirmation stays no until TimeNex accepts a record.
- The audit history spells out TimeNex mapping decisions. A pending mapping names the TimeNex ID and does not say the record was synced.
- Employee Management lists each TimeNex mapping as pending, verified, rejected, or not mapped. The employee ID still does not sign anyone in.
- `pytest`: 27 passed. Today's board and the monthly sheet now include the sign-in method and a TimeNex confirmation count. That count stays zero. Remote rows are still not labelled as a fingerprint.
- `pytest`: 27 passed. An authorized mapping file verifies a local employee and still sends nothing. The administrator queue reports zero TimeNex confirmations.
- `pytest`: 26 passed. A verified TimeNex employee mapping queues check-out as awaiting a contract. The adapter sends nothing, and no row is marked confirmed. Passkey ceremony options store no biometric template. Forged passkey responses are rejected.
- `pytest`: 22 passed. Administration → Attendance includes a monthly sheet of days with a check-in. A zero is not labelled absent.
- `pytest`: 21 passed. Office timings can be saved as Monday–Friday, 09:00–17:00 PKT, with 15 minutes of grace. That save leaves late and early status off and does not change the remote-attendance switch.
- `pytest`: 20 passed. History & Reports exports the signed-in person's own CSV. Administration → Attendance shows today's board: checked in, checked out, and no record today, each with its source label.
- Administration has an employee directory with search and edit. A browser check filtered to `QA-D7D1F980` and saved the designation QA reviewer.
- QA harness writes `TEST_DATA` rows only, and only outside production. In the browser, the development admin page created one closed pair for employee `QA-D7D1F980` and reported that nothing was sent to TimeNex or a terminal. A second harness submit in the same check created another pair. Employee CSV import rejects duplicate numbers and emails.
- Browser, desktop: manager sign-in, TOTP enrollment, recovery codes, check-in, check-out, receipt with event ID, server time in PKT, source “Remote attendance · web browser”, history, correction request.
- Browser, phone-sized viewport: home stacks the check-in action. The desktop browser user agent still labels the event as web, which is accurate for that session.
- Browser, administrator: forced password change, MFA enrollment, account list, device `NOT_CONNECTED` with receiving events off, TimeNex not synced, correction approval with a reason, attendance rows labelled “Administrator correction” and “TEST RECORD”, CSV export audited as `ATTENDANCE_EXPORTED`.
- PWA files respond: `/manifest.webmanifest`, `/icon-192.png`, `/icon-512.png`, `/sw.js`. Installing the PWA on a phone was not done from this machine.

## Blockers
- TimeNex has a web tenant and CSV backup. No API contract was found, so this app still does not send attendance there.
- SenseFace 7A firmware build, T&A versus A&C mode, and the operator of 173.208.167.249:80 are unconfirmed. The terminal was not reconfigured.
- KPRA has not recorded an organizational remote-attendance approval. The development database enabled the policy once and labels the environment as development.
- Schedules and grace periods are unset, so late and early status stays off.

## Latest verified facts
- User supplied device photographs identify SenseFace 7A, serial VDE2244500097, ADMS server 173.208.167.249 port 80.
- Official SenseFace 7 Series material lists ADMS and AC Push/TA Push switching. The manual’s TCP 4370 is the device COMM port, separate from the observed ADMS port 80.
- No public TimeNex API, webhook, or import contract was found.
