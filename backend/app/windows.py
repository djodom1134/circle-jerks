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
    # Kept under the "today" code for URL/preference back-compat — the label and
    # the UI both say "24 hours" now.
    "today": 86400,
}

WINDOW_LABELS = {
    "5m": "in the last 5 minutes",
    "30m": "in the last 30 minutes",
    "1h": "in the last hour",
    "6h": "in the last 6 hours",
    "today": "in the last 24 hours",
}


class WindowRange(BaseModel):
    code: str
    label: str
    start_ts: int
    end_ts: int
    seconds: int


def validate_window(window: str | None) -> str:
    code = window or "1h"
    if code not in WINDOW_SECONDS:
        raise ValueError("window must be one of 5m, 30m, 1h, 6h, today")
    return code


def resolve_window(window: str | None, tz_name: str, now: datetime | None = None) -> WindowRange:
    """Every window is a rolling lookback from `now`.

    "today" used to snap to local midnight, which made it narrower than the 6h
    button in the small hours (00:30 local returned a 30-minute window). It is
    a rolling 24h instead, so no window depends on the local calendar day.
    """
    code = validate_window(window)
    # Reject a bad timezone here rather than deep inside histogram bucketing,
    # where it would surface as a 500 instead of a 422.
    ZoneInfo(tz_name)
    current = now or datetime.now(timezone.utc)
    start = current - timedelta(seconds=WINDOW_SECONDS[code])
    seconds = max(0, int(current.timestamp() - start.timestamp()))
    return WindowRange(
        code=code,
        label=WINDOW_LABELS[code],
        start_ts=int(start.timestamp()),
        end_ts=int(current.timestamp()),
        seconds=seconds,
    )

