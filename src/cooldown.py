"""Symbol evaluation cooldown tracking.

After the consensus engine evaluates a symbol, it is marked with a cooldown
timestamp. Symbols inside their cooldown window are filtered out of the
candidate list, saving LLM tokens on symbols that were just analysed.

Default cooldown: 24 hours (EVAL_COOLDOWN_HOURS in config).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger

from src.config import get_settings
from src.db import get_db


def mark_evaluated(symbols: list[str]) -> None:
    """Record that these symbols were just evaluated. Starts/resets their cooldown."""
    if not symbols:
        return
    settings = get_settings()
    now = datetime.now(timezone.utc)
    next_eval = now + timedelta(hours=settings.eval_cooldown_hours)
    conn = get_db()
    try:
        conn.executemany(
            """INSERT OR REPLACE INTO symbol_cooldown (symbol, last_evaluated_at, next_eval_at)
               VALUES (?, ?, ?)""",
            [(sym.upper(), now.isoformat(), next_eval.isoformat()) for sym in symbols],
        )
        conn.commit()
        logger.info(
            "Cooldown set for {} symbol(s) — next eval after {}",
            len(symbols), next_eval.strftime("%Y-%m-%d %H:%M UTC"),
        )
    finally:
        conn.close()


def filter_cooled_down(symbols: list[str]) -> list[str]:
    """Return only symbols whose cooldown has expired (or were never evaluated).

    Symbols still within their cooldown window are dropped and logged.
    """
    if not symbols:
        return []
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        placeholders = ",".join("?" * len(symbols))
        rows = conn.execute(
            f"SELECT symbol, next_eval_at FROM symbol_cooldown WHERE symbol IN ({placeholders})",
            [s.upper() for s in symbols],
        ).fetchall()
    finally:
        conn.close()

    cooled = {row["symbol"]: row["next_eval_at"] for row in rows if row["next_eval_at"] > now}
    active = [s for s in symbols if s.upper() not in cooled]

    if cooled:
        logger.info(
            "Cooldown active for {} symbol(s), skipping: {}",
            len(cooled), sorted(cooled.keys()),
        )

    return active
