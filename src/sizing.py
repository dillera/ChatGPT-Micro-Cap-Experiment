"""Confidence-tiered position sizing.

Computes whole share count from buying power, price, and consensus confidence.
No trade below $50 notional (SIZE-04). High conviction (>= 0.75) caps at 40%
of buying power (SIZE-02). Normal conviction (>= 0.60) caps at 20% (SIZE-03).
"""
from __future__ import annotations

from decimal import Decimal

from loguru import logger

MIN_TRADE_VALUE = Decimal("50.00")              # SIZE-04
HIGH_CONVICTION_THRESHOLD = 0.75                 # SIZE-02
NORMAL_CONVICTION_THRESHOLD = 0.60               # SIZE-03
HIGH_CONVICTION_MAX_PCT = Decimal("0.40")        # SIZE-02: up to 40%
NORMAL_CONVICTION_MAX_PCT = Decimal("0.20")      # SIZE-03: up to 20%


def compute_shares(
    buying_power: Decimal,
    price: Decimal,
    confidence: float,
) -> int:
    """Compute number of whole shares to buy based on confidence tier.

    Args:
        buying_power: Available buying power from account.
        price: Current share price (mid-price from live quote).
        confidence: Minimum confidence from bull/bear consensus (0.0-1.0).

    Returns:
        Number of whole shares to buy. 0 means reject trade.
    """
    if price <= 0:
        logger.warning("Price <= 0, cannot size position")
        return 0

    if confidence >= HIGH_CONVICTION_THRESHOLD:
        max_notional = buying_power * HIGH_CONVICTION_MAX_PCT
    elif confidence >= NORMAL_CONVICTION_THRESHOLD:
        max_notional = buying_power * NORMAL_CONVICTION_MAX_PCT
    else:
        logger.info(
            "Confidence {:.2f} below threshold {}",
            confidence, NORMAL_CONVICTION_THRESHOLD,
        )
        return 0

    if max_notional < MIN_TRADE_VALUE:
        logger.info(
            "Max notional ${:.2f} below ${} minimum",
            max_notional, MIN_TRADE_VALUE,
        )
        return 0

    shares = int(max_notional / price)  # Round down to whole shares
    notional = Decimal(shares) * price

    if notional < MIN_TRADE_VALUE:
        logger.info(
            "Even {} share(s) = ${:.2f} below ${} minimum",
            shares, notional, MIN_TRADE_VALUE,
        )
        return 0

    return shares
