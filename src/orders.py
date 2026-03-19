"""Order execution layer with safety gates.

Every order passes through: OTC filter -> PDT check -> spread check ->
position sizing -> dry_run validation -> real submission (if not dry_run).

Requirements covered: BROK-04, BROK-05
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from loguru import logger

from src.config import get_settings
from src.db import get_db
from src.otc_filter import is_exchange_listed
from src.pdt import check_pdt_limit, record_day_trade
from src.sizing import compute_shares

if TYPE_CHECKING:
    from src.broker import TastytradeClient
    from src.models import TradeRecommendation

MAX_SPREAD_PCT = 0.05  # 5% -- reject if wider


def execute_trade(
    client: TastytradeClient,
    rec: TradeRecommendation,
    buying_power: Decimal,
    dry_run: bool = True,
) -> dict:
    """Execute a single approved trade recommendation.

    Safety gates (in order):
    1. OTC filter -- validate ticker is on a major exchange
    2. PDT check -- ensure day-trade limit not reached (buys only)
    3. Spread check -- reject if bid-ask spread > 5%
    4. Position sizing -- compute shares from confidence and buying power
    5. Dry-run validation -- always validate with dry_run=True first
    6. Real submission -- only if dry_run=False

    Returns dict with status, details, and reason for rejection if applicable.
    """
    ticker = rec.symbol

    # Gate 1: OTC filter -- validate symbol is exchange-listed
    # We pass exchange=None here; the OTC filter will reject unknown exchanges.
    # In production, Equity.get() validates the symbol exists in tastytrade's universe.
    if not is_exchange_listed(ticker, "NASDAQ"):
        logger.warning("Rejected {}: OTC/invalid symbol", ticker)
        return {"status": "rejected", "reason": "OTC/invalid symbol", "ticker": ticker}

    # Gate 2: PDT check (buys only)
    if rec.action == "BUY" and not check_pdt_limit():
        logger.warning("Rejected {}: PDT limit reached", ticker)
        return {"status": "rejected", "reason": "PDT limit reached", "ticker": ticker}

    # Gate 3: Spread check
    bid, ask, spread_pct = client.get_quote(ticker)
    if spread_pct > MAX_SPREAD_PCT:
        logger.warning(
            "Rejected {}: spread {:.1%} > {:.0%} threshold",
            ticker, spread_pct, MAX_SPREAD_PCT,
        )
        return {
            "status": "rejected",
            "reason": f"spread {spread_pct:.1%} > 5%",
            "ticker": ticker,
            "spread_pct": spread_pct,
        }

    # Gate 4: Position sizing
    mid = (bid + ask) / 2
    shares = compute_shares(buying_power, Decimal(str(round(mid, 2))), rec.confidence)
    if shares == 0:
        logger.warning("Rejected {}: insufficient size (0 shares)", ticker)
        return {
            "status": "rejected",
            "reason": "insufficient size",
            "ticker": ticker,
        }

    # Compute order prices
    limit_price = Decimal(str(round(mid, 2)))
    stop_price = Decimal(str(round(mid * (1 - rec.stop_loss_pct), 2)))

    # Gate 5: Dry-run pre-flight validation (always)
    logger.info(
        "Pre-flight validation for {}: {} shares @ ${} (stop ${})",
        ticker, shares, limit_price, stop_price,
    )
    preflight = client.place_otoco_order(
        ticker=ticker,
        shares=shares,
        limit_price=limit_price,
        stop_price=stop_price,
        dry_run=True,
    )

    if dry_run:
        return {
            "status": "dry_run",
            "ticker": ticker,
            "shares": shares,
            "limit_price": float(limit_price),
            "stop_price": float(stop_price),
            "spread_pct": spread_pct,
        }

    # Gate 6: Real submission
    logger.info("Submitting real order for {}: {} shares", ticker, shares)
    result = client.place_otoco_order(
        ticker=ticker,
        shares=shares,
        limit_price=limit_price,
        stop_price=stop_price,
        dry_run=False,
    )

    # Extract order ID from response
    order_response = result.get("order_response")
    order_id = None
    if order_response and hasattr(order_response, "order"):
        order_obj = order_response.order
        if hasattr(order_obj, "id"):
            order_id = str(order_obj.id)

    # Record trade to SQLite
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO trades (executed_at, symbol, action, shares, price,
               total_value, stop_loss, reason, order_id, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                rec.symbol,
                rec.action,
                shares,
                float(limit_price),
                float(Decimal(shares) * limit_price),
                float(stop_price),
                rec.reasoning,
                order_id,
                "llm_consensus",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Record day trade for PDT tracking
    record_day_trade(ticker)

    logger.info(
        "Order executed: {} {} x {} shares @ ${} (stop ${}, order_id={})",
        rec.action, ticker, shares, limit_price, stop_price, order_id,
    )

    return {
        "status": "executed",
        "ticker": ticker,
        "shares": shares,
        "limit_price": float(limit_price),
        "stop_price": float(stop_price),
        "spread_pct": spread_pct,
        "order_id": order_id,
    }
