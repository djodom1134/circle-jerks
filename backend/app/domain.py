from __future__ import annotations

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field


class ScanParams(BaseModel):
    airport_icao: str
    user_lat: float
    user_lon: float
    user_elevation_ft: int | None = Field(default=None, ge=-1500, le=20000)
    ring_nm: float = Field(default=8.0, ge=3.0, le=20.0)
    pass_radius_nm: float = Field(default=0.5, ge=0.1, le=3.0)
    pass_ceiling_ft: int = Field(default=5000, ge=500, le=12000)
    window: str = "1h"

    def normalized(self) -> "ScanParams":
        return self.model_copy(update={"airport_icao": self.airport_icao.upper()})


def location_hash(lat: float, lon: float) -> str:
    payload = f"{round(lat, 2):.2f}:{round(lon, 2):.2f}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def monitor_hash(params: ScanParams) -> str:
    p = params.normalized()
    payload = {
        "airport_icao": p.airport_icao,
        "user": location_hash(p.user_lat, p.user_lon),
        "ring_nm": round(p.ring_nm, 1),
        "pass_radius_nm": round(p.pass_radius_nm, 2),
        "pass_ceiling_ft": p.pass_ceiling_ft,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:20]


def _format_time_12h(dt: datetime, include_date: bool = False, include_minutes: bool = True) -> str:
    hour = dt.strftime("%I").lstrip("0") or "12"
    suffix = dt.strftime("%p")
    date = f"{dt.strftime('%Y-%m-%d')} " if include_date else ""
    if include_minutes:
        return f"{date}{hour}:{dt.strftime('%M')} {suffix}"
    return f"{date}{hour} {suffix}"


def local_time_label(ts: int, tz_name: str) -> str:
    return _format_time_12h(datetime.fromtimestamp(ts, ZoneInfo(tz_name)), include_date=True)


def hour_label(ts: int, tz_name: str, include_date: bool = False) -> str:
    return _format_time_12h(
        datetime.fromtimestamp(ts, ZoneInfo(tz_name)),
        include_date=include_date, include_minutes=False,
    )


def minute_label(ts: int, tz_name: str, include_date: bool = False) -> str:
    return _format_time_12h(
        datetime.fromtimestamp(ts, ZoneInfo(tz_name)), include_date=include_date,
    )
