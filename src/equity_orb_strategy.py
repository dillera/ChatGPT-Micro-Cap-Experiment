"""Equity Opening Range Breakout (ORB) strategy.

Inspired by: web-11k-strat.html (FXM Brand / Stephen M., Feb 2026)

A rules-based stock breakout system for the first 30-45 minutes after open.
Works with any TastyTrade account — no options spread approval needed.

Entry rules (ALL must be true):
  1. ORB has formed (first 15-min bar completed)
  2. Current price breaks above ORB high (LONG) or below ORB low (SHORT)
  3. RSI confirms direction: RSI >= 52 for LONG, RSI <= 48 for SHORT
  4. VWAP slope confirms: slope > 0 for LONG, slope < 0 for SHORT
  5. VIX regime is not EXTREME (no breakouts in crisis)
  6. Price is within the morning window (9:45–10:30 ET)

Position sizing:
  Risk 2% of buying power per trade.
  Stop = other side of ORB range.
  Shares = floor(risk_dollars / stop_distance).
  Entry = limit order 0.05% above ORB high (LONG) or below ORB low (SHORT).

Profit target (placed as a separate limit sell after entry fills):
  Target = entry + 2× stop distance (2:1 R/R per article).
  Managed via check_and_close_equity_orb() called in monitor windows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from loguru import logger

from src.market_context import MarketContext

ET = ZoneInfo("America/New_York")

# ORB equity trades only fire in this window
ORB_WINDOW_START_HOUR = 9
ORB_WINDOW_START_MIN = 45
ORB_WINDOW_END_HOUR = 10
ORB_WINDOW_END_MIN = 30

RISK_PCT = 0.02          # 2% of buying power per trade
ENTRY_SLIP = 0.0005      # 0.05% slippage above/below ORB for limit price
RR_RATIO = 2.0           # target = 2× stop distance


@dataclass
class OrbSignal:
    symbol: str
    direction: str           # "LONG" or "SHORT"
    entry_price: float       # limit price for entry order
    stop_price: float        # stop-loss price
    target_price: float      # profit target price
    shares: int              # computed from 2% risk rule
    orb_high: float
    orb_low: float
    orb_width: float
    confidence: float
    entry_reason: str


def _in_orb_window() -> bool:
    now = datetime.now(ET)
    start = now.replace(hour=ORB_WINDOW_START_HOUR, minute=ORB_WINDOW_START_MIN, second=0, microsecond=0)
    end = now.replace(hour=ORB_WINDOW_END_HOUR, minute=ORB_WINDOW_END_MIN, second=0, microsecond=0)
    return start <= now <= end


def evaluate_equity_orb_signal(
    ctx: MarketContext,
    buying_power: float,
) -> OrbSignal | None:
    """Evaluate whether an ORB breakout trade is warranted right now.

    Returns an OrbSignal or None if no trade is appropriate.
    """
    if not _in_orb_window():
        logger.debug("ORB equity: outside morning window, skipping")
        return None

    if ctx.market_regime == "EXTREME":
        logger.info("ORB equity: VIX EXTREME — skipping breakout trades")
        return None

    if not ctx.orb_formed or ctx.orb_high is None or ctx.orb_low is None:
        logger.info("ORB equity: opening range not yet formed for {}", ctx.symbol)
        return None

    price = ctx.underlying_price
    orb_high = ctx.orb_high
    orb_low = ctx.orb_low
    orb_width = orb_high - orb_low

    if orb_width <= 0:
        return None

    # Minimum range width: avoid tiny noise ranges (< 0.1% of price)
    if orb_width / price < 0.001:
        logger.info("ORB equity: range too narrow ({:.3f}%) — skipping", orb_width / price * 100)
        return None

    rsi = ctx.rsi_14
    slope = ctx.vwap_slope

    # Bullish breakout
    if ctx.above_orb:
        if rsi is not None and rsi < 52:
            logger.info("ORB equity: LONG breakout blocked — RSI {:.1f} < 52", rsi)
            return None
        if slope is not None and slope < 0:
            logger.info("ORB equity: LONG breakout blocked — VWAP slope negative")
            return None

        entry_price = round(orb_high * (1 + ENTRY_SLIP), 2)
        stop_price = round(orb_low - 0.01, 2)
        stop_dist = entry_price - stop_price
        if stop_dist <= 0:
            return None
        target_price = round(entry_price + RR_RATIO * stop_dist, 2)
        shares = max(1, int((buying_power * RISK_PCT) / stop_dist))

        conf = 0.65
        if rsi and rsi >= 55:
            conf += 0.05
        if slope and slope > 0.001:
            conf += 0.05

        return OrbSignal(
            symbol=ctx.symbol,
            direction="LONG",
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            shares=shares,
            orb_high=orb_high,
            orb_low=orb_low,
            orb_width=orb_width,
            confidence=round(conf, 2),
            entry_reason=(
                f"ORB LONG breakout: {ctx.symbol} @ ${price:.2f} > ORB high ${orb_high:.2f} "
                f"(RSI={rsi:.1f if rsi else 'N/A'}, slope={slope:.4f if slope else 'N/A'})"
            ),
        )

    # Bearish breakdown
    if ctx.below_orb:
        if rsi is not None and rsi > 48:
            logger.info("ORB equity: SHORT breakdown blocked — RSI {:.1f} > 48", rsi)
            return None
        if slope is not None and slope > 0:
            logger.info("ORB equity: SHORT breakdown blocked — VWAP slope positive")
            return None

        entry_price = round(orb_low * (1 - ENTRY_SLIP), 2)
        stop_price = round(orb_high + 0.01, 2)
        stop_dist = stop_price - entry_price
        if stop_dist <= 0:
            return None
        target_price = round(entry_price - RR_RATIO * stop_dist, 2)
        shares = max(1, int((buying_power * RISK_PCT) / stop_dist))

        conf = 0.65
        if rsi and rsi <= 45:
            conf += 0.05
        if slope and slope < -0.001:
            conf += 0.05

        return OrbSignal(
            symbol=ctx.symbol,
            direction="SHORT",
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            shares=shares,
            orb_high=orb_high,
            orb_low=orb_low,
            orb_width=orb_width,
            confidence=round(conf, 2),
            entry_reason=(
                f"ORB SHORT breakdown: {ctx.symbol} @ ${price:.2f} < ORB low ${orb_low:.2f} "
                f"(RSI={rsi:.1f if rsi else 'N/A'}, slope={slope:.4f if slope else 'N/A'})"
            ),
        )

    logger.info(
        "ORB equity: {} price ${:.2f} inside range [{:.2f} – {:.2f}], no breakout",
        ctx.symbol, price, orb_low, orb_high,
    )
    return None


def execute_equity_orb_trade(
    client,
    signal: OrbSignal,
    dry_run: bool = True,
) -> dict:
    """Place the entry limit order for an ORB breakout.

    The order is a DAY limit. Stop management is handled by
    check_and_close_equity_orb() in monitor windows.
    """
    logger.info(
        "ORB equity: {} {} {} shares @ ${:.2f} | stop=${:.2f} | target=${:.2f} | dry={}",
        signal.direction, signal.symbol, signal.shares,
        signal.entry_price, signal.stop_price, signal.target_price, dry_run,
    )

    entry_dec = Decimal(str(signal.entry_price))
    stop_dec = Decimal(str(signal.stop_price))

    if signal.direction == "LONG":
        result = client.place_otoco_order(
            ticker=signal.symbol,
            shares=signal.shares,
            limit_price=entry_dec,
            stop_price=stop_dec,
            dry_run=dry_run,
        )
    else:
        # SHORT: sell limit entry — use place_sell_order (entry) + mental stop
        # TastyTrade short equity requires margin; use sell order for simplicity
        result = client.place_sell_order(
            ticker=signal.symbol,
            shares=signal.shares,
            limit_price=entry_dec,
            dry_run=dry_run,
        )

    _record_orb_trade(signal, result, dry_run)

    return {
        "strategy": "EQUITY_ORB",
        "symbol": signal.symbol,
        "direction": signal.direction,
        "shares": signal.shares,
        "entry_price": signal.entry_price,
        "stop_price": signal.stop_price,
        "target_price": signal.target_price,
        "orb_width": signal.orb_width,
        "confidence": signal.confidence,
        "dry_run": dry_run,
        "order_result": result,
        "status": "submitted" if not dry_run else "dry_run",
    }


def _record_orb_trade(signal: OrbSignal, order_result: dict, dry_run: bool) -> None:
    """Persist ORB equity trade to the trades table for history/dashboard display."""
    from src.db import get_db
    now = datetime.now(ET).isoformat()
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO trades
               (symbol, action, shares, price, reason, dry_run, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                signal.symbol,
                f"ORB_{signal.direction}",
                signal.shares,
                signal.entry_price,
                signal.entry_reason,
                1 if dry_run else 0,
                now,
            ),
        )
        conn.commit()
    except Exception as e:
        logger.warning("ORB equity: could not record trade: {}", e)
    finally:
        conn.close()
