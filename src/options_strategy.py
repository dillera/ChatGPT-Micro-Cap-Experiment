"""Options strategy signal generation and spread selection.

Three time-windowed strategies:
  1. ORB (Opening Range Breakout) — 9:45-10:15 ET — primary
  2. Mean Reversion — 11:00-14:00 ET — fallback
  3. Pre-Close — 15:00-15:45 ET — last resort

All time checks use America/New_York timezone.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from loguru import logger

from src.market_context import MarketContext

ET = ZoneInfo("America/New_York")


@dataclass
class StrategySignal:
    strategy: str           # "ORB", "MEAN_REVERSION", "PRE_CLOSE"
    direction: str          # "CALL" or "PUT"
    symbol: str
    confidence: float       # initial mechanical confidence before LLM
    entry_reason: str
    suggested_dte: int      # 0 or 1
    suggested_width: float  # spread width in dollars


def evaluate_signal_for_window(
    ctx: MarketContext,
    window: str,
    trades_today: int,
    max_trades: int,
    config,
) -> StrategySignal | None:
    """Top-level: route to the correct strategy evaluator for the current window.

    Args:
        ctx: Current market context.
        window: "morning", "midday", or "close".
        trades_today: Number of trades already taken today.
        max_trades: Maximum trades allowed per day.
        config: Settings instance.

    Returns:
        StrategySignal or None if no signal.
    """
    if ctx.market_regime == "EXTREME":
        logger.info("VIX={:.1f} — EXTREME regime, skipping all signals", ctx.vix)
        return None

    if trades_today >= max_trades:
        logger.info("Max trades ({}) reached today, skipping signal evaluation", max_trades)
        return None

    if window == "morning":
        return evaluate_orb_signal(ctx, config)
    elif window == "midday":
        return evaluate_mean_reversion_signal(ctx, config, trades_today)
    elif window == "close":
        return evaluate_preclose_signal(ctx, config, trades_today)
    else:
        return None


def evaluate_orb_signal(ctx: MarketContext, config) -> StrategySignal | None:
    """Opening Range Breakout signal (9:45-10:15 ET).

    Entry rules:
    - Must be in orb_entry window
    - ORB must have formed (orb_high and orb_low available)
    - Price must break ORB high (call) or ORB low (put) by >= 0.15%
    - VIX must not be EXTREME

    Returns StrategySignal or None.
    """
    now_et = datetime.now(ET).time()
    entry_start = _parse_time(config.orb_entry_start)
    entry_end = _parse_time(config.orb_entry_end)

    if not (entry_start <= now_et <= entry_end):
        logger.debug("ORB: outside window ({} - {})", config.orb_entry_start, config.orb_entry_end)
        return None

    if not ctx.orb_formed or ctx.orb_high is None or ctx.orb_low is None:
        logger.info("ORB: range not yet formed")
        return None

    if ctx.underlying_price <= 0:
        return None

    breakout_up = (ctx.underlying_price - ctx.orb_high) / ctx.orb_high
    breakout_down = (ctx.orb_low - ctx.underlying_price) / ctx.orb_low

    if breakout_up >= 0.0015:
        conf = min(0.80, 0.55 + breakout_up * 10)
        reason = (
            f"Price ${ctx.underlying_price:.2f} broke ORB high ${ctx.orb_high:.2f} "
            f"by {breakout_up:.2%} — bullish breakout"
        )
        logger.info("ORB signal: CALL — {}", reason)
        return StrategySignal(
            strategy="ORB",
            direction="CALL",
            symbol=ctx.symbol,
            confidence=round(conf, 2),
            entry_reason=reason,
            suggested_dte=0,
            suggested_width=config.options_spread_width,
        )

    if breakout_down >= 0.0015:
        conf = min(0.80, 0.55 + breakout_down * 10)
        reason = (
            f"Price ${ctx.underlying_price:.2f} broke ORB low ${ctx.orb_low:.2f} "
            f"by {breakout_down:.2%} — bearish breakdown"
        )
        logger.info("ORB signal: PUT — {}", reason)
        return StrategySignal(
            strategy="ORB",
            direction="PUT",
            symbol=ctx.symbol,
            confidence=round(conf, 2),
            entry_reason=reason,
            suggested_dte=0,
            suggested_width=config.options_spread_width,
        )

    logger.info(
        "ORB: no breakout yet — up={:.3%} down={:.3%} (need >= 0.15%)",
        breakout_up, breakout_down,
    )
    return None


def evaluate_mean_reversion_signal(
    ctx: MarketContext,
    config,
    trades_today: int,
) -> StrategySignal | None:
    """Mid-day mean reversion signal (11:00-14:00 ET).

    Only fires if no morning trade was taken. Looks for extended VWAP deviation.
    Returns StrategySignal or None.
    """
    if trades_today > 0:
        logger.info("Mid-day: skipping — morning trade already taken")
        return None

    now_et = datetime.now(ET).time()
    start = _parse_time(config.midday_start)
    end = _parse_time(config.midday_end)

    if not (start <= now_et <= end):
        return None

    if ctx.vwap is None or ctx.underlying_price <= 0:
        return None

    deviation = (ctx.underlying_price - ctx.vwap) / ctx.vwap

    # Mean reversion: extended move away from VWAP, expect snap-back
    if deviation <= -0.005:
        reason = (
            f"Price ${ctx.underlying_price:.2f} is {abs(deviation):.2%} below VWAP ${ctx.vwap:.2f} "
            f"— expecting mean reversion bounce (call debit spread)"
        )
        logger.info("Mid-day signal: CALL reversion — {}", reason)
        return StrategySignal(
            strategy="MEAN_REVERSION",
            direction="CALL",
            symbol=ctx.symbol,
            confidence=0.55,
            entry_reason=reason,
            suggested_dte=0,
            suggested_width=config.options_spread_width,
        )

    if deviation >= 0.005:
        reason = (
            f"Price ${ctx.underlying_price:.2f} is {deviation:.2%} above VWAP ${ctx.vwap:.2f} "
            f"— expecting mean reversion pullback (put debit spread)"
        )
        logger.info("Mid-day signal: PUT reversion — {}", reason)
        return StrategySignal(
            strategy="MEAN_REVERSION",
            direction="PUT",
            symbol=ctx.symbol,
            confidence=0.55,
            entry_reason=reason,
            suggested_dte=0,
            suggested_width=config.options_spread_width,
        )

    logger.info("Mid-day: no mean reversion signal (VWAP deviation={:.3%})", deviation)
    return None


def evaluate_preclose_signal(
    ctx: MarketContext,
    config,
    trades_today: int,
) -> StrategySignal | None:
    """Pre-close positioning signal (15:00-15:45 ET).

    Only fires if fewer than 2 trades taken and trend is strongly intact.
    Returns StrategySignal or None.
    """
    if trades_today >= 2:
        logger.info("Pre-close: skipping — {} trades already taken", trades_today)
        return None

    now_et = datetime.now(ET).time()
    start = _parse_time(config.preclose_start)
    end = _parse_time(config.preclose_end)

    if not (start <= now_et <= end):
        return None

    if ctx.trend_bias == "NEUTRAL":
        logger.info("Pre-close: no strong trend (NEUTRAL), skipping")
        return None

    direction = "CALL" if ctx.trend_bias == "BULLISH" else "PUT"
    reason = (
        f"Pre-close positioning: {ctx.trend_bias} trend intact at ${ctx.underlying_price:.2f} "
        f"(VIX={ctx.vix:.1f})"
    )
    logger.info("Pre-close signal: {} — {}", direction, reason)

    return StrategySignal(
        strategy="PRE_CLOSE",
        direction=direction,
        symbol=ctx.symbol,
        confidence=0.55,
        entry_reason=reason,
        suggested_dte=0,
        suggested_width=config.options_spread_width,
    )


def select_spread(
    chain: dict,
    direction: str,
    underlying_price: float,
    spread_width: float,
    max_debit_pct: float,
    long_bid: float,
    long_ask: float,
    short_bid: float,
    short_ask: float,
) -> tuple[dict, dict] | None:
    """Select the long and short legs for a vertical debit spread.

    For a CALL debit spread:
      long  = ATM or 1-strike OTM call (nearest to underlying_price)
      short = long_strike + spread_width

    For a PUT debit spread:
      long  = ATM or 1-strike OTM put (nearest to underlying_price)
      short = long_strike - spread_width

    Validates that net_debit <= max_debit_pct * spread_width.

    Returns (long_leg_dict, short_leg_dict) or None if no valid spread found.
    Leg dicts contain: {occ_symbol, streamer_symbol, strike, option_type}
    """
    legs = chain.get("calls" if direction == "CALL" else "puts", [])
    if len(legs) < 2:
        logger.warning("Insufficient option legs for {} spread", direction)
        return None

    # Pick long leg: strike closest to underlying price (ATM)
    long_candidates = sorted(legs, key=lambda s: abs(s["strike"] - underlying_price))
    long_leg = long_candidates[0]

    # Find short leg at long_strike ± spread_width
    if direction == "CALL":
        target_short_strike = long_leg["strike"] + spread_width
    else:
        target_short_strike = long_leg["strike"] - spread_width

    short_candidates = sorted(legs, key=lambda s: abs(s["strike"] - target_short_strike))
    short_leg = short_candidates[0]

    if short_leg["occ_symbol"] == long_leg["occ_symbol"]:
        logger.warning("Long and short leg are the same strike — chain too sparse")
        return None

    # Compute natural net debit (worst-case fill): long_ask - short_bid
    net_debit_natural = long_ask - short_bid
    if net_debit_natural <= 0:
        logger.warning("Invalid spread: net debit <= 0 ({:.2f})", net_debit_natural)
        return None

    actual_width = abs(long_leg["strike"] - short_leg["strike"])
    if actual_width <= 0:
        return None

    if net_debit_natural > max_debit_pct * actual_width:
        logger.warning(
            "Spread rejected: debit ${:.2f} > {:.0%} of ${:.1f} width",
            net_debit_natural, max_debit_pct, actual_width,
        )
        return None

    logger.info(
        "{} spread: long {}@${:.2f} / short {}@${:.2f} | debit~${:.2f} | width=${:.1f}",
        direction,
        long_leg["occ_symbol"], long_leg["strike"],
        short_leg["occ_symbol"], short_leg["strike"],
        net_debit_natural, actual_width,
    )
    return long_leg, short_leg


def compute_spread_contracts(
    buying_power: float,
    debit_per_contract_dollars: float,
    max_loss_per_trade: float,
    max_contracts: int = 5,
) -> int:
    """Compute contract count bounded by per-trade risk cap.

    max_loss for a debit spread = debit paid (defined risk).
    contracts = floor(max_loss_per_trade / debit_per_contract_dollars)
    Minimum 1, maximum max_contracts.
    """
    if debit_per_contract_dollars <= 0:
        return 0
    contracts = int(max_loss_per_trade / debit_per_contract_dollars)
    return max(1, min(contracts, max_contracts))


def _parse_time(t_str: str) -> time:
    """Parse 'HH:MM' string to a datetime.time object."""
    h, m = t_str.split(":")
    return time(int(h), int(m))
