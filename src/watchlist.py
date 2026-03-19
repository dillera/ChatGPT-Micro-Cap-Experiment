"""Watchlist CRUD operations against SQLite.

Provides manual ticker management for the daily trading cycle.
Tickers on the watchlist are included as buy candidates alongside
screener results and LLM proposals.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.db import get_db


def add_ticker(symbol: str, notes: str | None = None) -> bool:
    """Add a ticker to the watchlist.

    If the ticker already exists but is inactive, reactivate it.

    Returns:
        True if newly activated (inserted or reactivated), False if already active.
    """
    conn = get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        # Try to insert; if symbol exists (unique index), this is a no-op
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (symbol, notes, added_at, active) "
            "VALUES (?, ?, ?, 1)",
            (symbol.upper(), notes, now),
        )
        # If the row existed but was inactive, reactivate it
        cur = conn.execute(
            "UPDATE watchlist SET active = 1, notes = COALESCE(?, notes), added_at = ? "
            "WHERE symbol = ? AND active = 0",
            (notes, now, symbol.upper()),
        )
        if cur.rowcount > 0:
            conn.commit()
            return True  # Reactivated

        # Check if the insert actually added a new row
        # (if symbol was already active, INSERT OR IGNORE did nothing)
        row = conn.execute(
            "SELECT active FROM watchlist WHERE symbol = ?",
            (symbol.upper(),),
        ).fetchone()
        if row and row["active"] == 1:
            # Was it just inserted? Check if added_at matches now
            row2 = conn.execute(
                "SELECT added_at FROM watchlist WHERE symbol = ? AND added_at = ?",
                (symbol.upper(), now),
            ).fetchone()
            if row2:
                conn.commit()
                return True  # Newly inserted
        conn.commit()
        return False  # Already active
    finally:
        conn.close()


def remove_ticker(symbol: str) -> bool:
    """Soft-delete a ticker from the watchlist (set active=0).

    Returns:
        True if a row was deactivated, False if ticker not found or already inactive.
    """
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE watchlist SET active = 0 WHERE symbol = ? AND active = 1",
            (symbol.upper(),),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_tickers() -> list[str]:
    """Return all active watchlist ticker symbols, sorted alphabetically."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT symbol FROM watchlist WHERE active = 1 ORDER BY symbol"
        ).fetchall()
        return [row["symbol"] for row in rows]
    finally:
        conn.close()


def get_active_symbols() -> list[str]:
    """Return active watchlist symbols (alias for downstream cycle integration)."""
    return list_tickers()
