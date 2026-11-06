# Deployment Plan

Initial deployment: Docker Compose on KPRA-controlled infrastructure.

## Components
- Nginx reverse proxy in DMZ or approved edge tier.
- Next.js frontend.
- FastAPI backend.
- PostgreSQL on private database network.
- Background integration worker.
- Backup process to separate protected storage.

## Network rules
- Internet -> HTTPS edge only.
- Edge -> approved reverse proxy/application endpoint.
- Application -> PostgreSQL on required DB port only.
- Device -> approved ADMS/TA Push receiver only, if applicable.
- No public exposure of PostgreSQL or device management/communication ports.

## Commands
Local development is in `README.md`. The Compose file publishes only loopback ports.

```powershell
docker compose --env-file .env -f deployment/docker-compose.yml up --build
docker compose --env-file .env -f deployment/docker-compose.yml ps
```

Stop without deleting the database volume:

```powershell
docker compose --env-file .env -f deployment/docker-compose.yml down
```

`down -v` deletes PostgreSQL data. Do not use it on a database you need to keep.

The API container runs `alembic upgrade head` before it serves traffic. Health is `GET /api/health`. Nginx is the only published HTTP port, `127.0.0.1:8080`. Put TLS and any WAF in front of that port on the KPRA edge. Do not publish 5432, 8000, or a device port on a public address.

## Backup and restore
Take a custom-format dump from the database container and store it encrypted, off the application host:

```powershell
docker compose --env-file .env -f deployment/docker-compose.yml exec db pg_dump -U attendance -Fc attendance > attendance.dump
```

Restore into an empty database, then start the API so Alembic can confirm the schema:

```powershell
docker compose --env-file .env -f deployment/docker-compose.yml exec -T db pg_restore -U attendance -d attendance --clean --if-exists < attendance.dump
```

Test that restore on a copy before relying on it. The dump contains attendance and account secrets in hashed or encrypted form. Protect the file.

## Rollback
Keep the previous image tags or the previous git revision. To roll back the application, stop the Compose project, check out the previous revision, and start it again. Do not roll back a migration that has already written attendance rows unless a tested down-migration and a backup restore are both ready. The current migration is `0001_initial` and has no down path that preserves data. Restoring the encrypted backup is the rollback for the database.

## Not production-ready
Compose is the first-release shape. It does not by itself provide KPRA DNS, a public TLS certificate, a WAF, secret storage, or an approved device path. `APP_ENV=production` refuses development seed data, short secrets, and `COOKIE_SECURE=false`.

## Required operational documentation
- DNS and TLS setup.
- Firewall rule matrix.
- Environment variables.
- Secret rotation.
- Database migration and rollback.
- Encrypted backup and restore.
- Health checks and monitoring.
- Deployment and rollback commands.
