# KPRA Smart Attendance Lite

Remote attendance for authorized managers. A browser check-in is remote attendance. It is never stored or shown as a fingerprint or face scan.

The app runs without TimeNex and without the SenseFace terminal. Those integrations are documented and intentionally not connected. See `docs/TIMENEX_FEASIBILITY.md` and `docs/SENSEFACE7_INTEGRATION.md`.

This repository is a working development build. It is not a production deployment. Do not point the live SenseFace terminal at it, and do not treat development records as official attendance.

## Local use

Copy `.env.example` to `.env` and set the passwords and `APP_SECRET_KEY`. The local `.env` is gitignored.

Development accounts, after `SEED_DEV_DATA=true`:

- `manager@kpra.example` — test manager, employee `TEST-0001`. The password is `SEED_MANAGER_PASSWORD`.
- `admin@kpra.example` — administrator. The first password is `BOOTSTRAP_ADMIN_PASSWORD`, and the first sign-in requires a new password and MFA.

Both accounts enroll TOTP on first sign-in. Recovery codes are shown once. The login page names the development accounts only when `APP_ENV=development`.

Start PostgreSQL, the API, and the web app:

```powershell
docker run -d --name kpra-attendance-db -e POSTGRES_USER=attendance -e POSTGRES_PASSWORD=<same value as POSTGRES_PASSWORD in .env> -e POSTGRES_DB=attendance -p 127.0.0.1:5432:5432 postgres:16
cd backend
..\.venv\Scripts\python.exe -m app.entrypoint api
cd ..\frontend
$env:API_PROXY_TARGET = "http://127.0.0.1:8000"
npm install
npm run dev
```

Open `http://127.0.0.1:3000`. If something else already uses port 8000, start the API with `uvicorn app.main:app --host 127.0.0.1 --port 8010` and point `API_PROXY_TARGET` at that port. Migrations and the development seed run from `python -m app.entrypoint api`.

## Tests

```powershell
.\backend\.venv\Scripts\python.exe -m pytest -q
```

Pytest uses database `attendance_test` on the same PostgreSQL instance. It does not use the development accounts.

## Docker Compose

From the repository root, with `.env` filled in and port 5432 free:

```powershell
docker compose --env-file .env -f deployment/docker-compose.yml up --build
```

The published ports are `127.0.0.1:8080` for the site and `127.0.0.1:5432` for PostgreSQL. They are not bound on all interfaces. Deployment, backup, and rollback notes are in `docs/DEPLOYMENT.md`.

## Scope that is deliberately absent

- No ADMS, TA Push, or TCP 4370 listener.
- No TimeNex client, and no screen that says a record is synced.
- No biometric images or templates.
- No late or early calculation until an authorized schedule is configured and inference is turned on.
