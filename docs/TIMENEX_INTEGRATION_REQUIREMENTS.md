# TimeNex integration requirements

Status: 4 Nov 2026. No vendor write contract is available. This application records attendance and queues it. It does not send attendance to TimeNex, and it does not send attendance through the KPRA ERP manual page.

TimeNex at https://www.timenex.net:8443/ is the final attendance destination. A row saved here is not a TimeNex transaction. Synchronization is confirmed only after an authorized read shows the same employee, date, and times in TimeNex.

## What was verified

An authorized administrator session opened these screens. Nothing was submitted.

| Screen | What it does | What it is not |
| --- | --- | --- |
| Executive dashboard | Shows employees, present, absent, late, and device sync | An API, and not a sync from this application |
| Generate CSV Backup (`/client-backup`) | Browser export | An import, and it was not generated |
| Attendance Status Report (`/report`) | Builds a report for a date | A file upload |
| Monthly Detailed Attendance | Exports CSV, Excel, or PDF | A file upload |
| Employees List (`/users`) | Searches by ID, name, CNIC, file number, gender, or BPS | A write API |
| Mark Attendance (`/marks_attendance`) | Searches one person and can record time in and time out | Called by this application |

No API token, webhook, or import template was present. Words that looked like "import" in the page source were stylesheet syntax.

The employee export used earlier has columns ID, Name, Department, and Designation. In, Out, and Status on that file are a one-day snapshot. That file is directory data. It is not an attendance import format. The ID is not a password.

The ERP page at `/attendance/manual` was inspected separately. It is a signed-in browser form. It is not this application's architecture.

## What this application already does without vendor access

- Stores check-in and check-out with the server clock, shown in Asia/Karachi.
- Labels a browser event as remote web or remote mobile. It does not label that event as a fingerprint or face scan.
- Keeps an append-only audit history, idempotency keys, and one open session per employee.
- Lets an administrator approve remote attendance per person and for the organization.
- Maps a local employee to a TimeNex employee ID after review. The mapping is not confirmation.
- Queues a verified employee's event as awaiting a contract. The adapter opens no socket.
- Marks a row confirmed only when the status is confirmed and a TimeNex reference is stored. Nothing in the current adapter can produce that reference.
- Accepts passkeys from Windows Hello, Android, Apple Face ID or Touch ID, another device, or a security key. It stores a public key, not a biometric template.
- Does not check GPS, office Wi-Fi, or an office IP address.

## What the TimeNex vendor or KPRA operator must supply

All of the following are required before this application can submit attendance. A browser cookie from an administrator session is not a substitute.

1. The product and version of the KPRA tenant, and the vendor's written integration document for that version.
2. An application credential: who issues it, how it is rotated, and which network may use it. It must not be a person's TimeNex password.
3. The write operation: method, URL, body, and the field that distinguishes a remote browser event from a terminal punch. This application will keep sending `REMOTE_WEB` or `REMOTE_MOBILE`. It will not imitate a SenseFace event.
4. The employee key the write expects. The sheet ID, the ERP option value, a CNIC, and a file number have not been shown to be interchangeable.
5. How check-in and check-out are represented: one row with in and out, or two events. How a missing checkout is left missing. How a second submission for the same employee and date is rejected or replaced.
6. The response field that means TimeNex accepted the row, and the reference this application must store.
7. An authorized read, with the same credential or a read-only credential, that returns the employee, date, in time, and out time. Confirmation uses that read. The write response alone is not enough.
8. The timezone of stored times. This application stores UTC and displays Asia/Karachi. TimeNex screens have shown both 12-hour text and a warning that times must be 24-hour values.
9. Whether a remote event is accepted at all. If TimeNex only accepts terminal punches, that limit has to be stated. This application will not relabel a browser event to get around it.
10. A non-production tenant, or a written test window, so the first submission is not a live payroll record.

## What stays unchanged until that contract exists

`TIMENEX_INTEGRATION_ENABLED`, `TIMENEX_WRITE_MODE`, `TIMENEX_BASE_URL`, and `TIMENEX_API_TOKEN` do not transmit attendance. The SenseFace terminal remains on its current ADMS address. No protocol packets are invented.

The acceptance example — a named employee checks in here and the same attendance appears in TimeNex — is not passed.
