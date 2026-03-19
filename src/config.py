from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # tastytrade OAuth2
    tastytrade_client_secret: str = ""
    tastytrade_refresh_token: str = ""

    # LLM API keys (Phase 2 will use these)
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Database
    db_path: str = str(DATA_DIR / "trading_bot.db")

    # Runtime flags
    dry_run: bool = False

    # Logging
    log_level: str = "INFO"
    log_dir: str = str(PROJECT_ROOT / "logs")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
