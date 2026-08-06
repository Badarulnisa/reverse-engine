"""
Central configuration for the reverse-engineering framework.
Typed settings via Pydantic — catches config errors at startup,
not silently at runtime.
"""
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Redis (job queue state) ---
    redis_url: str = Field(default="redis://localhost:6379/0")

    # --- Database ---
    database_url: str = Field(default="sqlite+aiosqlite:///./reverse_engine.db")

    # --- HTTP client defaults ---
    default_timeout_seconds: float = 15.0
    default_retries: int = 3
    default_backoff_base_seconds: float = 2.0

    # --- Logging ---
    log_level: str = "INFO"

    # --- Paths ---
    root_dir: Path = Path(__file__).resolve().parent.parent.parent


settings = Settings()