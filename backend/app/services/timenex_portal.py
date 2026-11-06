"""TimeNex portal session for the existing Mark Attendance form.

This client uses the signed-in portal account from the environment.
It does not store the password in source, and it does not call the form
until mark_day is invoked.
"""

import logging
import re
from datetime import date, time

import httpx

from app.config import get_settings

logger = logging.getLogger("kpra.timenex")
_TOKEN = re.compile(r'name="_token"\s+value="([^"]+)"|value="([^"]+)"\s+name="_token"')
_MARK_ID = re.compile(r"/marks_attendance/mark_attendance/(\d+)")
_INPUT = re.compile(r"<input\b[^>]*>", re.I)


def _redact(text: str) -> str:
    cleaned = re.sub(r"(password=)[^&\s]+", r"\1[redacted]", text, flags=re.I)
    cleaned = re.sub(r"(_token=)[^&\s]+", r"\1[redacted]", cleaned)
    cleaned = re.sub(r'name="_token"\s+value="[^"]+"', 'name="_token" value="[redacted]"', cleaned)
    return cleaned


def _snippet(text: str, limit: int = 240) -> str:
    flat = " ".join(_redact(text or "").split())
    return flat[:limit]


def _input_value(html: str, name: str) -> str | None:
    for tag in _INPUT.findall(html or ""):
        if not re.search(rf'\bname="{re.escape(name)}"', tag, re.I):
            continue
        match = re.search(r'\bvalue="([^"]*)"', tag, re.I)
        if match is None:
            return None
        value = match.group(1).strip()
        return value or None
    return None


def _token_from(html: str) -> str:
    match = _TOKEN.search(html or "")
    if match is None:
        raise RuntimeError("The TimeNex page did not include a form token.")
    return match.group(1) or match.group(2)


class TimenexPortalClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        settings = get_settings()
        self.base_url = settings.timenex_portal_url.rstrip("/")
        self.username = settings.timenex_username
        self.password = settings.timenex_password
        self.http = client or httpx.Client(
            base_url=self.base_url,
            follow_redirects=True,
            timeout=30.0,
            verify=False,
        )
        self._owns_client = client is None
        self._logged_in = False

    def close(self) -> None:
        if self._owns_client:
            self.http.close()

    def login(self) -> httpx.Client:
        if not self.username or not self.password:
            raise RuntimeError("TimeNex portal sign-in is not configured.")
        page = self._request("GET", "/")
        token = _token_from(page.text)
        response = self._request(
            "POST",
            "/loginaction",
            data={"_token": token, "email": self.username, "password": self.password},
        )
        if self._is_login_page(response):
            raise RuntimeError("TimeNex portal sign-in was rejected.")
        self._logged_in = True
        return self.http

    def search_employee(self, query: str) -> list[dict]:
        self._ensure_login()
        page = self._request("GET", "/marks_attendance")
        response = self._request(
            "POST",
            "/employee/search",
            data={"_token": _token_from(page.text), "search": query.strip()},
        )
        if self._is_login_page(response):
            self._logged_in = False
            self._ensure_login()
            page = self._request("GET", "/marks_attendance")
            response = self._request(
                "POST",
                "/employee/search",
                data={"_token": _token_from(page.text), "search": query.strip()},
            )
        found = []
        seen = set()
        for employee_id in _MARK_ID.findall(response.text):
            if employee_id in seen:
                continue
            seen.add(employee_id)
            found.append({"timenex_employee_id": employee_id})
        return found

    def open_form(self, employee_id: str) -> dict:
        self._ensure_login()
        response = self._request("GET", f"/marks_attendance/mark_attendance/{employee_id}")
        if self._is_login_page(response):
            self._logged_in = False
            self._ensure_login()
            response = self._request("GET", f"/marks_attendance/mark_attendance/{employee_id}")
        return {
            "ok": response.status_code < 400 and "Attendance Date" in response.text and not self._is_login_page(response),
            "status_code": response.status_code,
            "url": str(response.url),
            "has_time_in": 'name="time_in"' in response.text,
            "has_time_out": 'name="time_out"' in response.text,
        }

    def current_times(self, employee_id: str) -> dict[str, str | None]:
        """Read time in and time out already shown on the mark form. This does not save."""
        self._ensure_login()
        form = self._request("GET", f"/marks_attendance/mark_attendance/{employee_id}")
        if self._is_login_page(form):
            self._logged_in = False
            self._ensure_login()
            form = self._request("GET", f"/marks_attendance/mark_attendance/{employee_id}")
        if form.status_code >= 400 or self._is_login_page(form):
            raise RuntimeError("The TimeNex mark attendance form did not open.")
        return {
            "time_in": _input_value(form.text, "time_in"),
            "time_out": _input_value(form.text, "time_out"),
        }

    def mark_day(self, employee_id: str, day: date, time_in: time, time_out: time | None) -> dict:
        self._ensure_login()
        form = self._request("GET", f"/marks_attendance/mark_attendance/{employee_id}")
        if self._is_login_page(form):
            self._logged_in = False
            self._ensure_login()
            form = self._request("GET", f"/marks_attendance/mark_attendance/{employee_id}")
        if form.status_code >= 400 or "Attendance Date" not in form.text:
            return self._failure(form, "The TimeNex mark attendance form did not open.")
        payload = {
            "_token": _token_from(form.text),
            "id": str(employee_id),
            "attendance_date": day.isoformat(),
            "time_in": time_in.strftime("%H:%M:%S"),
            "time_out": "" if time_out is None else time_out.strftime("%H:%M:%S"),
            "ack": "on",
        }
        saved = self._request("POST", "/marks_attendance/markattendance", data=payload)
        if self._is_login_page(saved):
            self._logged_in = False
            self.login()
            form = self._request("GET", f"/marks_attendance/mark_attendance/{employee_id}")
            payload["_token"] = _token_from(form.text)
            saved = self._request("POST", "/marks_attendance/markattendance", data=payload)
        failed = saved.status_code >= 400 or self._is_login_page(saved) or "alert-danger" in saved.text or "The given data was invalid" in saved.text
        reference = None if failed else f"portal:{employee_id}:{day.isoformat()}"
        snippet = _snippet(saved.text)
        logger.info("timenex mark_day employee=%s date=%s status=%s ok=%s", employee_id, day.isoformat(), saved.status_code, not failed)
        return {
            "ok": not failed,
            "status_code": saved.status_code,
            "reference": reference,
            "error": None if not failed else (snippet or "TimeNex did not accept the day."),
            "body_snippet": snippet,
            "payload": {
                "id": str(employee_id),
                "attendance_date": day.isoformat(),
                "time_in": payload["time_in"],
                "time_out": payload.get("time_out"),
            },
        }

    def _ensure_login(self) -> None:
        if not self._logged_in:
            self.login()

    def _request(self, method: str, path: str, data: dict | None = None) -> httpx.Response:
        response = self.http.request(method, path, data=data)
        logger.info("timenex %s %s -> %s %s", method, path, response.status_code, _snippet(response.text, 180))
        return response

    def _is_login_page(self, response: httpx.Response) -> bool:
        return 'placeholder="Password"' in response.text

    def _failure(self, response: httpx.Response, message: str) -> dict:
        snippet = _snippet(response.text)
        return {
            "ok": False,
            "status_code": response.status_code,
            "reference": None,
            "error": snippet or message,
            "body_snippet": snippet,
            "payload": None,
        }
