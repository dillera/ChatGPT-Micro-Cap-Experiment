"""Data-fetching helpers for the Streamlit dashboard.

Provides portfolio position/P&L queries, portfolio summary from daily
snapshots, and manual circuit breaker reset -- all backed by SQLite.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.db import get_db


def get_positions_with_pnl() -> list[dict]:
    """Return all open positions as dicts suitable for st.dataframe().

    P&L is a placeholder (0.0) because the dashboard reads from SQLite
    which is synced by the trading cycle.  Portfolio-level P&L comes
    from the daily_snapshots table instead.
    """
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM positions ORDER BY symbol"
        ).fetchall()
        positions = []
        for row in rows:
            positions.append(
                {
                    "symbol": row["symbol"],
                    "shares": row["shares"],
                    "buy_price": row["buy_price"],
                    "cost_basis": row["cost_basis"],
                    "stop_loss": row["stop_loss"],
                    "opened_at": row["opened_at"],
                    "current_value": row["shares"] * row["buy_price"],
                    "pnl": 0.0,
                }
            )
        return positions
    finally:
        conn.close()


def get_portfolio_summary() -> dict:
    """Return the latest daily snapshot as a dict.

    If no snapshots exist yet, returns zeroed-out placeholder values.
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM daily_snapshots ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
        if row:
            return {
                "total_equity": row["total_equity"],
                "cash_balance": row["cash_balance"],
                "positions_value": row["positions_value"],
                "daily_pnl": row["daily_pnl"],
                "daily_pnl_pct": row["daily_pnl_pct"],
                "peak_equity": row["peak_equity"],
                "drawdown_pct": row["drawdown_pct"],
                "snapshot_date": row["snapshot_date"],
            }
        return {
            "total_equity": 0.0,
            "cash_balance": 0.0,
            "positions_value": 0.0,
            "daily_pnl": 0.0,
            "daily_pnl_pct": 0.0,
            "peak_equity": 0.0,
            "drawdown_pct": 0.0,
            "snapshot_date": "N/A",
        }
    finally:
        conn.close()


def reset_circuit_breaker() -> None:
    """Manually reset the circuit breaker to ACTIVE status.

    Mirrors the OVERRIDE_CIRCUIT_BREAKER=1 logic from cycle.py.
    Clears tripped_at, reason, and sets reset_at to current timestamp.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        conn.execute(
            "UPDATE circuit_breaker SET status='ACTIVE', reset_at=?, "
            "reason=NULL, tripped_at=NULL WHERE id=1",
            (now,),
        )
        conn.commit()
    finally:
        conn.close()
