from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    scan_interval_seconds: int = 15  # REST/health fallback; WebSocket events wake scans immediately.
    websocket_debounce_seconds: float = 0.35
    universe_refresh_seconds: int = 120
    weather_refresh_seconds: int = 60
    macro_refresh_seconds: int = 300
    actionable_min_edge: float = 0.025
    watch_min_divergence: float = 0.08
    paper_stake_usd: float = 100.0
    db_path: str = "signals.db"
    port: int = 8000
    request_timeout: float = 20.0
    max_events: int = 10000
    # Keep Gamma list pages conservative. The API has changed validation rules over time;
    # 100 is broadly compatible and still cheap enough for full-universe pagination.
    gamma_page_size: int = 100
    alert_cooldown_seconds: int = 900

    # Live feeds
    market_ws_enabled: bool = True
    sports_ws_enabled: bool = True
    crypto_rtds_enabled: bool = True
    ws_tokens_per_connection: int = 400

    # Weather
    weather_lock_min_probability: float = 0.94
    weather_lock_min_local_hour: int = 14
    weather_lock_cooling_obs: int = 2
    weather_market_price_ceiling: float = 0.985

    # Generic market anomaly filters
    duplicate_similarity_threshold: float = 0.90
    min_market_liquidity: float = 50.0
    wide_spread_threshold: float = 0.10
    wide_spread_min_volume_24h: float = 2000.0

    # Outcome-known lag detectors
    known_outcome_max_ask: float = 0.975
    sports_result_max_age_seconds: int = 120
    crypto_boundary_tolerance_seconds: int = 12
    crypto_crossfeed_watch_bps: float = 20.0

    # Optional official BLS macro release detector. Core bot works without this.
    bls_api_key: str = ""


settings = Settings()
