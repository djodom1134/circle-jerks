from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Signs the admin session cookie and the OAuth state cookie (see main.py's
# hmac.new(settings.app_secret...) call sites). This literal is public --
# it ships in this open-source repo -- so a production deploy that forgets
# to override it is fully cookie-forgeable by anyone who reads the source.
DEFAULT_APP_SECRET = "local-development-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CIRCLEJERK_",
        env_file=(".env", "../.env"),
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Circle Jerks: Automated Noise Complaint Generator"
    environment: Literal["local", "test", "production"] = "local"
    public_base_url: str = "http://localhost:5173"
    app_secret: str = DEFAULT_APP_SECRET

    database_path: str = "data/circlejerk.sqlite3"
    redis_url: str = "memory://"
    timezone: str = "America/Denver"

    groq_api_key: str | None = Field(default=None, validation_alias="GROQ_API_KEY")
    groq_model: str = "llama-3.3-70b-versatile"
    groq_fallback_model: str = "llama-3.1-8b-instant"

    opensky: str | None = Field(default=None, validation_alias="OPENSKY")
    opensky_client_id: str | None = Field(default=None, validation_alias="OPENSKY_CLIENT_ID")
    opensky_client_secret: str | None = Field(default=None, validation_alias="OPENSKY_CLIENT_SECRET")
    flightaware_api_key: str | None = Field(default=None, validation_alias="FLIGHTAWARE")
    flightaware_base_url: str = "https://aeroapi.flightaware.com/aeroapi"
    flightaware_timeout_seconds: float = 6.0
    opensky_poll_interval_seconds: int = 30
    opensky_anonymous_poll_interval_seconds: int = 30
    opensky_timeout_seconds: float = 12.0
    opensky_historical_enabled: bool = False
    # Backfill reaches back as far as we retain tracks locally. Aligning these
    # means a fresh Redis (after FLUSH or watchdog trim) can re-hydrate the full
    # retention window from OpenSky historical state vectors on demand.
    opensky_historical_limit_seconds: int = 14_400  # 4h, matches track_ttl_seconds
    opensky_historical_step_seconds: int = 30
    opensky_historical_snapshots_per_scan: int = 4
    opensky_historical_backfill_interval_seconds: int = 60
    opensky_historical_cache_seconds: int = 7200

    live_source_priority: str = "adsbx,self_hosted,adsb_lol,adsb_fi,airplanes_live,opensky"
    live_poll_interval_seconds: int = 10
    # Detection (4h event-detection pass per monitor) runs on its own slower
    # cadence, decoupled from live position ingestion above — see
    # worker._detect_loop. Heavy detector runs must never starve ingestion.
    detector_interval_seconds: int = 30
    live_source_timeout_seconds: float = 12.0
    live_source_backoff_seconds: int = 60
    live_source_rate_limit_backoff_seconds: int = 300
    live_source_max_radius_nm: float = 250.0
    live_source_max_staleness_seconds: int = 90
    live_source_min_aircraft_for_freshness: int = 3
    bbox_merge_distance_nm: float = 5.0

    adsb_lol_base_url: str = "https://api.adsb.lol"
    airplanes_live_base_url: str = "https://api.airplanes.live"
    adsb_fi_base_url: str = "https://opendata.adsb.fi/api"
    self_hosted_feeder_base_url: str | None = Field(default=None, validation_alias="SELF_HOSTED_FEEDER_BASE_URL")
    self_hosted_feeder_path_style: Literal["lat_lon_dist", "point"] = "lat_lon_dist"
    adsbx_rapidapi_key: str | None = Field(default=None, validation_alias="ADSBX_RAPIDAPI_KEY")
    adsbx_rapidapi_host: str = "adsbexchange-com1.p.rapidapi.com"

    # The ledger sidecar. Proxied rather than read directly: ledger-api owns
    # its own SQLite file, and opening it from here would put two writers on
    # one lock — the exact thing that split was made to avoid.
    ledger_api_base_url: str = "http://ledger-api:8100"

    default_airport_icao: str = "KBJC"
    monitor_ttl_seconds: int = 900
    # Sized to the managed Valkey memory budget (418MB) — at ~470MB for 24h of
    # tracks across active airports, the noeviction policy refused new writes
    # and wedged scan/SQLite. 4h retention keeps total well under the cap; the
    # "today" window is still served by triggering a historical backfill on
    # demand from OpenSky when track samples have aged out.
    track_ttl_seconds: int = 14_400
    # Cold-tier (SQLite track_archive) retention. The historical track-density
    # view reads up to this many days back; prune deletes older. Bounded by
    # disk, not Redis memory — safe to keep a week of a single field's traffic.
    track_archive_horizon_days: int = 7
    event_ttl_seconds: int = 14_400
    description_ttl_seconds: int = 600
    # Master switch for FlightAware AeroAPI. Set FLIGHTAWARE_ENABLED=false in
    # .env to stop spending — origin lookups fall through to adsbdb.com (free)
    # and OpenSky, and the FA-based historical backfill is skipped entirely.
    # validation_alias bypasses the CIRCLEJERK_ prefix so users see the plain
    # name in .env that matches the existing FLIGHTAWARE=... pattern.
    flightaware_enabled: bool = Field(default=True, validation_alias="FLIGHTAWARE_ENABLED")
    # Cold-start escape hatch: even with flightaware_enabled=false, fire ONE FA
    # backfill the first time a user lands on an airport whose archive is empty.
    # Without this, brand-new locations have no history until live polling has
    # been running for hours (Longmont works only because we've polled it for
    # weeks). After the cold-start fires, the per-airport 24h cooldown plus the
    # daily budget below keep the spend bounded.
    flightaware_cold_start_enabled: bool = Field(default=True, validation_alias="FLIGHTAWARE_COLD_START_ENABLED")
    # An airport counts as "cold" when its archive has fewer than this many
    # samples in the recent retention window. ~500 ≈ a few minutes of live data
    # at a busy field; well below this means a true cold start.
    flightaware_cold_start_min_samples: int = Field(default=500, validation_alias="FLIGHTAWARE_COLD_START_MIN_SAMPLES")
    # Hard cap on cold-start FA spends per rolling 24h across all airports —
    # circuit breaker against a scraper or unexpected traffic spike enumerating
    # the whole airport table. At ~$0.10–0.50 per cold-start this caps daily
    # spend at a few dollars.
    flightaware_cold_start_daily_budget: int = Field(default=5, validation_alias="FLIGHTAWARE_COLD_START_DAILY_BUDGET")
    # Free fallback for cold-start backfill: when FlightAware is disabled or
    # auth_failed, fetch each visible aircraft's recent trace from adsb.lol
    # and filter to the airport bbox. No budget gate — adsb.lol is ODbL and
    # we cap concurrency to be polite.
    adsblol_historical_enabled: bool = Field(default=True, validation_alias="ADSBLOL_HISTORICAL_ENABLED")
    # /scan response cache. Disabled by default — the managed Valkey instance
    # is noeviction, and full scan responses (~100KB each) exhausted maxmemory
    # in production, cascading into SQLite lock contention. Re-enable cautiously
    # and pair with `allkeys-lru` if that's ever flipped on the cluster.
    # Short TTL — the frontend polls /scan every 5s; a cold scan can take many
    # seconds, so without caching, polls stack up and saturate the workers
    # ("stuck on loading" cascade). 15s means ~2 of every 3 polls hit cache.
    # Footprint is tiny (a handful of location buckets * one entry each), well
    # within Valkey headroom now that track TTL is bounded + the watchdog trims.
    scan_response_cache_seconds: int = 15
    # Wide windows cost detector CPU over a full day of tracks and barely change
    # minute to minute. Only `today` qualifies: 1h and 6h carry the live aircraft
    # markers, and caching those froze the map for the cache's lifetime.
    wide_window_scan_cache_seconds: int = 30
    # A window STRICTLY LONGER than this counts as "wide" — 6h must not.
    wide_window_threshold_seconds: int = 21600
    # Lat/lon bucket size (degrees) used to group users for cache sharing.
    # ~0.003° ≈ 330 m at 40° latitude — tight enough that "altitude over user"
    # / "passes over user" don't drift meaningfully between users in the
    # bucket, loose enough that neighbors share cache.
    scan_response_cache_bucket_deg: float = 0.003

    admin_username: str = "admin"
    admin_password: str | None = Field(default=None, exclude=True)
    admin_password_hash: str | None = Field(default=None, exclude=True)
    admin_session_seconds: int = 12 * 3600
    admin_superusers: str | None = Field(default=None, validation_alias="ADMIN_SUPERUSERS")
    google_oauth_client_id: str | None = Field(
        default=None, validation_alias="GOOGLE_OAUTH_CLIENT_ID"
    )
    google_oauth_client_secret: str | None = Field(
        default=None, validation_alias="GOOGLE_OAUTH_CLIENT_SECRET", exclude=True
    )
    google_oauth_redirect_uri: str | None = Field(
        default=None, validation_alias="GOOGLE_OAUTH_REDIRECT_URI"
    )
    active_user_window_seconds: int = 90
    buy_me_coffee_url: str | None = "https://buymeacoffee.com/djodom"
    bmc_api_token: str | None = Field(default=None, validation_alias="BMC_API_TOKEN")
    bmc_api_base_url: str = "https://developers.buymeacoffee.com/api/v1"
    bmc_cache_seconds: int = 300
    repeat_offender_min_reports: int = 2
    repeat_offender_limit: int = 10

    max_aircraft_per_scan: int = 500
    request_timeout_seconds: float = 10.0

    @model_validator(mode="after")
    def _require_app_secret_override_in_production(self) -> "Settings":
        # Fail fast at startup: a production deploy with the default secret
        # is fully cookie-forgeable (see DEFAULT_APP_SECRET comment above).
        # Scoped to "production" only -- "local" and "test" must stay silent
        # so local dev and the test suite are unaffected.
        if self.environment == "production" and self.app_secret == DEFAULT_APP_SECRET:
            raise ValueError(
                "CIRCLEJERK_APP_SECRET must be set to a unique, non-default value "
                "when CIRCLEJERK_ENVIRONMENT=production. The default value signs "
                "the admin session cookie and the OAuth state cookie; leaving it "
                "unset makes every admin session forgeable by anyone who reads "
                "this open-source default."
            )
        return self

    def opensky_credentials(self) -> tuple[str | None, str | None]:
        if self.opensky_client_id and self.opensky_client_secret:
            return self.opensky_client_id, self.opensky_client_secret
        if self.opensky and ":" in self.opensky:
            client_id, client_secret = self.opensky.split(":", 1)
            return client_id or None, client_secret or None
        return None, None

    def live_source_priority_list(self) -> list[str]:
        allowed = {"adsb_lol", "opensky", "airplanes_live", "adsb_fi", "adsbx", "self_hosted"}
        sources = [
            source.strip().lower().replace("-", "_")
            for source in self.live_source_priority.split(",")
            if source.strip()
        ]
        ordered: list[str] = []
        for source in sources:
            if source not in allowed or source in ordered:
                continue
            if source == "adsbx" and not self.adsbx_rapidapi_key:
                continue
            if source == "self_hosted" and not self.self_hosted_feeder_base_url:
                continue
            ordered.append(source)
        return ordered


@lru_cache
def get_settings() -> Settings:
    return Settings()
