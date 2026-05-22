"""HMA + Alligator intraday trend-continuation strategy.

Inspired by: web-StopCurve-Fitting.html (Kryptera / Medium, Mar 2026)

A fully-day strategy (no morning-only restriction) that fires when the
Hull Moving Average trend and Bill Williams Alligator state agree on direction.

Options signal (StrategySignal):
  Long  (CALL debit spread): HMA RISING  + Alligator EATING_UP
  Short (PUT  debit spread): HMA FALLING + Alligator EATING_DOWN
  No trade: either indicator is FLAT / SLEEPING, or they disagree

Equity signal (HmaEquitySignal):
  Same direction rules but sized for a direct equity momentum trade.
  Confidence scoring:
    base 0.60
    +0.08 if RSI confirms direction
    +0.06 if VWAP slope confirms direction
    +0.06 if VIX regime is LOW_VOL or NORMAL (calmer market)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from loguru import logger

from src.market_context import MarketContext
from src.options_strategy import StrategySignal

ET = ZoneInfo("America/New_York")

# Blocked during EXTREME VIX — no edge in crisis
_BLOCKED_REGIMES = {"EXTREME"}

# Equity momentum sizing constants
_RISK_PCT = 0.015          # 1.5% of buying power per equity trade
_ENTRY_SLIP = 0.0003       # 0.03% slippage for limit price
_RR_RATIO = 2.0            # 2:1 reward-to-risk


@dataclass
class HmaEquitySignal:
    symbol: str
    direction: str        # "LONG" or "SHORT"
    entry_price: float
    stop_price: float
    target_price: float
    shares: int
    confidence: float
    entry_reason: str
    hma_trend: str
    alligator_state: str


def evaluate_hma_alligator_signal(
    ctx: MarketContext,
    config=None,
) -> StrategySignal | None:
    """Evaluate HMA + Alligator for an options trend-continuation trade.

    Fires at any time of day (all three windows: morning/midday/close).
    Returns a StrategySignal compatible with the arbiter, or None.
    """
    if ctx.market_regime in _BLOCKED_REGIMES:
        logger.info("HMA+Alligator: blocked — regime={}", ctx.market_regime)
        return None

    hma = ctx.hma_trend
    alli = ctx.alligator_state

    if alli == "SLEEPING":
        logger.debug("HMA+Alligator: Alligator sleeping — no trade")
        return None

    if hma == "FLAT":
        logger.debug("HMA+Alligator: HMA flat — no trade")
        return None

    long_signal = hma == "RISING" and alli == "EATING_UP"
    short_signal = hma == "FALLING" and alli == "EATING_DOWN"

    if not long_signal and not short_signal:
        logger.info(
            "HMA+Alligator: indicators disagree — HMA={} Alligator={}",
            hma, alli,
        )
        return None

    direction = "CALL" if long_signal else "PUT"

    confidence = _score_confidence(ctx, direction)

    rsi_str = f"{ctx.rsi_14:.1f}" if ctx.rsi_14 is not None else "N/A"
    entry_reason = (
        f"HMA+Alligator {'LONG' if long_signal else 'SHORT'}: "
        f"{ctx.symbol} @ ${ctx.underlying_price:.2f} | "
        f"HMA={hma} Alligator={alli} RSI={rsi_str} regime={ctx.market_regime}"
    )

    logger.info(
        "HMA+Alligator: {} {} signal | HMA={} Alli={} conf={:.2f}",
        direction, ctx.symbol, hma, alli, confidence,
    )

    return StrategySignal(
        strategy="HMA_ALLIGATOR",
        direction=direction,
        symbol=ctx.symbol,
        confidence=confidence,
        entry_reason=entry_reason,
        suggested_dte=0,
        suggested_width=3.0,
        market_regime=ctx.market_regime,
    )


def evaluate_hma_equity_signal(
    ctx: MarketContext,
    buying_power: float,
) -> HmaEquitySignal | None:
    """Evaluate HMA + Alligator for a direct equity momentum trade.

    Suitable for midday scans where ORB window has closed.
    Returns HmaEquitySignal or None.
    """
    if ctx.market_regime in _BLOCKED_REGIMES:
        logger.info("HMA+Alligator equity: blocked — regime={}", ctx.market_regime)
        return None

    hma = ctx.hma_trend
    alli = ctx.alligator_state

    if alli == "SLEEPING" or hma == "FLAT":
        return None

    long_signal = hma == "RISING" and alli == "EATING_UP"
    short_signal = hma == "FALLING" and alli == "EATING_DOWN"

    if not long_signal and not short_signal:
        return None

    direction = "LONG" if long_signal else "SHORT"
    price = ctx.underlying_price

    confidence = _score_confidence(ctx, "CALL" if long_signal else "PUT")

    # Price & risk geometry
    atr_est = price * 0.004  # 0.4% of price as rough ATR proxy for 5-min intraday
    if long_signal:
        entry_price = round(price * (1 + _ENTRY_SLIP), 2)
        stop_price = round(price - atr_est, 2)
        stop_dist = entry_price - stop_price
        if stop_dist <= 0:
            return None
        target_price = round(entry_price + _RR_RATIO * stop_dist, 2)
    else:
        entry_price = round(price * (1 - _ENTRY_SLIP), 2)
        stop_price = round(price + atr_est, 2)
        stop_dist = stop_price - entry_price
        if stop_dist <= 0:
            return None
        target_price = round(entry_price - _RR_RATIO * stop_dist, 2)

    shares = max(1, int((buying_power * _RISK_PCT) / stop_dist))

    logger.info(
        "HMA+Alligator equity: {} {} | entry={:.2f} stop={:.2f} target={:.2f} shares={} conf={:.2f}",
        direction, ctx.symbol, entry_price, stop_price, target_price, shares, confidence,
    )

    return HmaEquitySignal(
        symbol=ctx.symbol,
        direction=direction,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        shares=shares,
        confidence=round(confidence, 2),
        entry_reason=(
            f"HMA+Alligator {direction}: {ctx.symbol} @ ${price:.2f} | "
            f"HMA={hma} Alligator={alli} regime={ctx.market_regime}"
        ),
        hma_trend=hma,
        alligator_state=alli,
    )


def execute_hma_equity_trade(
    client,
    signal: HmaEquitySignal,
    dry_run: bool = True,
) -> dict:
    """Place entry limit order for an HMA+Alligator momentum trade."""
    from decimal import Decimal

    logger.info(
        "HMA equity: {} {} {} shares @ ${:.2f} | stop=${:.2f} | target=${:.2f} | dry={}",
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
        result = client.place_sell_order(
            ticker=signal.symbol,
            shares=signal.shares,
            limit_price=entry_dec,
            dry_run=dry_run,
        )

    _record_hma_trade(signal, result, dry_run)

    return {
        "strategy": "HMA_ALLIGATOR",
        "symbol": signal.symbol,
        "direction": signal.direction,
        "shares": signal.shares,
        "entry_price": signal.entry_price,
        "stop_price": signal.stop_price,
        "target_price": signal.target_price,
        "hma_trend": signal.hma_trend,
        "alligator_state": signal.alligator_state,
        "confidence": signal.confidence,
        "dry_run": dry_run,
        "order_result": result,
        "status": "submitted" if not dry_run else "dry_run",
    }


def _score_confidence(ctx: MarketContext, direction: str) -> float:
    """Score mechanical confidence for HMA+Alligator signal."""
    conf = 0.60  # base: both indicators agree

    rsi = ctx.rsi_14
    slope = ctx.vwap_slope

    if direction == "CALL":
        if rsi is not None and rsi >= 52:
            conf += 0.08
        if slope is not None and slope > 0:
            conf += 0.06
    else:
        if rsi is not None and rsi <= 48:
            conf += 0.08
        if slope is not None and slope < 0:
            conf += 0.06

    if ctx.market_regime in ("LOW_VOL", "NORMAL"):
        conf += 0.06

    return min(0.90, round(conf, 2))


def _record_hma_trade(signal: HmaEquitySignal, order_result: dict, dry_run: bool) -> None:
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
                f"HMA_{signal.direction}",
                signal.shares,
                signal.entry_price,
                signal.entry_reason,
                1 if dry_run else 0,
                now,
            ),
        )
        conn.commit()
    except Exception as e:
        logger.warning("HMA equity: could not record trade: {}", e)
    finally:
        conn.close()
