"""SenseFace 7 Series boundary.

The vendor manual documents that a terminal connects outward to an ADMS address.
It does not publish the push payload. This adapter does not listen, does not
connect to 173.208.167.249, and does not accept a fabricated terminal identity.
"""

from app.errors import AppError


class SenseFaceAdapter:
    code = "senseface"

    def status(self) -> dict:
        return {
            "code": self.code,
            "connected": False,
            "receiving_events": False,
            "protocol_implemented": False,
            "reason": (
                "No verified SenseFace 7 ADMS payload is implemented. "
                "The production terminal stays on its current server."
            ),
        }

    def ingest(self, payload: bytes) -> None:
        del payload
        raise AppError(
            "NOT_AVAILABLE",
            "No verified SenseFace event format is implemented. The payload was rejected.",
            501,
        )
