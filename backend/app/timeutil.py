from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

PKT = ZoneInfo("Asia/Karachi")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("naive datetime")
    return value.astimezone(timezone.utc)


def time_view(value: datetime | None) -> dict | None:
    if value is None:
        return None
    utc = value.astimezone(timezone.utc)
    pkt = value.astimezone(PKT)
    return {
        "utc": utc.isoformat(),
        "pkt": pkt.isoformat(),
        "pkt_display": pkt.strftime("%d %b %Y, %H:%M:%S PKT"),
    }


def pkt_day_bounds(day_pkt: datetime) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day_pkt.date(), time.min, tzinfo=PKT)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start_local = datetime(year, month, 1, tzinfo=PKT)
    if month == 12:
        end_local = datetime(year + 1, 1, 1, tzinfo=PKT)
    else:
        end_local = datetime(year, month + 1, 1, tzinfo=PKT)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def parse_hhmm(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError("time")
    hour, minute = int(parts[0]), int(parts[1])
    if hour > 23 or minute > 59 or hour < 0 or minute < 0:
        raise ValueError("time")
    return time(hour, minute)
