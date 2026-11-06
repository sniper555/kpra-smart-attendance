# Security Design

- HTTPS only.
- MFA for managers and administrators; mandatory for privileged roles.
- Argon2id password hashing.
- Secure, HttpOnly, SameSite session cookies.
- CSRF protection for state-changing cookie-authenticated requests.
- Backend RBAC and object-level authorization.
- Login and attendance endpoint rate limiting.
- Restrict CORS to approved origins.
- No public database/device ports.
- Secrets from environment/secret store; never committed.
- Audit login, logout, attendance, corrections, admin changes, exports, and integration jobs.
- Store timestamps in UTC; render in Asia/Karachi.
- GPS, office Wi-Fi, and office IP checks are not used. Location is not collected and is not proof of presence.
- No face/fingerprint images or templates stored by this app. Passkeys store a public key only.
- Corrections are append-only compensating events.
- Encrypted backups and periodic restore tests.
