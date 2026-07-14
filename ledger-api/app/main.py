from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Annotated

logging.basicConfig(
    level=os.environ.get("LEDGER_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import db, fees, ledger
from .settings import Settings, get_settings

logger = logging.getLogger("ledger_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The nightly recompute loop is NOT started here. Like the main
    # circlejerks API/worker split (backend/app/main.py + backend/app/worker.py,
    # run as separate `api` / `worker` processes from the same image — see
    # docker-compose.prod.yml), this service's API process only ever serves
    # `/airports/{icao}/ledger`; `python -m app.worker` is a second process
    # from this same image that runs `_ledger_loop` (see worker.py). Keeping
    # them separate means a slow or wedged API worker process can never delay
    # the nightly rebuild, and vice versa.
    app.state.settings = get_settings()
    yield


app = FastAPI(title="The Lost Landing — ledger sidecar", version="0.1.0", lifespan=lifespan)


def settings_dep() -> Settings:
    return app.state.settings


# CORS middleware must be added before the app starts serving, which is
# earlier than `lifespan` runs — so this reads the same cached `get_settings()`
# singleton the lifespan uses, rather than `app.state`.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz(settings: Annotated[Settings, Depends(settings_dep)]):
    return {"ok": True, "environment": settings.environment}


@app.get("/airports/{icao}/ledger")
async def get_airport_ledger(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    _now: int | None = None,
):
    """The Lost Landing's entire data source: runway uses, the daily series, the
    operator ledger, and the methodology block that ships WITH the numbers.

    Opens the production database READ-ONLY and this service's own database
    read-write, as two distinct connections — see app/db.py's module
    docstring. Neither is ever confused for the other.
    """
    now = _now if _now is not None else int(time.time())
    with db.production_readonly_session(settings.production_database_path) as ro_conn, \
            db.ledger_db_session(settings.ledger_database_path) as rw_conn:
        if db.get_airport(ro_conn, icao) is None:
            raise HTTPException(status_code=404, detail="airport not found")
        return ledger.build_ledger(ro_conn, rw_conn, icao, settings, now_ts=now, days=days)


@app.get("/airports/{icao}/aircraft-fees")
async def get_aircraft_fees(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
    _now: int | None = None,
):
    """Per-aircraft runway-use totals for the live map's price tags and the
    hero ticker. See app/fees.py's module docstring for the rolling-24h vs
    local-calendar-month/year distinction, and why neither ever reads
    `daily_operation_rollup`.

    Read-only against the production database ONLY -- this endpoint never
    opens this service's own database, because it has nothing to write and
    nothing it needs from the rollup.
    """
    now = _now if _now is not None else int(time.time())
    with db.production_readonly_session(settings.production_database_path) as ro_conn:
        if db.get_airport(ro_conn, icao) is None:
            raise HTTPException(status_code=404, detail="airport not found")
        return fees.build_aircraft_fees(ro_conn, icao, now_ts=now)
