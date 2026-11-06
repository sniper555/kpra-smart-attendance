# Project Instructions — KPRA Smart Attendance Lite

Read `MASTER_BUILD_PROMPT.md` and all relevant files in `docs/` before coding.

## Product priorities
1. Fast, secure manager self-attendance from desktop/mobile.
2. Accurate attendance events and immutable audit history.
3. Standalone operation even if TimeNex or the biometric device is offline.
4. SenseFace 7 Series integration only through verified supported interfaces.
5. Hybrid deployment with application/data inside KPRA-controlled infrastructure.

## Hard rules
- Never put credentials or secrets in source code, prompts, logs, Git, or docs.
- Never fabricate biometric events or label remote attendance as biometric.
- Never change the live device's ADMS address/port without explicit approval and rollback plan.
- Never assume TimeNex has an API or write access.
- Never expose database/device ports publicly.
- Enforce all authorization in backend APIs.
- Use server timestamps and idempotency for attendance.
- Corrections must be auditable; do not delete historical attendance events.
- Do not add out-of-scope HR/payroll or universal protocol features.

## Engineering rules
- Prefer a modular monolith.
- Keep integrations behind adapters.
- Pin dependencies and document version decisions.
- Write migrations and automated tests.
- Every UI control must work or be explicitly disabled with explanation.
- Update `docs/PROGRESS.md` and `docs/DECISIONS.md`.
- Run relevant tests after each meaningful change.
