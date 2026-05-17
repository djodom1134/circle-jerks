from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    app_secret: str = "local-development-secret"

    database_path: str = "data/circlejerk.sqlite3"
    redis_url: str = "memory://"
    timezone: str = "America/Denver"

    groq_api_key: str | None = Field(default=None, validation_alias="GROQ_API_KEY")
    groq_model: str = "llama-3.3-70b-versatile"

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
    opensky_historical_limit_seconds: int = 3600
    opensky_historical_step_seconds: int = 30
    opensky_historical_snapshots_per_scan: int = 4
    opensky_historical_backfill_interval_seconds: int = 60
    opensky_historical_cache_seconds: int = 7200

    live_source_priority: str = "adsbx,self_hosted,adsb_lol,adsb_fi,airplanes_live,opensky"
    live_poll_interval_seconds: int = 10
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

    default_airport_icao: str = "KBJC"
    monitor_ttl_seconds: int = 900
    track_ttl_seconds: int = 86_400
    event_ttl_seconds: int = 86_400
    description_ttl_seconds: int = 600

    admin_username: str = "admin"
    admin_password: str | None = Field(default=None, exclude=True)
    admin_password_hash: str | None = Field(default=None, exclude=True)
    admin_session_seconds: int = 12 * 3600
    active_user_window_seconds: int = 90
    buy_me_coffee_url: str | None = "https://buymeacoffee.com/djodom"
    bmc_api_token: str | None = Field(default=None, validation_alias="BMC_API_TOKEN")
    bmc_api_base_url: str = "https://developers.buymeacoffee.com/api/v1"
    bmc_cache_seconds: int = 300
    repeat_offender_min_reports: int = 2
    repeat_offender_limit: int = 12

    max_aircraft_per_scan: int = 500
    request_timeout_seconds: float = 10.0

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
