# TimeNex Integration Feasibility

Status: AUTHORIZED SESSION REVIEWED. NO WRITE API. The current requirements are in `docs/TIMENEX_INTEGRATION_REQUIREMENTS.md`. The ERP manual page is not the integration path.

Outcome for this release: D. The attendance application runs on its own. CSV export is available to an administrator. Nothing is labelled synced. An authorized administrator session on 3 November 2026 confirmed the KPRA tenant is a web application. It was not used to create or change attendance.

Portal referenced by the project brief: https://www.timenex.net:8443/

## What was checked

Public web search on 3 November 2026 for TimeNex API, webhook, and import/export documentation, including `site:timenex.net`. No public API reference, webhook contract, or authorized import specification was found for this portal.

Unrelated products that must not be treated as evidence:

- TimeTrex (timetrex.com) has its own API. It is a different product.
- App store listings for other applications named TimeNex describe generic HR apps and are not evidence about https://www.timenex.net:8443/.

A business directory describes a Peshawar "Timenex" biometric attendance system as devices plus an application server and database reached over VPN. That is a marketing description, not an API contract, and it was not confirmed against the KPRA tenant.

An authorized administrator later signed in through the browser. The password is not stored in this repository and should be rotated in TimeNex.

## Verified in the authorized KPRA session

The signed-in tenant is titled Khyber Pakhtunkhwa Revenue Authority. The account menu says Welcome Admin. These are observations, not a licence to change records.

- About 190 employees are on the executive dashboard. On that view, 72 were present, 93 absent, and 2 employees had no time category. Devices showed 4 online and 1 offline. Last sync on the dashboard was about one hour earlier. That sync is TimeNex's own device sync, not a sync from this application.
- Device list includes serial VDE2244500097 with the title KPRA Head office. That is the photographed SenseFace 7A. Other listed serials are BRWU181560139 (KPRA head office), A8JY180360205 (Regional Office Abbottabad, listed twice), BRWU184360207 (Mardan), and TTQ5242500311 (Bannu).
- Manual attendance exists at `/marks_attendance`. It searches by name, ID, or CNIC and has time-in and time-out controls. Using it writes official TimeNex attendance. This application does not call that screen.
- A CSV backup action exists at `/client-backup`. Time categories, reports, and payroll screens can also export CSV, Excel, or PDF from the browser. That is an operator export, not an API.
- The head-office time category shown is titled Morning Shift Non.Roster, in 10:00, out 5:00, grace 00:00. Regional categories use 09:00 and 05:00 with grace around 09:15 to 09:30. TimeNex itself warns that times must be 24-hour values and that AM/PM text must not be used. Because the stored out time is ambiguous, this application still does not calculate late or early.
- No API, webhook, or token screen was present in the visited menus. The password that was typed into the browser for this review must be rotated in TimeNex. It is not stored in this repository.

## Decision

| Path | Result |
| --- | --- |
| Supported write API | Not verified. Not implemented. |
| Supported import | Not verified. Not implemented. |
| Read-only export | Browser CSV, Excel, and PDF actions exist. They are not an API and are not called by this app. |
| Standalone plus CSV | This release. |

`TIMENEX_INTEGRATION_ENABLED=true` does not transmit data. The worker records status `MISCONFIGURED` and states that no request was sent.

The administrator screen shows TimeNex as not synced. There is no control that marks records synchronized.

## What would change the decision

A written API or import contract that states authentication, the attendance write or export operation, employee identifier rules, duplicate handling, and how a remote browser event is distinguished from a terminal event. The web session did not provide that contract. Remote attendance stays in this database only.

## Questions for the write integration

Checked again on 3 November 2026 from the authorized session notes and the earlier public search. No new vendor contract was supplied.

| Question | Finding |
| --- | --- |
| Official attendance write API? | Not found. No API, webhook, or token screen was in the visited menus. |
| Remote attendance transactions? | Not documented. The manual mark screen writes official attendance and is not called by this application. |
| Employee ID mapping? | TimeNex searches by name, ID, or CNIC on that screen. This application now stores an administrator-verified local mapping. The ID is not authentication. |
| Check-in and check-out submission? | No supported submission operation was found. |
| Attendance imports? | Checked again in the signed-in portal on 4 Nov 2026. Dashboard backup, Attendance Status Report, Monthly Detailed Attendance, Employees List, and Mark Attendance were opened. Reports and the backup export CSV, Excel, or PDF. Mark Attendance searches one person by name, ID, or CNIC. No import template, file upload, or API token was on those screens. No attendance was submitted. |
| Physical versus application-generated attendance? | Not specified by a vendor contract. This application keeps `REMOTE_WEB`, `REMOTE_MOBILE`, `PHYSICAL_BIOMETRIC`, and `ADMIN_CORRECTION` separate and does not label a browser event as a fingerprint or face scan. |
| Vendor integration documentation? | Not found for https://www.timenex.net:8443/. |

The connector is configurable through `TIMENEX_WRITE_MODE`, `TIMENEX_BASE_URL`, and `TIMENEX_API_TOKEN`. Those values do not transmit data. A verified client can be added when KPRA obtains the contract. Until TimeNex returns a reference, the outbox stays unconfirmed.

The acceptance example — Ali, employee 20043, checks in here and the same attendance appears in TimeNex — is not passed.

## Unknowns

- Product version and vendor of the KPRA tenant.
- Whether TimeNex has a separate, approved format for importing new attendance. The employee export is not that format.
- Whether 173.208.167.249 is a TimeNex ADMS endpoint.
- Employee number / PIN mapping inside TimeNex.
- Whether TimeNex would accept a remote-source event at all.
