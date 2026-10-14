"""
Application configuration.

All configuration is read from environment variables.
Never hard-code secrets. Never commit .env.
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    # S.9.17 — the minimum number of API keys before generation runs.
    min_keys_for_generation: int = 1  # TEMP for testing
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Environment ---
    env: Literal["development", "staging", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- Security ---
    secret_key: str = Field(..., min_length=16)
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_min: int = 15
    jwt_refresh_ttl_days: int = 7

    # --- Database ---
    database_url: str = Field(..., description="asyncpg URL")
    database_url_sync: str = Field(..., description="psycopg URL for Alembic")

    # --- Redis ---
    # DB assignments:
    #   0 — Celery broker / default app cache
    #   1 — Celery result backend
    #   3 — Scraper HTTP cache (packages/trading/sources/scraper.py)
    #   4 — Rate limiter (packages/core/rate_limit.py)

    # Trading generation: per (org, subblock) per hour. The free tier
    # is deliberately tight to prevent runaway generation. Paid plans
    # get a much higher cap. Configured here so ops can tune without
    # a code change.
    trading_generate_limit_per_hour: int = 30
    trading_generate_limit_per_hour_paid: int = 60
    redis_url: str = "redis://redis:6379/0"

    # --- External API keys ---
    fred_api_key: str | None = None

    # --- Test database (optional — only loaded if set) ---
    test_database_url: str | None = None
    test_database_url_sync: str | None = None

    # --- URLs ---
    api_base_url: str = "http://localhost:8000"
    web_base_url: str = "http://localhost:3000"
    # Public product URL used in CTAs. Override in .env with
    # PRODUCT_URL=https://your-domain.com. Never default to
    # example.com — that string appears in every article if the
    # config is missing.
    product_url: str = "https://signaliq.example"

    # --- AI providers ---
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None
    groq_api_key: str | None = None

    # --- External data sources ---
    sec_user_agent: str = "SignalIQ (contact@yoursite.com)"
    stock_news_fetch_content: bool = False
    stock_news_tickers: str = "AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA,ORCL"
    stock_news_request_delay: float = 1.5

    # --- Derived ---
    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_test(self) -> bool:
        return self.env == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton settings accessor."""
    return Settings()  # type: ignore[call-arg]