# KPRA Smart Attendance Lite — Starter Pack

This package contains the complete project instructions for Claude Code or Cursor.

## Fast start

1. Extract this ZIP into a new folder named `KPRA-Smart-Attendance-Lite`.
2. Open that folder in Cursor.
3. Open the built-in terminal and start Agent mode.
4. Paste the contents of `MASTER_BUILD_PROMPT.md` as your first instruction.
5. Allow the agent to inspect the included `CLAUDE.md`, `.cursor/rules/`, and `docs/`.
6. Review the Phase 0/Phase 1 findings before allowing production integration or deployment.

You do **not** need to manually create folders or instruction files. They are already included.

## Important security actions

- The TimeNex password was previously shared in a chat. Change it immediately if it is still active.
- Do not put production credentials in prompts, source code, screenshots, Git, or `.env.example`.
- Use a temporary least-privilege account for portal investigation.
- Do not change the SenseFace device's production server address or port until the existing TimeNex/ADMS flow has been documented and a change window is approved.

## Scope

This is a lightweight manager attendance application for authorized KPRA managers, with:
- responsive laptop/mobile PWA;
- secure manager login and MFA;
- remote Check-In / Check-Out, clearly labelled as remote attendance;
- admin-managed manager roster and employee PIN mapping;
- attendance history and basic reports;
- audit trail and correction workflow;
- optional, separately scoped SenseFace 7 Series ADMS/TA Push event integration;
- optional TimeNex synchronization only through a verified, authorized interface.

It does not emulate fingerprints, bypass biometrics, or fabricate physical biometric events.
