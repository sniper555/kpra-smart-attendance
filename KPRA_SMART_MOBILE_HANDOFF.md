# KPRA Smart Attendance — handoff for the mobile folder

This file describes the working web application in this folder as of 6 Nov 2026. Copy it into the new `kpra smart mobile` folder and treat it as the source of truth for what already exists. Build the phone app as a client of this service. Do not start a second attendance database, and do not invent a second way to write TimeNex.

The web app is a local development build. It is not a production deployment. Records in it are not official KPRA production attendance.

## What this application is

A manager attendance app for authorized people. A person checks in and checks out in a browser. An administrator can also type one employee's day and send that day to TimeNex.

Stack:

- Web: Next.js 15.5.28, React 19.1.2, Tailwind. Dev server `http://localhost:3000`.
- API: FastAPI, SQLAlchemy 2, Alembic. Dev server `http://127.0.0.1:8010`. The Next server proxies `/api` to that API. `frontend/next.config.mjs` sets `experimental.proxyTimeout` to 180000 ms because a TimeNex save can take longer than 30 seconds.
- Database: PostgreSQL 16 in container `kpra-attendance-db`, published only on `127.0.0.1:5432`, database `attendance`. Tests use `attendance_test`.
- Time zone: the server stores UTC. The screens show Asia/Karachi.

Local accounts, from the development seed:

- `admin@kpra.example` — administrator. Password comes from `BOOTSTRAP_ADMIN_PASSWORD` in `.env`. The first sign-in forces a new password and MFA. This login is mapped to TimeNex employee **22063**.
- `manager@kpra.example` — manager, employee `TEST-0001`. Password comes from `SEED_MANAGER_PASSWORD` in `.env`.

Do not copy `.env` into the mobile folder, and do not put passwords, TimeNex credentials, session cookies, or CSRF tokens into source, docs, or chat.

## Screens that already work

Signed-in home (`/home`):

- Large Check-In or Check-Out button. The saved time is the server clock, shown in PKT.
- Source label is “Remote attendance · web browser” on a desktop browser, and remote mobile when the browser sends a mobile user agent.
- Sync to TimeNex. Check-in alone is not sent. Sync sends the finished day. If the date is not today, or TimeNex already has that day, the screen asks “Overwrite TimeNex day with these times” before it posts.
- TimeNex link: mapping status, how the person signed in, and whether TimeNex confirmed the punch.
- The open session on the administrator account is still the 3 Nov 2026 12:21:27 PKT remote-web check-in. Working hours on that card keep growing until that session is checked out.

History (`/history`): the person's own punches and a Sync action.

Corrections: a person can request a correction. An administrator approves or rejects it with a reason. Old punches are not deleted.

Account (`/account`): password change and “Add a passkey”.

Administration (`/admin`) is only in the left menu, and that menu is hidden on a narrow window. The bottom bar on a phone-sized window is Home, History, Corrections, Profile. It has no Administration item. Mark attendance is the second button on the Administration page, or the direct address `http://localhost:3000/admin?tab=mark`.

Administration → Mark attendance:

- Pick an employee, a date, an in time, and an optional out time.
- The checkbox “Also push this day to TimeNex” is off until the administrator ticks it.
- The warning says the save overwrites the entire day in TimeNex, including a device punch or a previous mark, and cannot be undone.
- Success text is exactly “Day replaced in TimeNex”.
- A portal error is shown as the portal wrote it, with status failed.
- An in time with a blank out time is allowed. TimeNex still keeps any separate terminal punch on that date, so the daily Out column can show that terminal time.
- There is no Clear / Delete / Mark Absent control. The TimeNex pages that were opened have no request that blanks a day to Absent. Do not invent one.

Other Administration areas already built: accounts, today's board, monthly sheet, CSV export, audit, office timings, TimeNex employee mapping, device registry. The device screen says the terminal is not connected. Late and early status stay off.

## How a punch gets into TimeNex

The write path is the TimeNex website form, over HTTPS, from the API process. It is not ADMS, not ZKTeco, not TCP 4370, and not the SenseFace terminal.

Client: `backend/app/services/timenex_portal.py`, class `TimenexPortalClient`.

1. Sign in to `https://www.timenex.net:8443` with `TIMENEX_USERNAME` and `TIMENEX_PASSWORD` from `.env`.
2. Open `GET /marks_attendance/mark_attendance/{employee_id}`.
3. Post `POST /marks_attendance/markattendance` with `_token`, `id`, `attendance_date` (`YYYY-MM-DD`), `time_in` (`HH:MM:SS`), `time_out` (always sent; empty string when there is no out time), and `ack=on`.

`ack=on` is sent because that form replaces the day. A successful response with no danger alert is stored as reference `portal:{employee_id}:{YYYY-MM-DD}`.

Who may call it:

- A personal Check-Out, for a verified TimeNex mapping.
- Home → Sync to TimeNex, with the extra confirm when the day is not today or TimeNex already has it.
- Administration → Mark attendance, only when the checkbox is ticked. That path calls `push_correction(..., use_form_out=True)` in `backend/app/services/timenex_push.py`.

The old TimeNex adapter still sends nothing. Do not turn `TIMENEX_INTEGRATION_ENABLED` into a transmitter. The ERP manual page is not called.

A verified mapping is required. The employee number in this app is not a TimeNex login. Gohar Ali is employee **22061**, active, with a verified TimeNex id **22061**. The administrator account writes **22063** if the person uses Home → Check-In on that login.

## The asterisk

A time saved by this form is stored in TimeNex with **Device ID: N/A**. The portal shows the manual asterisk beside that time. A punch from the physical terminal is stored with device serial **VDE2244500097** and has no asterisk.

On 4 Nov 2026, Gohar Ali's punch list showed both:

| Time | TimeNex label | Origin |
| --- | --- | --- |
| 06:00:00 | Device ID: N/A | Typed through the website form, including a save from this tool |
| 10:33:58, 17:20:58, 17:29:14 | Device ID: VDE2244500097 | The SenseFace terminal |

The mobile app must keep that difference. It must not send an ADMS attendance log, must not reuse the terminal serial, and must not label a phone tap as a fingerprint or a face scan. Hiding the asterisk means pretending the terminal sent the punch. That is out of scope for the mobile folder.

The terminal's ADMS address, `173.208.167.249` port 80, stays unchanged. Do not reconfigure the device.

## Why the phone cannot use this folder today

The site and the API listen on this computer only (`localhost` / `127.0.0.1`). A phone cannot open that address.

Check-in and check-out require a passkey when the live policy `passkey_required_for_attendance` is on. The passkey already enrolled is “Passkey for localhost” through Windows Hello on this computer. A phone cannot finish a passkey for `localhost`. An IP address such as `http://192.168.x.x` is not a secure context, so the browser will not create a passkey there either.

A phone can check in only when both the pages and this API are on one HTTPS name the phone can open, inside KPRA's own network. The person then signs in on the phone and adds a new passkey on that phone. The localhost passkey does not move to the new name. Netlify, or any host that serves only the pages, cannot do this: the TimeNex password and the database stay on the API.

Passkeys store a public key and a signature counter. They do not store a fingerprint or a face image. A passkey does not prove the person is in the office. This app does not read GPS, office Wi-Fi, or office IP.

## Attendance rules the mobile client must keep

- Sources stay `REMOTE_WEB`, `REMOTE_MOBILE`, `PHYSICAL_BIOMETRIC`, and `ADMIN_CORRECTION`. A phone tap is `REMOTE_MOBILE`.
- Server time is authoritative for a live check-in and check-out. The phone clock is not the punch time.
- One open session per employee. A check-out needs an open session. Duplicate check-ins and check-outs are rejected.
- Corrections need a reason and an administrator. History is append-only. Do not update or delete `attendance_events` except the TimeNex status fields allowed by migration 0008.
- Administrator Mark attendance may replace a day, including an in-time-only day, and may park another open session so the unique open-session index still holds. Personal Check-In and Check-Out do not get that override.
- Do not collect biometric templates or face or fingerprint images.
- Do not log passwords, tokens, session cookies, or biometric data.

## What the mobile folder should build

A phone client of this existing API, aimed at a manager who does not want to carry the laptop.

The phone needs:

1. Sign-in against this API.
2. A passkey enrolled on the phone, after the API is served on an HTTPS name.
3. Check-In and Check-Out, labelled remote mobile, using the server time.
4. Sync to TimeNex for that person's verified employee id, with the same overwrite confirm the website uses.
5. A clear result: saved here, and either confirmed by TimeNex or the exact portal error.
6. History for that person.

An administrator day-entry screen, if it is included, is the same form as `/admin?tab=mark`: employee, date, in time, optional out time, checkbox off by default, success text “Day replaced in TimeNex”. Put it where a thumb can reach it. The current bottom bar does not.

Leave these on the existing server: PostgreSQL, the portal client, mappings, audit, and the TimeNex password. The phone holds none of those secrets.

## What was tried and should not be repeated

- Posting an empty `new_attendance` to TimeNex `POST /update_attendance_record` does not delete a punch. It stored a device punch as `00:00:11` because that endpoint rewrites the seconds. Do not call it to clear a day.
- An in-time-only portal save does not remove a terminal punch. The daily report uses the earliest punch as In and the latest punch as Out. A later terminal punch stays in the Out column.
- Several live saves were already made for Gohar Ali **22061**, including 4 Nov and 5 Nov 2026. Do not send more experimental writes while building the phone client.
- `README.md` still says there is no TimeNex client. That sentence is stale. The portal client above is the writer. `docs/PROGRESS.md` and `docs/DECISIONS.md` ADR-023 and ADR-024 are the decisions to follow.

## Run this web app while the phone client is developed

PostgreSQL container `kpra-attendance-db` must be running. Then, from this folder:

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

In a second terminal:

```powershell
cd frontend
$env:API_PROXY_TARGET = "http://127.0.0.1:8010"
npm run dev -- --hostname 127.0.0.1 --port 3000
```

Open `http://localhost:3000`. Passkeys fail on `http://127.0.0.1:3000`.

Tests, from `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api.py -q
```

The last full run after the open-session work was 41 passed and 1 skipped. The skipped test is the live portal login, and it stays skipped unless `TIMENEX_PORTAL_MANUAL=1`.
