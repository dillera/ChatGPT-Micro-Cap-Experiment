from __future__ import annotations

from dataclasses import dataclass


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
