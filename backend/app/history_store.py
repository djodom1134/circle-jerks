"""The long-term ("cold") track store: a second SQLite file on the same
track_archive schema, holding the backfilled year plus everything that ages
out of the operational (hot) 7-day archive. Read paths fall through to it for
windows older than the hot horizon; the archive loop syncs the aging tail into
it before prune deletes it from hot.

Both helpers are defensive: if the file isn't configured/present, `available`
returns False and every caller behaves exactly as it did before this feature.
"""

from __future__ import annotations

import os

from .settings import Settings


def available(settings: Settings) -> bool:
    p = getattr(settings, "history_database_path", None)
    return bool(p) and os.path.exists(p) and os.path.getsize(p) > 0


def airport_allowed(icao: str | None, settings: Settings) -> bool:
    # icao24-only queries carry no airport; the cold store is airport-scoped by
    # its own contents (KLMO only, today), so those are safe to serve.
    if not icao:
        return True
    allow = {a.upper() for a in getattr(settings, "permanent_history_airports", []) or []}
    return icao.upper() in allow


def horizon_seconds(settings: Settings) -> int:
    return int(settings.track_archive_horizon_days) * 86400
