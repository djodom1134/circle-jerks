from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel

WindowCode = str

WINDOW_SECONDS = {
    "5m": 300,
    "30m": 1800,
    "1h": 3600,
    "6h": 21600,
}

WINDOW_LABELS = {
    "5m": "in the last 5 minutes",
    "30m": "in the last 30 minutes",
    "1h": "in the last hour",
    "6h": "in the last 6 hours",
    "today": "today",
}


class WindowRange(BaseModel):
    code: str
    label: str
    start_ts: int
    end_ts: int
    seconds: int


def validate_window(window: str | None) -> str:
    code = window or "1h"
    if code not in {*WINDOW_SECONDS.keys(), "today"}:
        raise ValueError("window must be one of 5m, 30m, 1h, 6h, today")
    return code


def resolve_window(window: str | None, tz_name: str, now: datetime | None = None) -> WindowRange:
    code = validate_window(window)
    tz = ZoneInfo(tz_name)
    current = now or datetime.now(timezone.utc)
    current_local = current.astimezone(tz)
    if code == "today":
        local_midnight = current_local.replace(hour=0, minute=0, second=0, microsecond=0)
        start = local_midnight.astimezone(timezone.utc)
    else:
        start = current - timedelta(seconds=WINDOW_SECONDS[code])
    seconds = max(0, int(current.timestamp() - start.timestamp()))
    return WindowRange(
        code=code,
        label=WINDOW_LABELS[code],
        start_ts=int(start.timestamp()),
        end_ts=int(current.timestamp()),
        seconds=seconds,
    )

