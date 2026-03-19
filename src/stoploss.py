"""Stop-loss enforcement against live positions.

Checks stored stop-loss levels against live market prices at cycle start.
Triggered stops are logged and recorded to the trades table for audit.

This module provides ADDITIONAL protection beyond the GTC stop orders
placed via OTOCO in Phase 2. It handles gap scenarios and positions
from before OTOCO was implemented.

Requirements covered: OPER-02
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

from src.db import get_db

if TYPE_CHECKING:
    from src.broker import AccountSnapshot, TastytradeClient


def check_and_enforce_stops(
    client: TastytradeClient,
    snapshot: AccountSnapshot,
    dry_run: bool = True,
) -> list[dict]:
    """Check live prices against stored stop-loss levels and enforce sells.

    For each position in the snapshot that has a stop_loss value stored in
    the SQLite positions table, fetches a live quote and compares mid-price
    against the stop level. Triggered stops are recorded to the trades table.

    Args:
        client: Authenticated TastytradeClient for live quotes.
        snapshot: Current account snapshot with position list.
        dry_run: If True, log but do not attempt to sell.

    Returns:
        List of dicts, one per triggered stop:
        {"symbol", "trigger_price", "stop_loss", "status"}
    """
    conn = get_db()
    results: list[dict] = []

    try:
        # Build map of symbol -> stop_loss from DB
        rows = conn.execute(
            "SELECT symbol, stop_loss FROM positions WHERE stop_loss IS NOT NULL"
        ).fetchall()
        stop_map: dict[str, float] = {row["symbol"]: row["stop_loss"] for row in rows}

        if not stop_map:
            logger.info("No positions with stop-loss levels in DB")
            return results

        # Check each snapshot position against stored stops
        for pos in snapshot.positions:
            symbol = pos["symbol"]
            stop_level = stop_map.get(symbol)

            if stop_level is None:
                continue

            # Fetch live quote
            try:
                bid, ask, _spread_pct = client.get_quote(symbol)
            except Exception as e:
                logger.error("Failed to get quote for {}: {}", symbol, e)
                results.append({
                    "symbol": symbol,
                    "trigger_price": None,
                    "stop_loss": stop_level,
                    "status": "skipped",
                    "reason": f"quote error: {e}",
                })
                continue

            mid = (bid + ask) / 2

            if mid <= stop_level:
                logger.warning(
                    "Stop-loss triggered for {}: price ${:.2f} <= stop ${:.2f}",
                    symbol, mid, stop_level,
                )

                # Record to trades table for audit trail regardless of mode
                _record_stop_loss_trade(
                    conn, symbol, pos["shares"], mid, stop_level
                )

                if dry_run:
                    logger.info(
                        "DRY RUN: Would sell {} shares of {} at ~${:.2f}",
                        pos["shares"], symbol, bid,
                    )
                    results.append({
                        "symbol": symbol,
                        "trigger_price": mid,
                        "stop_loss": stop_level,
                        "status": "dry_run",
                    })
                else:
                    # TODO: Add a simple sell method to broker.py (Phase 4).
                    # place_otoco_order is for opening positions, not closing.
                    # For now, log the need and mark as needing the sell method.
                    logger.warning(
                        "LIVE: Stop-loss sell needed for {} but simple sell "
                        "method not yet implemented. Recorded to trades table.",
                        symbol,
                    )
                    results.append({
                        "symbol": symbol,
                        "trigger_price": mid,
                        "stop_loss": stop_level,
                        "status": "needs_sell_method",
                    })
            else:
                logger.debug(
                    "{}: price ${:.2f} > stop ${:.2f} -- safe",
                    symbol, mid, stop_level,
                )

    finally:
        conn.close()

    if results:
        logger.info(
            "Stop-loss check complete: {} triggered out of {} monitored",
            len(results), len(stop_map),
        )
    else:
        logger.info(
            "Stop-loss check complete: 0 triggered out of {} monitored",
            len(stop_map),
        )

    return results


def _record_stop_loss_trade(
    conn,
    symbol: str,
    shares: float,
    trigger_price: float,
    stop_level: float,
) -> None:
    """Record a stop-loss event to the trades table for audit."""
    conn.execute(
        """INSERT INTO trades (executed_at, symbol, action, shares, price,
           total_value, stop_loss, reason, order_id, source)
           VALUES (?, ?, 'SELL', ?, ?, ?, ?, ?, NULL, 'stop_loss')""",
        (
            datetime.now().isoformat(),
            symbol,
            shares,
            trigger_price,
            trigger_price * shares,
            stop_level,
            f"Stop-loss triggered: price ${trigger_price:.2f} <= stop ${stop_level:.2f}",
        ),
    )
    conn.commit()
    logger.info("Recorded stop-loss trade for {} to trades table", symbol)
