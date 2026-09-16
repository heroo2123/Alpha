from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # The legacy/default product may still opt into a local .env.  Exact-SHA
    # weather-PAPER deployment sets ALPHA_DISABLE_DOTENV=1 so only the explicitly
    # attested systemd EnvironmentFile can influence runtime configuration.
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_commands_in_app: bool = True
    scan_interval_seconds: int = 15
    websocket_debounce_seconds: float = 0.35
    universe_refresh_seconds: int = 120
    universe_max_stale_seconds: int = 600
    weather_refresh_seconds: int = 60
    macro_refresh_seconds: int = 300
    actionable_min_edge: float = 0.025
    watch_min_divergence: float = 0.08
    paper_stake_usd: float = 100.0
    db_path: str = "signals.db"
    port: int = 8000
    request_timeout: float = 20.0
    max_events: int = 10000
    gamma_page_size: int = 100
    gamma_page_concurrency: int = 8
    alert_cooldown_seconds: int = 900

    structural_scan_min_interval_seconds: int = 45
    expensive_watch_min_interval_seconds: int = 180
    weather_fast_scan_min_interval_seconds: float = 1.0
    crypto_resolution_scan_min_interval_seconds: float = 1.0

    telegram_actionable_min_interval_seconds: float = 1.05
    telegram_watch_min_interval_seconds: float = 1.5
    telegram_watch_backlog_limit: int = 20
    telegram_watch_per_scan_limit: int = 3

    market_ws_enabled: bool = True
    sports_ws_enabled: bool = True
    crypto_rtds_enabled: bool = True
    ws_tokens_per_connection: int = 400

    weather_open_meteo_enabled: bool = False
    weather_lock_min_probability: float = 0.94
    weather_lock_min_local_hour: int = 14
    weather_lock_cooling_obs: int = 2
    weather_market_price_ceiling: float = 0.985

    duplicate_similarity_threshold: float = 0.90
    min_market_liquidity: float = 50.0
    wide_spread_threshold: float = 0.10
    wide_spread_min_volume_24h: float = 2000.0

    known_outcome_max_ask: float = 0.975
    sports_result_max_age_seconds: int = 120
    crypto_boundary_tolerance_seconds: int = 12
    crypto_crossfeed_watch_bps: float = 20.0

    bls_api_key: str = ""


_DISABLE_DOTENV = os.environ.get("ALPHA_DISABLE_DOTENV", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
settings = Settings(_env_file=None if _DISABLE_DOTENV else ".env")
