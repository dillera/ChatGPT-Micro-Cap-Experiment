from __future__ import annotations

from dataclasses import dataclass
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


class TradeRecommendation(BaseModel):
    """Schema for LLM trading response. Used with native structured output."""

    action: Literal["BUY", "SELL", "HOLD"]
    symbol: str = Field(description="Ticker symbol, e.g. RXRX")
    confidence: float = Field(ge=0.0, le=1.0, description="0.0-1.0 conviction level")
    stop_loss_pct: float = Field(
        ge=0.01, le=0.50, description="Stop loss as % below entry"
    )
    reasoning: str = Field(max_length=500, description="2-3 sentence rationale")


class TradingAnalysis(BaseModel):
    """Top-level response schema for each LLM."""

    market_assessment: str = Field(max_length=300)
    recommendations: list[TradeRecommendation]


class ConsensusResult(BaseModel):
    """Output of the consensus engine for downstream consumers."""

    approved_trades: list[TradeRecommendation]
    bull_analysis: TradingAnalysis
    bear_analysis: TradingAnalysis
    all_symbols_seen: list[str]
    disagreed_symbols: list[str]
