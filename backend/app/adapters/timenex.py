"""TimeNex boundary.

The KPRA tenant is a web application with CSV export and a manual attendance screen.
No authorized write or import contract is implemented here. This adapter sends nothing
and never reports a record as confirmed.
"""

from dataclasses import dataclass

from app.config import get_settings
from app.errors import AppError

NOT_SENT = "No request was sent."
AWAITING = (
    "No authorized TimeNex write or import contract is configured. "
    "No request was sent. This attendance record is not synchronized."
)
MISCONFIGURED = (
    "A TimeNex switch or address is set, but no verified write contract is implemented. "
    "No request was sent."
)


@dataclass(frozen=True)
class SubmissionResult:
    confirmed: bool
    submitted: bool
    status: str
    reference: str | None
    message: str


class TimeNexAdapter:
    code = "timenex"

    def status(self) -> dict:
        settings = get_settings()
        address_set = bool(settings.timenex_base_url.strip() or settings.timenex_api_token.strip())
        return {
            "code": self.code,
            "synced": False,
            "api_implemented": False,
            "import_implemented": False,
            "write_mode": settings.timenex_write_mode,
            "address_configured": address_set,
            "acceptance_test_passed": False,
            "reason": (
                "No TimeNex write API or approved import contract is implemented. "
                "Attendance stays in this application until TimeNex confirms it. Nothing is marked synced."
            ),
        }

    def push_attendance(self, event_id: str) -> None:
        del event_id
        raise AppError(
            "NOT_AVAILABLE",
            "TimeNex attendance push is not implemented. No request was sent.",
            501,
        )

    def submit(self, payload: dict) -> SubmissionResult:
        del payload
        settings = get_settings()
        switched_on = (
            settings.timenex_integration_enabled
            or bool(settings.timenex_base_url.strip())
            or bool(settings.timenex_api_token.strip())
            or settings.timenex_write_mode not in {"", "none"}
        )
        if switched_on:
            return SubmissionResult(False, False, "MISCONFIGURED", None, MISCONFIGURED)
        return SubmissionResult(False, False, "AWAITING_CONTRACT", None, AWAITING)
