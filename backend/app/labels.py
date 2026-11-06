from app.constants import SOURCE_LABELS


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def classify_remote_source(user_agent: str, sec_ch_ua_mobile: str | None) -> str:
    if (sec_ch_ua_mobile or "").strip() == "?1":
        return "REMOTE_MOBILE"
    ua = (user_agent or "").lower()
    markers = ("android", "iphone", "ipad", "ipod", "mobile")
    if any(marker in ua for marker in markers):
        return "REMOTE_MOBILE"
    return "REMOTE_WEB"


def user_agent_family(source: str) -> str:
    if source == "REMOTE_MOBILE":
        return "mobile"
    if source == "REMOTE_WEB":
        return "desktop"
    return "unknown"


def client_platform(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    if "android" in ua:
        return "android"
    if "iphone" in ua or "ipad" in ua or "ipod" in ua:
        return "ios"
    if "windows" in ua:
        return "windows"
    if "mac os" in ua or "macintosh" in ua:
        return "mac"
    return "other"


def allow_metadata(raw: dict) -> dict:
    allowed = {
        "user_agent_family",
        "client_platform",
        "user_verification",
        "is_test_record",
        "import_batch_id",
        "reconciliation_status",
        "correction_request_id",
    }
    clean: dict = {}
    for key in allowed:
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, str):
            clean[key] = value[:200]
        elif isinstance(value, bool):
            clean[key] = value
    return clean


def csv_safe(value: object) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text
