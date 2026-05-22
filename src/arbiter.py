"""Multi-strategy signal arbiter — picks the highest-conviction trade.

Inspired by: web-arbiter.html (Sips & Scale, ILLUMINATION, Mar 2026)

Instead of a cascade (first signal wins), the arbiter evaluates ALL
strategy signals simultaneously and scores each using:
  1. Vote share       — directional agreement across strategies
  2. Breadth          — how many independent strategies agree
  3. Contradiction    — penalizes disagreement between signals
  4. tanh squash      — compresses raw score to stable [-1, 1] range

A signal only wins if its conviction score clears a minimum threshold (0.55).
All considered signals and their scores are logged for the dashboard audit trail.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from loguru import logger

from src.options_strategy import StrategySignal


# Market bias by strategy name — NOT by the option leg type.
# A CREDIT_PUT (bull put spread) is BULLISH even though it uses PUT legs.
# A CREDIT_CALL (bear call spread) is BEARISH even though it uses CALL legs.
_STRATEGY_BIAS = {
    # Debit spreads: directional, bias matches the leg
    "DEBIT_CALL":    +1.0,
    "DEBIT_PUT":     -1.0,
    # Credit spreads: bias is opposite to the leg sold
    "CREDIT_PUT":    +1.0,   # bull put spread → bullish
    "CREDIT_CALL":   -1.0,   # bear call spread → bearish
    # Iron condor: market-neutral
    "IRON_CONDOR":    0.0,
    # Debit sub-strategies (window-specific labels from evaluate_signal_for_window)
    "ORB":           +1.0,   # resolved at runtime by direction field below
    "MEAN_REVERSION": 0.0,   # resolved at runtime
    "PRE_CLOSE":     +1.0,   # resolved at runtime
    # HMA + Alligator trend-continuation: resolved at runtime by direction
    "HMA_ALLIGATOR": +1.0,
}


def _market_bias(signal: StrategySignal) -> float:
    """Return +1 (bullish), -1 (bearish), or 0 (neutral) for a signal."""
    if signal.strategy in _STRATEGY_BIAS:
        bias = _STRATEGY_BIAS[signal.strategy]
        # For runtime-resolved strategies, use the direction field
        if signal.strategy in ("ORB", "MEAN_REVERSION", "PRE_CLOSE", "HMA_ALLIGATOR"):
            bias = +1.0 if signal.direction == "CALL" else -1.0
        return bias
    # Fallback: infer from option leg direction
    return +1.0 if signal.direction == "CALL" else (-1.0 if signal.direction == "PUT" else 0.0)

# Number of distinct strategy families we evaluate each cycle
_TOTAL_STRATEGIES = 4  # debit, condor, credit, hma_alligator


@dataclass
class ArbiterResult:
    winner: StrategySignal | None
    conviction: float               # tanh-squashed score for winner [0, 1]
    all_scores: list[dict] = field(default_factory=list)  # audit log
    rejection_reason: str | None = None


def _score(signal: StrategySignal, all_signals: list[StrategySignal]) -> float:
    """Compute tanh conviction score for one signal given the full slate.

    Follows the web-arbiter.html formula:
      vote      = directional alignment with all other signals
      breadth   = fraction of strategy families that emitted a signal
      contrad   = contradiction penalty (mixed directions = expensive)
      raw       = vote * div_conf − 0.4 * contrad
      score     = tanh(1.6 * raw)
    """
    my_dir = _market_bias(signal)

    long_weight = sum(
        s.confidence for s in all_signals if _market_bias(s) > 0
    )
    short_weight = sum(
        s.confidence for s in all_signals if _market_bias(s) < 0
    )
    neutral_weight = sum(
        s.confidence for s in all_signals if _market_bias(s) == 0
    )
    total = max(1e-9, long_weight + short_weight + neutral_weight)

    # Signed vote share from this signal's perspective
    if my_dir > 0:
        aligned = long_weight
        opposed = short_weight
    elif my_dir < 0:
        aligned = short_weight
        opposed = long_weight
    else:
        # Condor wins when both directional camps are weak
        aligned = neutral_weight + 0.5 * (total - long_weight - short_weight)
        opposed = max(long_weight, short_weight)

    vote = (aligned - opposed) / total

    # Breadth: how many strategy families fired vs total possible
    unique_strategies = len({s.strategy.split("_")[0] for s in all_signals})
    breadth = unique_strategies / _TOTAL_STRATEGIES
    div_conf = 0.5 + 0.5 * breadth

    # Contradiction: fraction of weight on the opposing side
    contrad = opposed / total

    raw = vote * div_conf - 0.4 * contrad

    # Scale by the signal's own mechanical confidence
    raw *= signal.confidence

    return math.tanh(1.6 * raw)


def arbitrate(signals: list[StrategySignal], min_conviction: float = 0.30) -> ArbiterResult:
    """Score all candidate signals and return the highest-conviction winner.

    Args:
        signals: All non-None signals from each strategy evaluator.
        min_conviction: Minimum |tanh score| to accept a signal. Signals
            below this are rejected as "not enough edge over friction."

    Returns:
        ArbiterResult with winner (or None) and full audit log.
    """
    if not signals:
        return ArbiterResult(
            winner=None,
            conviction=0.0,
            rejection_reason="no signals from any strategy",
        )

    scored: list[tuple[float, StrategySignal]] = []
    audit: list[dict] = []

    for sig in signals:
        raw_score = _score(sig, signals)
        abs_score = abs(raw_score)
        audit.append({
            "strategy": sig.strategy,
            "direction": sig.direction,
            "market_bias": "BULL" if _market_bias(sig) > 0 else ("BEAR" if _market_bias(sig) < 0 else "NEUTRAL"),
            "symbol": sig.symbol,
            "mech_confidence": sig.confidence,
            "arbiter_score": round(raw_score, 4),
            "conviction_pct": round(abs_score * 100, 1),
            "accepted": abs_score >= min_conviction,
        })
        scored.append((abs_score, sig))

    scored.sort(key=lambda t: t[0], reverse=True)

    logger.info("Arbiter scores:")
    for entry in sorted(audit, key=lambda e: e["conviction_pct"], reverse=True):
        marker = "✓" if entry["accepted"] else "✗"
        logger.info(
            "  {} {} {} | score={} conviction={}%",
            marker, entry["strategy"], entry["direction"],
            entry["arbiter_score"], entry["conviction_pct"],
        )

    best_score, best_signal = scored[0]

    if best_score < min_conviction:
        return ArbiterResult(
            winner=None,
            conviction=best_score,
            all_scores=audit,
            rejection_reason=(
                f"highest conviction {best_score:.2f} < threshold {min_conviction} "
                f"({best_signal.strategy})"
            ),
        )

    # Augment the winner's confidence with the arbiter's conviction
    best_signal.confidence = round(min(0.99, best_signal.confidence * (1 + best_score)), 3)

    return ArbiterResult(
        winner=best_signal,
        conviction=best_score,
        all_scores=audit,
    )
