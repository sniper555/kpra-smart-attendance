"""Read the TimeNex Mark Attendance screen. Does not submit attendance."""

import json
import re
from pathlib import Path

from dotenv import dotenv_values
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".local" / "timenex-mark"
OUT.mkdir(parents=True, exist_ok=True)
env = dotenv_values(ROOT / ".env")
base = (env.get("TIMENEX_PORTAL_URL") or "").rstrip("/")
username = env.get("TIMENEX_USERNAME") or ""
password = env.get("TIMENEX_PASSWORD") or ""
if not base or not username or not password:
    raise SystemExit("Portal address or sign-in values are missing from .env")

requests_seen = []


def keep(request):
    if request.resource_type not in {"document", "xhr", "fetch"}:
        return
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"cookie", "authorization"}
    }
    body = request.post_data
    if body:
        body = re.sub(r"(password=)[^&]*", r"\1[redacted]", body)
        body = re.sub(r"(_token=)[^&]*", r"\1[redacted]", body)
    requests_seen.append(
        {
            "method": request.method,
            "url": request.url,
            "resource_type": request.resource_type,
            "headers": headers,
            "post_data": body,
        }
    )


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.on("request", keep)
    page.goto(base + "/", wait_until="domcontentloaded", timeout=60000)
    page.get_by_placeholder("Username").fill(username)
    page.get_by_placeholder("Password").fill(password)
    page.get_by_role("button", name="Secure Login").click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2000)
    (OUT / "after-login-url.txt").write_text(page.url, encoding="utf-8")
    if "login" in page.url.lower() or page.get_by_placeholder("Username").count():
        (OUT / "login-failed.txt").write_text(page.inner_text("body")[:2000], encoding="utf-8")
        browser.close()
        raise SystemExit("Sign-in did not leave the login page")
    page.goto(base + "/marks_attendance", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    (OUT / "mark-url.txt").write_text(page.url, encoding="utf-8")
    (OUT / "mark.html").write_text(page.content(), encoding="utf-8")
    forms = page.eval_on_selector_all(
        "form",
        """forms => forms.map(form => ({
            action: form.getAttribute("action"),
            method: (form.getAttribute("method") || "").toLowerCase(),
            id: form.id,
            fields: [...form.querySelectorAll("input, select, textarea, button")].map(field => ({
                tag: field.tagName,
                type: field.getAttribute("type"),
                name: field.getAttribute("name"),
                id: field.id,
                value: field.tagName === "INPUT" && field.type === "password" ? "" : (field.getAttribute("value") || "").slice(0, 80),
                placeholder: field.getAttribute("placeholder")
            }))
        }))""",
    )
    scripts = page.eval_on_selector_all(
        "script",
        "nodes => nodes.map(node => node.src || '')",
    )
    (OUT / "forms.json").write_text(json.dumps(forms, indent=2), encoding="utf-8")
    (OUT / "scripts.json").write_text(json.dumps(scripts, indent=2), encoding="utf-8")
    page.get_by_placeholder("Name \\ ID \\ CNIC").fill("22091")
    page.get_by_role("button", name="Search").click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2000)
    (OUT / "search-url.txt").write_text(page.url, encoding="utf-8")
    (OUT / "search.html").write_text(page.content(), encoding="utf-8")
    search_forms = page.eval_on_selector_all(
        "form",
        """forms => forms.map(form => ({
            action: form.getAttribute("action"),
            method: (form.getAttribute("method") || "").toLowerCase(),
            id: form.id,
            className: form.className,
            fields: [...form.querySelectorAll("input, select, textarea, button")].map(field => ({
                tag: field.tagName,
                type: field.getAttribute("type"),
                name: field.getAttribute("name"),
                id: field.id,
                placeholder: field.getAttribute("placeholder")
            }))
        }))""",
    )
    (OUT / "search-forms.json").write_text(json.dumps(search_forms, indent=2), encoding="utf-8")
    buttons = page.eval_on_selector_all(
        "button, a.btn, input[type=submit]",
        "nodes => nodes.map(node => (node.innerText || node.value || '').trim()).filter(Boolean)",
    )
    (OUT / "search-buttons.json").write_text(json.dumps(buttons, indent=2), encoding="utf-8")
    page.goto(base + "/marks_attendance/mark_attendance/22091", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    (OUT / "punch-url.txt").write_text(page.url, encoding="utf-8")
    (OUT / "punch.html").write_text(page.content(), encoding="utf-8")
    punch_forms = page.eval_on_selector_all(
        "form",
        """forms => forms.map(form => ({
            action: form.getAttribute("action"),
            method: (form.getAttribute("method") || "").toLowerCase(),
            id: form.id,
            fields: [...form.querySelectorAll("input, select, textarea, button")].map(field => ({
                tag: field.tagName,
                type: field.getAttribute("type"),
                name: field.getAttribute("name"),
                id: field.id,
                placeholder: field.getAttribute("placeholder")
            }))
        }))""",
    )
    (OUT / "punch-forms.json").write_text(json.dumps(punch_forms, indent=2), encoding="utf-8")
    (OUT / "requests.json").write_text(json.dumps(requests_seen, indent=2), encoding="utf-8")
    browser.close()

html = (OUT / "mark.html").read_text(encoding="utf-8", errors="replace")
urls = sorted(set(re.findall(r"""https?://[^"'\\s<]+|/[^"'\\s]*attendance[^"'\\s]*""", html, flags=re.I)))
(OUT / "html-urls.txt").write_text("\n".join(urls), encoding="utf-8")
print("saved", OUT)
print("url", (OUT / "mark-url.txt").read_text(encoding="utf-8"))
print("forms", len(forms))
