"""Options strategy signal generation and spread selection.

Four time-windowed strategies:
  1. ORB (Opening Range Breakout) — 9:45-10:15 ET — primary
  2. Mean Reversion — 11:00-14:00 ET — fallback
  3. Pre-Close — 15:00-15:45 ET — last resort
  4. Credit Spread — any window — fires when no debit signal fires

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
    strategy: str           # "ORB", "MEAN_REVERSION", "PRE_CLOSE", "CREDIT_PUT", "CREDIT_CALL", "IRON_CONDOR"
    direction: str          # "CALL", "PUT", or "NEUTRAL" (iron condor)
    symbol: str
    confidence: float       # initial mechanical confidence before LLM
    entry_reason: str
    suggested_dte: int      # 0 or 1
    suggested_width: float  # spread width in dollars
    market_regime: str = "NORMAL"  # from MarketContext — drives VIX position sizing


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
        ok, mom_reason = passes_momentum_filter(ctx, "CALL", is_credit=False)
        if not ok:
            logger.info("ORB CALL signal suppressed by momentum filter: {}", mom_reason)
            return None
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
            market_regime=ctx.market_regime,
        )

    if breakout_down >= 0.0015:
        ok, mom_reason = passes_momentum_filter(ctx, "PUT", is_credit=False)
        if not ok:
            logger.info("ORB PUT signal suppressed by momentum filter: {}", mom_reason)
            return None
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
            market_regime=ctx.market_regime,
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
        ok, mom_reason = passes_momentum_filter(ctx, "CALL", is_credit=False)
        if not ok:
            logger.info("Mid-day CALL reversion suppressed by momentum filter: {}", mom_reason)
            return None
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
            market_regime=ctx.market_regime,
        )

    if deviation >= 0.005:
        ok, mom_reason = passes_momentum_filter(ctx, "PUT", is_credit=False)
        if not ok:
            logger.info("Mid-day PUT reversion suppressed by momentum filter: {}", mom_reason)
            return None
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
            market_regime=ctx.market_regime,
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

    ok, mom_reason = passes_momentum_filter(ctx, direction, is_credit=False)
    if not ok:
        logger.info("Pre-close {} signal suppressed by momentum filter: {}", direction, mom_reason)
        return None

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
        market_regime=ctx.market_regime,
    )


def passes_momentum_filter(
    ctx: MarketContext,
    direction: str,
    is_credit: bool = False,
) -> tuple[bool, str]:
    """RSI + VWAP-slope momentum gate applied before any spread entry.

    Debit spreads require momentum to confirm direction:
      CALL debit: RSI >= 45 AND vwap_slope >= 0
      PUT  debit: RSI <= 55 AND vwap_slope <= 0

    Credit spreads only reject on extreme opposing momentum:
      PUT credit  (bull put):  RSI >= 30 AND vwap_slope >= -0.001
      CALL credit (bear call): RSI <= 70 AND vwap_slope <= +0.001

    If both indicators are unavailable the filter passes (fail-open).
    Returns (passes: bool, reason: str).
    """
    rsi = ctx.rsi_14
    slope = ctx.vwap_slope

    if rsi is None and slope is None:
        return True, "momentum data unavailable — filter bypassed"

    if is_credit:
        if direction == "NEUTRAL":  # iron condor — block strong trending in either direction
            if rsi is not None and (rsi < 35 or rsi > 65):
                return False, f"iron condor blocked: RSI {rsi:.1f} outside 35–65 (market trending)"
            if slope is not None and abs(slope) > 0.001:
                return False, f"iron condor blocked: |VWAP slope| {abs(slope):.4f} > 0.001 (trending)"
        elif direction == "PUT":
            if rsi is not None and rsi < 30:
                return False, f"credit PUT blocked: RSI {rsi:.1f} < 30 (bearish momentum too strong)"
            if slope is not None and slope < -0.001:
                return False, f"credit PUT blocked: VWAP slope {slope:.4f} < -0.001 (strong downtrend)"
        else:  # CALL credit
            if rsi is not None and rsi > 70:
                return False, f"credit CALL blocked: RSI {rsi:.1f} > 70 (bullish momentum too strong)"
            if slope is not None and slope > 0.001:
                return False, f"credit CALL blocked: VWAP slope {slope:.4f} > 0.001 (strong uptrend)"
    else:
        if direction == "CALL":
            if rsi is not None and rsi < 45:
                return False, f"debit CALL blocked: RSI {rsi:.1f} < 45 (insufficient bullish momentum)"
            if slope is not None and slope < 0:
                return False, f"debit CALL blocked: VWAP slope {slope:.4f} < 0 (downward slope)"
        else:  # PUT debit
            if rsi is not None and rsi > 55:
                return False, f"debit PUT blocked: RSI {rsi:.1f} > 55 (insufficient bearish momentum)"
            if slope is not None and slope > 0:
                return False, f"debit PUT blocked: VWAP slope {slope:.4f} > 0 (upward slope)"

    return True, "momentum filter passed"


def evaluate_credit_spread_signal(ctx: MarketContext, config) -> StrategySignal | None:
    """Credit spread fallback signal — fires when no debit signal triggers.

    Sells an OTM spread to collect theta decay. High win rate in range-bound markets.

    Bull put spread (PUT_CREDIT): BULLISH or NEUTRAL bias — sell OTM put below price.
    Bear call spread (CALL_CREDIT): BEARISH bias — sell OTM call above price.

    Skipped if VIX regime is HIGH_VOL or EXTREME (credit spreads need defined, calm vol).
    """
    if ctx.market_regime in ("HIGH_VOL", "EXTREME"):
        logger.info(
            "Credit spread: skipping — {} regime (VIX={:.1f})", ctx.market_regime, ctx.vix
        )
        return None

    if ctx.underlying_price <= 0:
        return None

    if ctx.trend_bias == "BEARISH":
        direction = "CALL"
        strategy = "CREDIT_CALL"
        reason = (
            f"Credit call spread: {ctx.trend_bias} bias at ${ctx.underlying_price:.2f} "
            f"(VIX={ctx.vix:.1f}) — selling OTM call {config.options_credit_otm_pct:.0%} above price"
        )
    else:
        direction = "PUT"
        strategy = "CREDIT_PUT"
        reason = (
            f"Credit put spread: {ctx.trend_bias} bias at ${ctx.underlying_price:.2f} "
            f"(VIX={ctx.vix:.1f}) — selling OTM put {config.options_credit_otm_pct:.0%} below price"
        )

    ok, mom_reason = passes_momentum_filter(ctx, direction, is_credit=True)
    if not ok:
        logger.info("Credit spread {} suppressed by momentum filter: {}", strategy, mom_reason)
        return None

    logger.info("Credit spread signal: {} — {}", strategy, reason)
    return StrategySignal(
        strategy=strategy,
        direction=direction,
        symbol=ctx.symbol,
        confidence=0.60,
        entry_reason=reason,
        suggested_dte=0,
        suggested_width=config.options_spread_width,
        market_regime=ctx.market_regime,
    )


def get_vix_size_multiplier(market_regime: str, config=None) -> float:
    """Scale contract count by VIX regime.

    LOW_VOL: cheap premium + high win rate → size up.
    NORMAL:  baseline.
    HIGH_VOL: expensive but risky → size down.
    EXTREME: blocked upstream by circuit breaker — returns 1.0 as safety default.
    """
    if config is None:
        from src.config import get_settings
        config = get_settings()
    if market_regime == "LOW_VOL":
        return config.vix_size_low_vol
    if market_regime == "HIGH_VOL":
        return config.vix_size_high_vol
    return config.vix_size_normal


def compute_implied_move_otm_pct(
    atm_call_quotes: tuple[float, float],
    atm_put_quotes: tuple[float, float],
    underlying_price: float,
    floor_pct: float = 0.003,
    ceiling_pct: float = 0.030,
) -> float:
    """Compute IV-implied OTM % for condor short strikes from ATM straddle price.

    The ATM straddle mid ≈ market's expected 1-sigma daily move in dollar terms.
    Dividing by underlying price converts it to a fraction suitable as an OTM offset.

    Args:
        atm_call_quotes: (bid, ask) for the ATM call.
        atm_put_quotes:  (bid, ask) for the ATM put.
        underlying_price: Current price of the underlying.
        floor_pct:  Minimum OTM fraction (default 0.3%) — protects against stale/thin quotes.
        ceiling_pct: Maximum OTM fraction (default 3.0%) — caps overly wide placement.

    Returns:
        OTM fraction clipped to [floor_pct, ceiling_pct]. Falls back to floor_pct
        if quotes are zero or underlying_price is invalid.
    """
    if underlying_price <= 0:
        return floor_pct

    call_mid = (atm_call_quotes[0] + atm_call_quotes[1]) / 2
    put_mid = (atm_put_quotes[0] + atm_put_quotes[1]) / 2
    straddle_mid = call_mid + put_mid

    if straddle_mid <= 0:
        logger.warning("IV-implied move: straddle mid is zero — using floor {:.1%}", floor_pct)
        return floor_pct

    implied_pct = straddle_mid / underlying_price
    clipped = max(floor_pct, min(implied_pct, ceiling_pct))

    logger.info(
        "IV-implied move: straddle_mid=${:.2f} → {:.2%} (raw={:.2%}, clipped=[{:.1%},{:.1%}])",
        straddle_mid, clipped, implied_pct, floor_pct, ceiling_pct,
    )
    return clipped


def evaluate_iron_condor_signal(ctx: MarketContext, config) -> StrategySignal | None:
    """Iron condor signal — sell OTM put spread + OTM call spread simultaneously.

    Requires LOW_VOL regime AND NEUTRAL trend bias. Short strike placement uses
    the ATM straddle-implied expected move (fetched at execution time in the
    executor) rather than a fixed OTM percentage.
    """
    if ctx.market_regime != "LOW_VOL":
        logger.info(
            "Iron condor: skipping — {} regime (need LOW_VOL, VIX={:.1f})",
            ctx.market_regime, ctx.vix,
        )
        return None

    if ctx.trend_bias != "NEUTRAL":
        logger.info("Iron condor: skipping — {} bias (need NEUTRAL)", ctx.trend_bias)
        return None

    if ctx.underlying_price <= 0:
        return None

    ok, mom_reason = passes_momentum_filter(ctx, "NEUTRAL", is_credit=True)
    if not ok:
        logger.info("Iron condor suppressed by momentum filter: {}", mom_reason)
        return None

    reason = (
        f"Iron condor: LOW_VOL (VIX={ctx.vix:.1f}) + NEUTRAL bias at ${ctx.underlying_price:.2f} "
        f"— short strikes placed at IV-implied expected move (fallback {config.options_condor_otm_pct:.1%})"
    )
    logger.info("Iron condor signal — {}", reason)
    return StrategySignal(
        strategy="IRON_CONDOR",
        direction="NEUTRAL",
        symbol=ctx.symbol,
        confidence=0.65,
        entry_reason=reason,
        suggested_dte=0,
        suggested_width=config.options_spread_width,
        market_regime=ctx.market_regime,
    )


def select_credit_spread(
    chain: dict,
    direction: str,
    underlying_price: float,
    spread_width: float,
    otm_pct: float,
) -> tuple[dict, dict] | None:
    """Select short and long legs for a vertical credit spread.

    For a PUT credit spread (bull put):
      short = put closest to underlying_price * (1 - otm_pct)  [we SELL this]
      long  = put closest to short_strike - spread_width        [protection]

    For a CALL credit spread (bear call):
      short = call closest to underlying_price * (1 + otm_pct) [we SELL this]
      long  = call closest to short_strike + spread_width       [protection]

    Returns (short_leg_dict, long_leg_dict) or None.
    """
    legs = chain.get("puts" if direction == "PUT" else "calls", [])
    if len(legs) < 2:
        logger.warning("Insufficient {} legs for credit spread", direction)
        return None

    if direction == "PUT":
        target_short_strike = underlying_price * (1 - otm_pct)
        target_long_strike_fn = lambda s: s - spread_width
    else:
        target_short_strike = underlying_price * (1 + otm_pct)
        target_long_strike_fn = lambda s: s + spread_width

    short_candidates = sorted(legs, key=lambda s: abs(s["strike"] - target_short_strike))
    short_leg = short_candidates[0]

    target_long = target_long_strike_fn(short_leg["strike"])
    long_candidates = sorted(legs, key=lambda s: abs(s["strike"] - target_long))
    long_leg = long_candidates[0]

    if short_leg["occ_symbol"] == long_leg["occ_symbol"]:
        logger.warning("Credit spread: short and long leg are the same strike")
        return None

    actual_width = abs(short_leg["strike"] - long_leg["strike"])
    if actual_width <= 0:
        return None

    logger.info(
        "{} credit spread: short {}@${:.2f} / long {}@${:.2f} | width=${:.1f}",
        direction,
        short_leg["occ_symbol"], short_leg["strike"],
        long_leg["occ_symbol"], long_leg["strike"],
        actual_width,
    )
    return short_leg, long_leg


def compute_credit_spread_contracts(
    max_loss_per_trade: float,
    width: float,
    net_credit: float,
    max_contracts: int = 5,
) -> int:
    """Compute contract count for a credit spread bounded by per-trade max loss.

    max_loss per contract = (width - net_credit) * 100
    contracts = floor(max_loss_per_trade / max_loss_per_contract)
    """
    max_loss_per_contract = (width - net_credit) * 100
    if max_loss_per_contract <= 0:
        return 0
    contracts = int(max_loss_per_trade / max_loss_per_contract)
    return max(1, min(contracts, max_contracts))


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
