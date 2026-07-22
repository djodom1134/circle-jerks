"""The public /v1 contract: one model per response, plus the builders that map
an internal row onto it.

Deliberately free of FastAPI routing, sqlite3, and all I/O. public_api.py owns
auth, scoping, and routing; this module owns the shape. Keeping them apart means
the entire published contract is readable in one sitting, and that a database
column added tomorrow cannot reach a partner — the model filters it structurally
rather than relying on a test somebody remembers to update.

Renames live HERE, at the serialization boundary. Internal dict keys, database
columns, and the frontend keep their existing names.
"""

from __future__ import annotations

from pydantic import BaseModel

from . import api_keys
from .deviation import CORRIDOR_NM

# Published in /v1/meta so a consumer can interpret fraction_off_pattern.
PATTERN_CORRIDOR_NM = CORRIDOR_NM
VNAP_SCORE_SCALE = "0..100"
AXIS_SCORE_SCALE = "0..100"


class Constants(BaseModel):
    pattern_corridor_nm: float
    vnap_score_scale: str
    axis_score_scale: str


class Limits(BaseModel):
    requests_per_minute: int
    max_page_size: int
    max_track_span_seconds: int


class MetaOut(BaseModel):
    version: str
    name: str
    scopes: list[str]
    airports: list[str] | None
    limits: Limits
    constants: Constants


def meta_out(ctx: api_keys.ApiKeyContext) -> MetaOut:
    from .public_api import API_VERSION, MAX_PAGE_SIZE, MAX_TRACK_SPAN_SECONDS, RATE_LIMIT_PER_MINUTE

    return MetaOut(
        version=API_VERSION,
        name=ctx.name,
        scopes=sorted(ctx.scopes),
        airports=sorted(ctx.airports) if ctx.airports is not None else None,
        limits=Limits(
            requests_per_minute=RATE_LIMIT_PER_MINUTE,
            max_page_size=MAX_PAGE_SIZE,
            max_track_span_seconds=MAX_TRACK_SPAN_SECONDS,
        ),
        constants=Constants(
            pattern_corridor_nm=PATTERN_CORRIDOR_NM,
            vnap_score_scale=VNAP_SCORE_SCALE,
            axis_score_scale=AXIS_SCORE_SCALE,
        ),
    )
