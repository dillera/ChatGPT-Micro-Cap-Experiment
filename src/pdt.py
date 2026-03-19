"""PDT (Pattern Day Trader) day-trade counter.

Tracks day trades in a rolling 5-business-day window to prevent
account lockout on sub-$25K accounts.

PDT rule: 4+ day trades in 5 business days = pattern day trader.
We use a conservative limit of 2 (leaving 1-trade safety buffer).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from loguru import logger

from src.db import get_db

MAX_DAY_TRADES_PER_5_DAYS = 3  # PDT rule limit
SAFE_DAY_TRADE_LIMIT = 2       # Conservative buffer (leave 1 trade margin)
ROLLING_WINDOW_DAYS = 5


def get_day_trade_count(conn: sqlite3.Connection | None = None) -> int:
    """Count day trades in the rolling 5-business-day window."""
    should_close = conn is None
    if conn is None:
        conn = get_db()
    try:
        # 7 calendar days covers 5 business days conservatively
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM day_trade_counter WHERE traded_at >= ?",
            (cutoff,),
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        if should_close:
            conn.close()


def check_pdt_limit(conn: sqlite3.Connection | None = None) -> bool:
    """Return True if it is SAFE to make another day trade. False if at or over limit."""
    count = get_day_trade_count(conn)
    safe = count < SAFE_DAY_TRADE_LIMIT
    if not safe:
        logger.warning(
            "PDT limit reached: {count}/{max} day trades in rolling window",
            count=count,
            max=MAX_DAY_TRADES_PER_5_DAYS,
        )
    return safe


def record_day_trade(
    symbol: str,
    trade_date: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Record a day trade for PDT tracking. trade_date format: YYYY-MM-DD."""
    should_close = conn is None
    if conn is None:
        conn = get_db()
    try:
        dt = trade_date or datetime.now().strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO day_trade_counter (symbol, traded_at) VALUES (?, ?)",
            (symbol, dt),
        )
        conn.commit()
        count = get_day_trade_count(conn)
        logger.info(
            "Day trade recorded: {symbol} on {date}. Count: {count}/{max}",
            symbol=symbol,
            date=dt,
            count=count,
            max=MAX_DAY_TRADES_PER_5_DAYS,
        )
    finally:
        if should_close:
            conn.close()
