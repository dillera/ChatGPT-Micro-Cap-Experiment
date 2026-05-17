from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field


@dataclass
class Position:
    symbol: str
    shares: float
    buy_price: float
    cost_basis: float
    stop_loss: float | None = None
    opened_at: str = ""       # ISO datetime string
    updated_at: str = ""      # ISO datetime string
    id: int | None = None


@dataclass
class Trade:
    executed_at: str           # ISO datetime string
    symbol: str
    action: str                # "BUY" or "SELL"
    shares: float
    price: float
    total_value: float
    commission: float = 0.0
    stop_loss: float | None = None
    reason: str | None = None
    order_id: str | None = None
    source: str = "manual"     # "llm_consensus", "stop_loss", "manual", "csv_migration"
    id: int | None = None


@dataclass
class DailySnapshot:
    snapshot_date: str         # YYYY-MM-DD
    total_equity: float
    cash_balance: float
    positions_value: float
    daily_pnl: float
    daily_pnl_pct: float
    peak_equity: float
    drawdown_pct: float
    id: int | None = None


@dataclass
class CircuitBreakerState:
    status: str = "ACTIVE"     # "ACTIVE", "HALTED_DAILY", "HALTED_DRAWDOWN"
    tripped_at: str | None = None
    reason: str | None = None
    reset_at: str | None = None
    id: int = 1


@dataclass
class DayTradeRecord:
    symbol: str
    traded_at: str             # YYYY-MM-DD
    id: int | None = None


# ---------------------------------------------------------------------------
# Pydantic models for LLM structured output (Phase 2 consensus engine)
# ---------------------------------------------------------------------------


@dataclass
class SpreadLeg:
    occ_symbol: str
    strike: float
    expiry: str
    dte: int
    action: str          # "BUY_TO_OPEN" or "SELL_TO_OPEN"
    option_type: str     # "C" or "P"
    contracts: int


@dataclass
class SpreadPosition:
    symbol: str                  # underlying, e.g. "SPY"
    spread_type: str             # "CALL_DEBIT" or "PUT_DEBIT"
    long_strike: float
    short_strike: float
    expiry: str
    dte_at_open: int
    contracts: int
    debit_paid: float            # per-contract cost in dollars (e.g. 1.85)
    max_profit: float            # (width - debit_paid) * 100 per contract
    max_loss: float              # debit_paid * 100 per contract
    target_exit_pct: float       # close at this fraction of max_profit
    opened_at: str
    long_occ: str
    short_occ: str
    daily_session: str           # "morning", "midday", "close"
    closed_at: str | None = None
    status: str = "OPEN"         # "OPEN", "CLOSED", "EXPIRED"
    order_id: str | None = None
    entry_delta: float | None = None
    id: int | None = None


@dataclass
class DailyTargetState:
    target_date: str             # YYYY-MM-DD
    target_amount: float
    realized_pnl: float
    unrealized_pnl: float
    trades_today: int
    max_trades: int
    target_hit: bool
    stop_loss_hit: bool
    id: int | None = None


# ---------------------------------------------------------------------------
# Pydantic models for LLM structured output (Phase 2 consensus engine)
# ---------------------------------------------------------------------------


class TradeRecommendation(BaseModel):
    """Schema for LLM trading response. Used with native structured output."""

    action: Literal["BUY", "SELL", "HOLD", "BUY_PUT"]
    symbol: str = Field(description="Ticker symbol, e.g. RXRX")
    confidence: float = Field(description="Conviction level between 0.0 and 1.0")
    stop_loss_pct: float = Field(
        description="Stop loss as decimal fraction below entry, e.g. 0.08 for 8%. Between 0.01 and 0.50."
    )
    reasoning: str = Field(description="2-3 sentence rationale")


class TradingAnalysis(BaseModel):
    """Top-level response schema for each LLM."""

    market_assessment: str = Field(description="Brief overall market assessment")
    recommendations: list[TradeRecommendation]


class ConsensusResult(BaseModel):
    """Output of the consensus engine for downstream consumers."""

    approved_trades: list[TradeRecommendation]
    bull_analysis: TradingAnalysis
    bear_analysis: TradingAnalysis
    all_symbols_seen: list[str]
    disagreed_symbols: list[str]


# ---------------------------------------------------------------------------
# Options-specific Pydantic models (0DTE vertical spread strategy)
# ---------------------------------------------------------------------------


class SpreadRecommendation(BaseModel):
    """LLM output for options consensus cycle."""

    action: Literal["BUY_CALL_SPREAD", "BUY_PUT_SPREAD", "HOLD"]
    symbol: str = Field(description="Underlying ticker, e.g. SPY")
    confidence: float = Field(description="Conviction level between 0.0 and 1.0")
    spread_width: float = Field(description="Width of the spread in dollars, e.g. 5.0")
    target_dte: int = Field(description="Target days to expiration, 0 or 1")
    reasoning: str = Field(description="2-3 sentence rationale")
    entry_window: str = Field(description="Entry window: morning_orb, midday_reversion, or pre_close")


class OptionsAnalysis(BaseModel):
    """Top-level options analysis response from each LLM."""

    market_assessment: str = Field(description="Brief overall market and volatility assessment")
    recommendations: list[SpreadRecommendation]


class OptionsConsensusResult(BaseModel):
    """Output of the options consensus engine."""

    approved_trades: list[SpreadRecommendation]
    bull_analysis: OptionsAnalysis
    bear_analysis: OptionsAnalysis
    disagreed: bool
