"""KPRA ERP manual-attendance connector.

The ERP page at /attendance/manual is a signed-in browser form. Its empID value
is an internal key, not the TimeNex employee ID. This connector does not call
that form, does not reuse a browser session, and never reports TimeNex confirmed.
"""

from dataclasses import dataclass


NEEDS_ID = (
    "Saved in this application. The TimeNex employee ID is not the ERP internal ID, "
    "and the ERP internal ID is not stored. No request was sent. TimeNex is not confirmed."
)
NOT_SENT = (
    "Saved in this application. No authorized ERP application endpoint is configured. "
    "The manual attendance page was not called. No request was sent. TimeNex is not confirmed."
)
PENDING = "ERP submission pending. TimeNex is not confirmed."
ACCEPTED = "ERP attendance accepted. TimeNex synchronization is pending. TimeNex has not confirmed this record."
FAILED = "ERP did not accept this record. It requires administrator attention. TimeNex is not confirmed."


@dataclass(frozen=True)
class ErpResult:
    confirmed: bool
    submitted: bool
    status: str
    reference: str | None
    message: str


class ErpAttendanceAdapter:
    code = "kpra_erp"

    def status(self) -> dict:
        return self._view("NOT_SENT", None)

    def submit(self, payload: dict) -> ErpResult:
        erp_id = str(payload.get("erp_employee_id") or "").strip()
        if not erp_id:
            return ErpResult(False, False, "NEEDS_ERP_ID", None, NEEDS_ID)
        return ErpResult(False, False, "NOT_SENT", None, NOT_SENT)

    def _view(self, status: str, reference: str | None) -> dict:
        messages = {
            "NEEDS_ERP_ID": NEEDS_ID,
            "NOT_SENT": NOT_SENT,
            "PENDING": PENDING,
            "ACCEPTED": ACCEPTED,
            "FAILED": FAILED,
        }
        accepted = status == "ACCEPTED" and bool(reference)
        return {
            "code": self.code,
            "local_recorded": True,
            "submitted": False,
            "confirmed": False,
            "erp_accepted": accepted,
            "timenex_confirmed": False,
            "status": status,
            "status_label": _label(status, accepted),
            "reference": reference if accepted else None,
            "api_implemented": False,
            "acceptance_test_passed": False,
            "message": messages.get(status, NOT_SENT),
        }


def _label(status: str, accepted: bool) -> str:
    if status == "PENDING":
        return "ERP submission pending"
    if accepted:
        return "ERP attendance accepted"
    if status == "FAILED":
        return "Failed / requires administrator attention"
    if status == "NEEDS_ERP_ID":
        return "Failed / requires administrator attention"
    return "Failed / requires administrator attention"
