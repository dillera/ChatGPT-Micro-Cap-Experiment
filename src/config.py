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

    # LLM API keys — both models routed through OpenRouter
    openrouter_api_key: str = ""

    # Financial Modeling Prep — economic calendar macro gate
    # Free tier: real-time quotes only.
    # Upgrade to starter (~$19/mo) to unlock economic_calendar endpoint.
    fmp_api_key: str = ""
    fmp_base_url: str = "https://financialmodelingprep.com/stable"
    # High-impact event names that block new trades when scheduled today.
    # Checked against the economic_calendar "event" field (case-insensitive substring).
    fmp_block_events: list[str] = [
        "FOMC", "Federal Open Market Committee",
        "CPI", "Consumer Price Index",
        "NFP", "Nonfarm Payroll", "Non-Farm Payroll",
        "PCE", "Personal Consumption Expenditure",
        "GDP", "Gross Domestic Product",
        "JOLTS", "PPI", "Producer Price Index",
        "Fed Chair", "Powell",
    ]
    # Impact filter — only block on "High" impact events (FMP field: "impact")
    fmp_min_impact: str = "High"

    # Database
    db_path: str = str(DATA_DIR / "trading_bot.db")

    # Consensus engine — bull and bear models, both routed through OpenRouter.
    # Overridable via BULL_MODEL / BEAR_MODEL env vars.
    # Defaults: Qwen3.5-Flash (bull) + Kimi K2.6 (bear) — cheap, capable, fast.
    # Fallback options: openai/gpt-4o-mini, anthropic/claude-sonnet-4-6
    bull_model: str = "qwen/qwen3.5-flash-02-23"
    bear_model: str = "moonshotai/kimi-k2.6"

    # Legacy aliases — kept so existing .env files with OPENAI_MODEL / ANTHROPIC_MODEL
    # still work without changes.
    openai_model: str = ""    # if set, overrides bull_model
    anthropic_model: str = "" # if set, overrides bear_model

    @property
    def resolved_bull_model(self) -> str:
        return self.openai_model or self.bull_model

    @property
    def resolved_bear_model(self) -> str:
        return self.anthropic_model or self.bear_model
    consensus_temperature: float = 0.3
    consensus_max_tokens: int = 8000
    min_confidence: float = 0.5
    soft_consensus_penalty: float = 0.80   # Multiply bull confidence when bear says HOLD (not SELL)
    max_candidates_per_cycle: int = 10     # Cap candidates sent to LLMs — focus beats breadth

    # Circuit breaker thresholds (Phase 3)
    max_daily_loss_pct: float = 0.10    # OPER-03: halt if daily loss > 10%
    max_drawdown_pct: float = 0.30      # OPER-04: halt if drawdown > 30% from ATH

    # Screener settings (Phase 6)
    screener_sectors: list[str] = ["biotech", "tech"]
    screener_max_market_cap: float = 300_000_000  # $300M
    screener_min_volume: int = 10_000             # 10K avg daily volume
    screener_cache_hours: int = 24                # Cache TTL
    screener_max_results_per_sector: int = 20     # Cap results

    # Research pipeline (broad multi-strategy screener)
    research_max_market_cap: float = 2_000_000_000  # $2B (small/mid cap)
    research_min_market_cap: float = 10_000_000     # $10M floor
    research_min_price: float = 2.0                 # Minimum stock price
    research_min_volume: int = 50_000               # Minimum avg daily volume
    research_max_per_run: int = 30                  # Max new symbols to add per run

    # Evaluation cooldown
    eval_cooldown_hours: int = 24  # Skip re-evaluating a symbol for this many hours

    # Position sizing
    min_trade_value: float = 10.0   # Minimum notional per trade (SIZE-04); lower if buying power is small

    # Options (put buying for bearish candidates) — legacy, kept for backward compat
    options_put_dte: int = 30
    options_max_contracts: int = 5
    options_buying_power_pct: float = 0.02
    options_min_underlying_price: float = 5.0

    # 0DTE vertical spread strategy — $100/day target
    options_daily_profit_target: float = 100.0
    options_daily_stop_loss: float = 150.0       # halt new trades if daily loss >= this
    options_max_trades_per_day: int = 3
    options_spread_width: float = 5.0            # $5-wide spreads for SPY/QQQ
    options_max_debit_pct: float = 0.50          # debit <= 50% of width (risk/reward gate)
    options_profit_close_pct: float = 0.50       # close at 50% of max profit
    options_universe: list[str] = ["SPY", "QQQ", "IWM"]
    options_credit_otm_pct: float = 0.01         # short strike ~1% OTM for credit spreads
    options_condor_otm_pct: float = 0.015        # short strikes ~1.5% OTM for iron condors (wider zone)

    # VIX-based position sizing multipliers
    vix_size_low_vol: float = 1.5    # LOW_VOL  (<15)  — cheap premium, high win rate → size up
    vix_size_normal: float = 1.0     # NORMAL (15-25)  — baseline
    vix_size_high_vol: float = 0.5   # HIGH_VOL (25-35) — expensive but risky → size down

    # Dynamic profit targets — time-of-day based close thresholds
    # Debit spreads: theta works against you — close aggressively early
    options_debit_target_early: float = 0.50    # before 11:00 ET (morning breakout — take the win)
    options_debit_target_midday: float = 0.40   # 11:00–13:00 ET
    options_debit_target_late: float = 0.30     # after 13:00 ET (theta eating value — take what's left)
    # Credit spreads: theta works for you — let it cook early, lock in late
    options_credit_target_early: float = 0.25   # before 12:00 ET (too early, let decay run)
    options_credit_target_midday: float = 0.40  # 12:00–14:00 ET
    options_credit_target_late: float = 0.60    # after 14:00 ET (rapid 0DTE decay, high achievable target)

    # Trading windows (Eastern time as HH:MM strings)
    orb_entry_start: str = "09:45"
    orb_entry_end: str = "10:15"
    midday_start: str = "11:00"
    midday_end: str = "14:00"
    preclose_start: str = "15:00"
    preclose_end: str = "15:45"
    eod_close_time: str = "15:45"               # force-close all 0DTE by this time

    # VIX thresholds
    vix_high_threshold: float = 25.0
    vix_extreme_threshold: float = 35.0

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


def reset_settings() -> None:
    """Force settings to reload from .env on next get_settings() call."""
    global _settings
    _settings = None
