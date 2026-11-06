"""Open the TimeNex mark form without saving."""

from app.config import get_settings
from app.services.timenex_portal import TimenexPortalClient

get_settings.cache_clear()
client = TimenexPortalClient()
try:
    client.login()
    opened = client.open_form("22063")
    print("login ok")
    print("form", {key: opened[key] for key in ("ok", "status_code", "has_time_in", "has_time_out")})
    print("url", opened["url"].split("?")[0])
finally:
    client.close()
