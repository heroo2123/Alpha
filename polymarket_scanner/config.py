from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    scan_interval_seconds: int = 20
    universe_refresh_seconds: int = 120
    weather_refresh_seconds: int = 60
    actionable_min_edge: float = 0.025
    watch_min_divergence: float = 0.08
    paper_stake_usd: float = 100.0
    db_path: str = "signals.db"
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    port: int = 8000
    request_timeout: float = 20.0
    max_events: int = 10000
    gamma_page_size: int = 200
    max_parallel_book_requests: int = 16
    alert_cooldown_seconds: int = 900
    weather_lock_min_probability: float = 0.94
    weather_lock_min_local_hour: int = 14
    weather_lock_cooling_obs: int = 2
    weather_market_price_ceiling: float = 0.985
    duplicate_similarity_threshold: float = 0.90
    min_market_liquidity: float = 50.0
    wide_spread_threshold: float = 0.10
    wide_spread_min_volume_24h: float = 2000.0


settings = Settings()
