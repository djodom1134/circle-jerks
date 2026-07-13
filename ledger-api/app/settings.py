from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEDGER_",
        env_file=(".env", "../.env"),
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "The Lost Landing — ledger sidecar"
    environment: str = "local"

    # The main circlejerks API's database. Opened READ-ONLY -- see
    # app/db.py's module docstring. This service never creates or migrates it.
    production_database_path: str = "../backend/data/circlejerk.sqlite3"

    # This service's OWN database. daily_operation_rollup and
    # aircraft_home_base live here, nowhere else.
    ledger_database_path: str = "data/ledger.sqlite3"

    # adsb_lol / opensky / etc — the credits block published in `methodology`.
    # Mirrors the main API's `live_source_priority`, since it describes the
    # SAME deployment's data sources, not something this service ingests itself.
    live_source_priority: str = "adsbx,self_hosted,adsb_lol,adsb_fi,airplanes_live,opensky"
    adsbx_rapidapi_key: str | None = None
    self_hosted_feeder_base_url: str | None = None

    # Nightly recompute cadence. Matches the interval Task 7 shipped on the
    # main worker before this became a standalone service.
    ledger_interval_seconds: int = 6 * 3600
    ledger_rollup_window_days: int = 3

    cors_allow_origins: str = "http://localhost:5174,http://127.0.0.1:5174"

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

    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
