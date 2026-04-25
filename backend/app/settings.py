from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CIRCLEJERK_",
        env_file=(".env", "../.env"),
        extra="ignore",
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
    opensky_poll_interval_seconds: int = 30
    opensky_anonymous_poll_interval_seconds: int = 30
    opensky_timeout_seconds: float = 12.0
    opensky_historical_enabled: bool = False
    opensky_historical_limit_seconds: int = 3600
    opensky_historical_step_seconds: int = 30
    opensky_historical_snapshots_per_scan: int = 4
    opensky_historical_backfill_interval_seconds: int = 60
    opensky_historical_cache_seconds: int = 7200

    live_source_priority: str = "adsb_lol,opensky,airplanes_live"
    live_poll_interval_seconds: int = 30
    live_source_timeout_seconds: float = 12.0
    live_source_backoff_seconds: int = 60
    live_source_rate_limit_backoff_seconds: int = 300
    live_source_max_radius_nm: float = 250.0
    bbox_merge_distance_nm: float = 5.0

    adsb_lol_base_url: str = "https://api.adsb.lol"
    airplanes_live_base_url: str = "https://api.airplanes.live"

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
        allowed = {"adsb_lol", "opensky", "airplanes_live"}
        sources = [
            source.strip().lower().replace("-", "_")
            for source in self.live_source_priority.split(",")
            if source.strip()
        ]
        return [source for source in sources if source in allowed]


@lru_cache
def get_settings() -> Settings:
    return Settings()
