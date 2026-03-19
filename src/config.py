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

    # tastytrade OAuth2 (SDK reads TT_SECRET and TT_REFRESH directly from env)
    tt_client_id: str = ""
    tt_secret: str = ""
    tt_refresh: str = ""

    # LLM API keys (Phase 2 will use these)
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Database
    db_path: str = str(DATA_DIR / "trading_bot.db")

    # Consensus engine
    openai_model: str = "gpt-5.4-mini"
    anthropic_model: str = "claude-sonnet-4-6"
    consensus_temperature: float = 0.3
    consensus_max_tokens: int = 2000
    min_confidence: float = 0.6

    # Circuit breaker thresholds (Phase 3)
    max_daily_loss_pct: float = 0.10    # OPER-03: halt if daily loss > 10%
    max_drawdown_pct: float = 0.30      # OPER-04: halt if drawdown > 30% from ATH

    # Screener settings (Phase 6)
    screener_sectors: list[str] = ["biotech", "tech"]
    screener_max_market_cap: float = 300_000_000  # $300M
    screener_min_volume: int = 10_000             # 10K avg daily volume
    screener_cache_hours: int = 24                # Cache TTL
    screener_max_results_per_sector: int = 20     # Cap results

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
