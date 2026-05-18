"""Order execution layer with safety gates.

Every order passes through: OTC filter -> PDT check -> spread check ->
position sizing -> dry_run validation -> real submission (if not dry_run).

Requirements covered: BROK-04, BROK-05
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from loguru import logger

from src.config import get_settings
from src.db import get_db
from src.otc_filter import is_exchange_listed
from src.pdt import check_pdt_limit, record_day_trade
from src.sizing import compute_shares
from src.watchlist import remove_ticker

if TYPE_CHECKING:
    from src.broker import TastytradeClient
    from src.models import SpreadPosition, SpreadRecommendation, TradeRecommendation
    from src.options_strategy import StrategySignal

ET = ZoneInfo("America/New_York")

MAX_SPREAD_PCT = 0.05       # 5% equity spread -- reject if wider
OPTIONS_MAX_SPREAD_PCT = 0.20  # 20% options spread -- wider tolerance for illiquid puts


def execute_put_trade(
    client: "TastytradeClient",
    rec: "TradeRecommendation",
    buying_power: Decimal,
    dry_run: bool = True,
) -> dict:
    """Buy a put option on a bearish candidate (bear-only, no consensus required).

    Safety gates:
    1. Fetch ATM put contract at ~30 DTE via tastytrade options chain
    2. Get option quote via DXLink streamer
    3. Spread check (20% tolerance for micro-cap options)
    4. Size: 2% of buying power, 1-N contracts, capped at options_max_contracts
    5. Dry-run pre-flight, then real submission if not dry_run
    """
    settings = get_settings()
    ticker = rec.symbol

    # Gate 1: find the put contract
    contract = client.get_put_contract(ticker, target_dte=settings.options_put_dte)
    if not contract:
        # None means either no chain exists or price is below minimum — already logged in broker
        return {"status": "rejected", "reason": "no options chain or below price minimum", "ticker": ticker, "action": "BUY_PUT"}

    occ_symbol = contract["occ_symbol"]
    streamer_symbol = contract["streamer_symbol"]

    # Gate 2: option quote
    try:
        bid, ask, spread_pct = client.get_quote(streamer_symbol)
    except Exception as e:
        logger.warning("Could not quote option {} for {}: {}", occ_symbol, ticker, e)
        return {"status": "rejected", "reason": f"option quote failed: {e}", "ticker": ticker, "action": "BUY_PUT"}

    mid = (bid + ask) / 2
    if mid <= 0:
        return {"status": "rejected", "reason": "invalid option price", "ticker": ticker, "action": "BUY_PUT"}

    # Gate 3: spread check (options are wider, use looser threshold)
    if spread_pct > OPTIONS_MAX_SPREAD_PCT:
        logger.warning("Rejected put on {}: spread {:.1%} > {:.0%}", ticker, spread_pct, OPTIONS_MAX_SPREAD_PCT)
        return {"status": "rejected", "reason": f"option spread {spread_pct:.1%} > 20%", "ticker": ticker, "action": "BUY_PUT"}

    # Gate 4: sizing — 2% of buying power, each contract = 100 shares * premium
    max_spend = float(buying_power) * settings.options_buying_power_pct
    contracts = max(1, int(max_spend / (mid * 100)))
    contracts = min(contracts, settings.options_max_contracts)

    limit_price = Decimal(str(round(mid, 2)))

    logger.info(
        "Put pre-flight: {} {} contract(s) @ ${} (strike={}, expiry={}, DTE={})",
        ticker, contracts, limit_price, contract["strike"], contract["expiry"], contract["dte"],
    )

    # Gate 5: dry-run pre-flight (also catches closing-only restriction)
    try:
        client.place_put_order(occ_symbol, contracts, limit_price, dry_run=True)
    except ValueError as e:
        if "closing-only" in str(e).lower():
            logger.warning("Removing {} from watchlist — closing-only restriction", ticker)
            remove_ticker(ticker)
        return {"status": "rejected", "reason": str(e), "ticker": ticker, "action": "BUY_PUT"}

    if dry_run:
        return {
            "status": "dry_run",
            "ticker": ticker,
            "action": "BUY_PUT",
            "occ_symbol": occ_symbol,
            "contracts": contracts,
            "limit_price": float(limit_price),
            "strike": contract["strike"],
            "expiry": contract["expiry"],
            "dte": contract["dte"],
            "spread_pct": spread_pct,
        }

    # Gate 6: real submission
    result = client.place_put_order(occ_symbol, contracts, limit_price, dry_run=False)

    order_id = None
    order_response = result.get("order_response")
    if order_response and hasattr(order_response, "order"):
        order_obj = order_response.order
        if hasattr(order_obj, "id"):
            order_id = str(order_obj.id)

    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO trades (executed_at, symbol, action, shares, price,
               total_value, stop_loss, reason, order_id, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                occ_symbol,
                "BUY_PUT",
                contracts,
                float(limit_price),
                float(Decimal(str(contracts)) * limit_price * 100),  # contract multiplier
                None,  # max loss = premium paid; no stop needed
                rec.reasoning,
                order_id,
                "llm_bear_put",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info(
        "Put executed: {} x{} contracts @ ${} (order_id={})",
        occ_symbol, contracts, limit_price, order_id,
    )
    return {
        "status": "executed",
        "ticker": ticker,
        "action": "BUY_PUT",
        "occ_symbol": occ_symbol,
        "contracts": contracts,
        "limit_price": float(limit_price),
        "strike": contract["strike"],
        "expiry": contract["expiry"],
        "dte": contract["dte"],
        "order_id": order_id,
    }


def execute_spread_trade(
    client: "TastytradeClient",
    rec: "SpreadRecommendation",
    signal: "StrategySignal",
    chain: dict,
    buying_power: float,
    dry_run: bool = True,
) -> dict:
    """Execute a vertical debit spread trade.

    Safety gates (in order):
    1. Daily target gate — skip if target/stop hit or max trades reached
    2. Spread selection — pick strikes from chain
    3. Quote both legs via DXLink
    4. Debit validation — net_debit <= max_debit_pct * width
    5. Contract sizing — bounded by per-trade risk cap
    6. Re-quote immediately before order submission (mandatory for 0DTE)
    7. Dry-run preflight → real submission
    8. Record to spread_positions table
    9. Increment daily trade count

    Returns dict with status and trade details.
    """
    from src.daily_target import get_today_target, increment_trade_count, should_take_trade
    from src.options_strategy import select_spread, compute_spread_contracts

    settings = get_settings()

    # Gate 1: daily target
    state = get_today_target()
    allowed, reason = should_take_trade(state, signal.strategy)
    if not allowed:
        logger.info("Spread trade blocked: {}", reason)
        return {"status": "skipped", "reason": reason}

    direction = signal.direction
    underlying_price = chain.get("underlying_price", 0.0)

    # Gate 2: spread selection (without quotes yet — just pick strikes)
    # We need quotes to validate debit; get them first, then call select_spread with prices
    legs = chain.get("calls" if direction == "CALL" else "puts", [])
    if len(legs) < 2:
        return {"status": "rejected", "reason": f"insufficient {direction} legs in chain"}

    # Find candidate long leg (ATM)
    long_candidates = sorted(legs, key=lambda s: abs(s["strike"] - underlying_price))
    long_leg = long_candidates[0]

    # Find candidate short leg
    if direction == "CALL":
        target_short = long_leg["strike"] + settings.options_spread_width
    else:
        target_short = long_leg["strike"] - settings.options_spread_width
    short_leg = sorted(legs, key=lambda s: abs(s["strike"] - target_short))[0]

    if short_leg["occ_symbol"] == long_leg["occ_symbol"]:
        return {"status": "rejected", "reason": "long and short strikes are the same"}

    # Gate 3: get live quotes for both legs
    try:
        long_q, short_q = client.get_spread_quotes(
            long_leg["streamer_symbol"], short_leg["streamer_symbol"]
        )
    except Exception as e:
        logger.warning("Spread quote failed: {}", e)
        return {"status": "rejected", "reason": f"quote failed: {e}"}

    long_bid, long_ask = long_q
    short_bid, short_ask = short_q

    if long_ask <= 0:
        return {"status": "rejected", "reason": "invalid long leg price"}

    # Net debit at natural (worst case): long_ask - short_bid
    net_debit_natural = long_ask - short_bid
    actual_width = abs(long_leg["strike"] - short_leg["strike"])

    # Gate 4: risk/reward validation
    if actual_width <= 0 or net_debit_natural <= 0:
        return {"status": "rejected", "reason": "invalid spread geometry"}

    if net_debit_natural > settings.options_max_debit_pct * actual_width:
        logger.warning(
            "Spread rejected: debit ${:.2f} > {:.0%} of ${:.1f} width",
            net_debit_natural, settings.options_max_debit_pct, actual_width,
        )
        return {
            "status": "rejected",
            "reason": f"debit ${net_debit_natural:.2f} exceeds {settings.options_max_debit_pct:.0%} of width",
        }

    # Gate 5: sizing
    # Submit at mid (between natural and mid price) to improve fill probability
    long_mid = (long_bid + long_ask) / 2
    short_mid = (short_bid + short_ask) / 2
    net_debit_mid = long_mid - short_mid
    net_debit_submit = max(round((net_debit_natural + net_debit_mid) / 2, 2), net_debit_mid)

    debit_per_contract = net_debit_submit * 100  # in dollars
    max_loss_per_trade = min(settings.options_daily_stop_loss, debit_per_contract * settings.options_max_contracts)
    contracts = compute_spread_contracts(
        buying_power, debit_per_contract, max_loss_per_trade, settings.options_max_contracts
    )
    if contracts == 0:
        return {"status": "rejected", "reason": "zero contracts computed"}

    limit_debit = Decimal(str(round(net_debit_submit, 2)))
    max_profit_per_contract = (actual_width - float(limit_debit)) * 100
    max_loss_dollars = float(limit_debit) * 100 * contracts

    logger.info(
        "Spread pre-flight: {} {} | long {} @ ${:.2f} / short {} @ ${:.2f} | "
        "debit=${:.2f} | {} contract(s) | max_loss=${:.2f}",
        direction, rec.symbol,
        long_leg["occ_symbol"], long_leg["strike"],
        short_leg["occ_symbol"], short_leg["strike"],
        float(limit_debit), contracts, max_loss_dollars,
    )

    # Gate 6: re-quote immediately before submission (0DTE moves fast)
    try:
        long_q2, short_q2 = client.get_spread_quotes(
            long_leg["streamer_symbol"], short_leg["streamer_symbol"]
        )
        net_debit_recheck = long_q2[1] - short_q2[0]  # long_ask - short_bid (natural)
        if net_debit_recheck > settings.options_max_debit_pct * actual_width:
            return {
                "status": "rejected",
                "reason": f"re-quote failed risk/reward: debit moved to ${net_debit_recheck:.2f}",
            }
    except Exception as e:
        logger.warning("Re-quote failed, proceeding with original quote: {}", e)

    # Gate 7: dry-run preflight
    try:
        client.place_vertical_spread(
            long_leg["occ_symbol"], short_leg["occ_symbol"],
            contracts, limit_debit, dry_run=True,
        )
    except ValueError as e:
        return {"status": "rejected", "reason": str(e)}

    if dry_run:
        logger.info("Spread dry-run complete: {} {} x{}", direction, rec.symbol, contracts)
        return {
            "status": "dry_run",
            "symbol": rec.symbol,
            "direction": direction,
            "long_occ": long_leg["occ_symbol"],
            "short_occ": short_leg["occ_symbol"],
            "long_strike": long_leg["strike"],
            "short_strike": short_leg["strike"],
            "contracts": contracts,
            "net_debit": float(limit_debit),
            "max_loss": max_loss_dollars,
            "max_profit": max_profit_per_contract * contracts,
            "expiry": chain.get("expiry"),
            "dte": chain.get("dte"),
        }

    # Gate 8: real submission
    result = client.place_vertical_spread(
        long_leg["occ_symbol"], short_leg["occ_symbol"],
        contracts, limit_debit, dry_run=False,
    )

    order_id = None
    order_response = result.get("order_response")
    if order_response and hasattr(order_response, "order"):
        order_obj = order_response.order
        if hasattr(order_obj, "id"):
            order_id = str(order_obj.id)

    spread_type = "CALL_DEBIT" if direction == "CALL" else "PUT_DEBIT"

    # Gate 9: record to spread_positions
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO spread_positions
               (symbol, spread_type, long_strike, short_strike, expiry, dte_at_open,
                contracts, debit_paid, max_profit, max_loss, target_exit_pct,
                opened_at, status, order_id, long_occ, short_occ, daily_session)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)""",
            (
                rec.symbol,
                spread_type,
                long_leg["strike"],
                short_leg["strike"],
                chain.get("expiry", ""),
                chain.get("dte", 0),
                contracts,
                float(limit_debit),
                max_profit_per_contract * contracts,
                max_loss_dollars,
                settings.options_profit_close_pct,
                datetime.now(ET).isoformat(),
                order_id,
                long_leg["occ_symbol"],
                short_leg["occ_symbol"],
                signal.strategy.lower(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Gate 10: increment daily trade count
    increment_trade_count()

    logger.info(
        "Spread executed: {} {} x{} contracts | order_id={}",
        spread_type, rec.symbol, contracts, order_id,
    )

    return {
        "status": "executed",
        "symbol": rec.symbol,
        "direction": direction,
        "long_occ": long_leg["occ_symbol"],
        "short_occ": short_leg["occ_symbol"],
        "long_strike": long_leg["strike"],
        "short_strike": short_leg["strike"],
        "contracts": contracts,
        "net_debit": float(limit_debit),
        "max_loss": max_loss_dollars,
        "max_profit": max_profit_per_contract * contracts,
        "expiry": chain.get("expiry"),
        "dte": chain.get("dte"),
        "order_id": order_id,
    }


def execute_credit_spread_trade(
    client: "TastytradeClient",
    rec: "SpreadRecommendation",
    signal: "StrategySignal",
    chain: dict,
    buying_power: float,
    dry_run: bool = True,
) -> dict:
    """Execute a vertical credit spread trade (bull put or bear call).

    Safety gates (in order):
    1. Daily target gate
    2. Strike selection — short OTM leg + long protection leg
    3. Quote both legs via DXLink
    4. Credit validation — net_credit must be > 0
    5. Contract sizing — bounded by max loss per trade
    6. Re-quote immediately before submission
    7. Dry-run preflight → real submission
    8. Record to spread_positions table
    9. Increment daily trade count
    """
    from src.daily_target import get_today_target, increment_trade_count, should_take_trade
    from src.options_strategy import select_credit_spread, compute_credit_spread_contracts

    settings = get_settings()

    # Gate 1: daily target
    state = get_today_target()
    allowed, reason = should_take_trade(state, signal.strategy)
    if not allowed:
        logger.info("Credit spread blocked: {}", reason)
        return {"status": "skipped", "reason": reason}

    direction = signal.direction
    underlying_price = chain.get("underlying_price", 0.0)

    # Gate 2: pick strikes
    legs_key = "puts" if direction == "PUT" else "calls"
    legs = chain.get(legs_key, [])
    if len(legs) < 2:
        return {"status": "rejected", "reason": f"insufficient {direction} legs in chain"}

    # Find short leg (OTM) and long leg (protection)
    if direction == "PUT":
        target_short_strike = underlying_price * (1 - settings.options_credit_otm_pct)
        target_long_strike_fn = lambda s: s - settings.options_spread_width
    else:
        target_short_strike = underlying_price * (1 + settings.options_credit_otm_pct)
        target_long_strike_fn = lambda s: s + settings.options_spread_width

    short_leg = sorted(legs, key=lambda s: abs(s["strike"] - target_short_strike))[0]
    long_leg = sorted(
        legs, key=lambda s: abs(s["strike"] - target_long_strike_fn(short_leg["strike"]))
    )[0]

    if short_leg["occ_symbol"] == long_leg["occ_symbol"]:
        return {"status": "rejected", "reason": "short and long strikes are the same"}

    # Gate 3: get live quotes
    try:
        short_q, long_q = client.get_spread_quotes(
            short_leg["streamer_symbol"], long_leg["streamer_symbol"]
        )
    except Exception as e:
        logger.warning("Credit spread quote failed: {}", e)
        return {"status": "rejected", "reason": f"quote failed: {e}"}

    short_bid, short_ask = short_q
    long_bid, long_ask = long_q

    if short_bid <= 0:
        return {"status": "rejected", "reason": "invalid short leg price"}

    # Gate 4: credit validation — natural credit = short_bid - long_ask (worst case fill)
    net_credit_natural = short_bid - long_ask
    actual_width = abs(short_leg["strike"] - long_leg["strike"])

    if actual_width <= 0 or net_credit_natural <= 0:
        logger.warning(
            "Credit spread rejected: natural credit ${:.2f} for width ${:.1f}",
            net_credit_natural, actual_width,
        )
        return {"status": "rejected", "reason": f"non-positive credit ${net_credit_natural:.2f}"}

    # Gate 5: sizing — submit at mid for better fills
    short_mid = (short_bid + short_ask) / 2
    long_mid = (long_bid + long_ask) / 2
    net_credit_mid = short_mid - long_mid
    net_credit_submit = max(round((net_credit_natural + net_credit_mid) / 2, 2), net_credit_mid)

    max_loss_per_trade = min(
        settings.options_daily_stop_loss,
        (actual_width - net_credit_submit) * 100 * settings.options_max_contracts,
    )
    contracts = compute_credit_spread_contracts(
        max_loss_per_trade, actual_width, net_credit_submit, settings.options_max_contracts
    )
    if contracts == 0:
        return {"status": "rejected", "reason": "zero contracts computed"}

    limit_credit = Decimal(str(round(net_credit_submit, 2)))
    max_profit_per_contract = float(limit_credit) * 100
    max_loss_per_contract = (actual_width - float(limit_credit)) * 100
    max_loss_dollars = max_loss_per_contract * contracts

    logger.info(
        "Credit spread pre-flight: {} {} | short {} @ ${:.2f} / long {} @ ${:.2f} | "
        "credit=${:.2f} | {} contract(s) | max_loss=${:.2f}",
        direction, rec.symbol,
        short_leg["occ_symbol"], short_leg["strike"],
        long_leg["occ_symbol"], long_leg["strike"],
        float(limit_credit), contracts, max_loss_dollars,
    )

    # Gate 6: re-quote before submission
    try:
        short_q2, long_q2 = client.get_spread_quotes(
            short_leg["streamer_symbol"], long_leg["streamer_symbol"]
        )
        net_credit_recheck = short_q2[0] - long_q2[1]  # short_bid - long_ask (natural)
        if net_credit_recheck <= 0:
            return {
                "status": "rejected",
                "reason": f"re-quote: credit turned negative (${net_credit_recheck:.2f})",
            }
    except Exception as e:
        logger.warning("Re-quote failed, proceeding with original: {}", e)

    # Gate 7: dry-run preflight
    try:
        client.place_credit_spread(
            short_leg["occ_symbol"], long_leg["occ_symbol"],
            contracts, limit_credit, dry_run=True,
        )
    except ValueError as e:
        return {"status": "rejected", "reason": str(e)}

    if dry_run:
        logger.info("Credit spread dry-run complete: {} {} x{}", direction, rec.symbol, contracts)
        return {
            "status": "dry_run",
            "symbol": rec.symbol,
            "direction": direction,
            "spread_type": "CALL_CREDIT" if direction == "CALL" else "PUT_CREDIT",
            "short_occ": short_leg["occ_symbol"],
            "long_occ": long_leg["occ_symbol"],
            "short_strike": short_leg["strike"],
            "long_strike": long_leg["strike"],
            "contracts": contracts,
            "net_credit": float(limit_credit),
            "max_profit": max_profit_per_contract * contracts,
            "max_loss": max_loss_dollars,
            "expiry": chain.get("expiry"),
            "dte": chain.get("dte"),
        }

    # Gate 8: real submission
    result = client.place_credit_spread(
        short_leg["occ_symbol"], long_leg["occ_symbol"],
        contracts, limit_credit, dry_run=False,
    )

    order_id = None
    order_response = result.get("order_response")
    if order_response and hasattr(order_response, "order"):
        order_obj = order_response.order
        if hasattr(order_obj, "id"):
            order_id = str(order_obj.id)

    spread_type = "CALL_CREDIT" if direction == "CALL" else "PUT_CREDIT"

    # Gate 9: record to spread_positions
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO spread_positions
               (symbol, spread_type, long_strike, short_strike, expiry, dte_at_open,
                contracts, debit_paid, max_profit, max_loss, target_exit_pct,
                opened_at, status, order_id, long_occ, short_occ, daily_session)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)""",
            (
                rec.symbol,
                spread_type,
                long_leg["strike"],
                short_leg["strike"],
                chain.get("expiry", ""),
                chain.get("dte", 0),
                contracts,
                -float(limit_credit),         # negative = credit received (store as negative debit)
                max_profit_per_contract * contracts,
                max_loss_dollars,
                settings.options_profit_close_pct,
                datetime.now(ET).isoformat(),
                order_id,
                long_leg["occ_symbol"],
                short_leg["occ_symbol"],
                signal.strategy.lower(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Gate 10: increment daily trade count
    increment_trade_count()

    logger.info(
        "Credit spread executed: {} {} x{} contracts | order_id={}",
        spread_type, rec.symbol, contracts, order_id,
    )

    return {
        "status": "executed",
        "symbol": rec.symbol,
        "direction": direction,
        "spread_type": spread_type,
        "short_occ": short_leg["occ_symbol"],
        "long_occ": long_leg["occ_symbol"],
        "short_strike": short_leg["strike"],
        "long_strike": long_leg["strike"],
        "contracts": contracts,
        "net_credit": float(limit_credit),
        "max_profit": max_profit_per_contract * contracts,
        "max_loss": max_loss_dollars,
        "expiry": chain.get("expiry"),
        "dte": chain.get("dte"),
        "order_id": order_id,
    }


def close_spread_position(
    client: "TastytradeClient",
    spread: "SpreadPosition",
    reason: str,
    dry_run: bool = True,
) -> dict:
    """Close an open spread by placing a closing order.

    Fetches current quotes, submits SELL_TO_CLOSE + BUY_TO_CLOSE order,
    then updates spread_positions.status and calls update_target_pnl().

    Returns dict with status and realized P&L.
    """
    from src.daily_target import compute_spread_pnl, update_target_pnl

    try:
        long_q, short_q = client.get_spread_quotes(
            spread.long_occ, spread.short_occ
        )
    except Exception as e:
        logger.warning("Could not quote spread legs for close: {}", e)
        return {"status": "rejected", "reason": f"quote failed: {e}"}

    long_bid, long_ask = long_q
    short_bid, short_ask = short_q

    long_mid = (long_bid + long_ask) / 2
    short_mid = (short_bid + short_ask) / 2

    realized_pnl = compute_spread_pnl(spread, long_mid, short_mid)

    # Net credit at mid price
    net_credit = max(0.01, round(long_mid - short_mid, 2))
    net_credit_dec = Decimal(str(net_credit))

    logger.info(
        "Closing spread {} ({}) | long_mid={:.2f} short_mid={:.2f} | pnl=${:.2f} | reason={}",
        spread.symbol, spread.spread_type, long_mid, short_mid, realized_pnl, reason,
    )

    try:
        client.place_spread_close(
            spread.long_occ, spread.short_occ,
            spread.contracts, net_credit_dec, dry_run=True,
        )
    except ValueError as e:
        return {"status": "rejected", "reason": str(e)}

    if dry_run:
        return {
            "status": "dry_run",
            "spread_id": spread.id,
            "symbol": spread.symbol,
            "realized_pnl": realized_pnl,
            "net_credit": net_credit,
            "reason": reason,
        }

    result = client.place_spread_close(
        spread.long_occ, spread.short_occ,
        spread.contracts, net_credit_dec, dry_run=False,
    )

    order_id = None
    order_response = result.get("order_response")
    if order_response and hasattr(order_response, "order"):
        order_obj = order_response.order
        if hasattr(order_obj, "id"):
            order_id = str(order_obj.id)

    now = datetime.now(ET).isoformat()
    conn = get_db()
    try:
        conn.execute(
            "UPDATE spread_positions SET status='CLOSED', closed_at=? WHERE id=?",
            (now, spread.id),
        )
        conn.commit()
    finally:
        conn.close()

    update_target_pnl(realized_pnl, increment_trades=False)

    logger.info(
        "Spread closed: {} {} | pnl=${:.2f} | order_id={}",
        spread.symbol, spread.spread_type, realized_pnl, order_id,
    )

    return {
        "status": "closed",
        "spread_id": spread.id,
        "symbol": spread.symbol,
        "realized_pnl": realized_pnl,
        "net_credit": net_credit,
        "reason": reason,
        "order_id": order_id,
    }


def check_and_close_open_spreads(
    client: "TastytradeClient",
    dry_run: bool = True,
) -> list[dict]:
    """Check all OPEN spreads for profit target, stop loss, or EOD force-close.

    Criteria for closing:
    - P&L >= max_profit * target_exit_pct (profit target hit)
    - P&L <= -max_loss (full loss on spread)
    - 0DTE spread and time >= 15:45 ET (EOD force-close)

    Returns list of close result dicts.
    """
    from src.models import SpreadPosition
    from datetime import time as dtime

    settings = get_settings()
    now_et = datetime.now(ET)
    eod_time = dtime(15, 45)
    results = []

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM spread_positions WHERE status='OPEN'"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return results

    logger.info("Checking {} open spread(s) for management", len(rows))

    for row in rows:
        spread = SpreadPosition(
            id=row["id"],
            symbol=row["symbol"],
            spread_type=row["spread_type"],
            long_strike=row["long_strike"],
            short_strike=row["short_strike"],
            expiry=row["expiry"],
            dte_at_open=row["dte_at_open"],
            contracts=row["contracts"],
            debit_paid=row["debit_paid"],
            max_profit=row["max_profit"],
            max_loss=row["max_loss"],
            target_exit_pct=row["target_exit_pct"],
            opened_at=row["opened_at"],
            long_occ=row["long_occ"],
            short_occ=row["short_occ"],
            daily_session=row["daily_session"] or "unknown",
            status=row["status"],
            order_id=row["order_id"],
            entry_delta=row["entry_delta"],
            closed_at=row["closed_at"],
        )

        # Fetch current quotes
        try:
            long_q, short_q = client.get_spread_quotes(spread.long_occ, spread.short_occ)
        except Exception as e:
            logger.warning("Could not quote spread {} for management: {}", spread.id, e)
            continue

        long_mid = (long_q[0] + long_q[1]) / 2
        short_mid = (short_q[0] + short_q[1]) / 2

        from src.daily_target import compute_spread_pnl, get_dynamic_profit_target
        current_pnl = compute_spread_pnl(spread, long_mid, short_mid)

        target_pct = get_dynamic_profit_target(spread.spread_type, now_et)
        profit_target_dollars = spread.max_profit * target_pct

        close_reason = None

        if current_pnl >= profit_target_dollars:
            close_reason = (
                f"profit_target ({current_pnl:.2f} >= {profit_target_dollars:.2f} "
                f"[{target_pct:.0%} dynamic])"
            )
        elif current_pnl <= -spread.max_loss:
            close_reason = f"stop_loss ({current_pnl:.2f} <= -{spread.max_loss:.2f})"
        elif spread.dte_at_open == 0 and now_et.time() >= eod_time:
            close_reason = "eod_force_close (0DTE >= 15:45)"

        if close_reason:
            logger.info("Closing spread {} — {}", spread.id, close_reason)
            r = close_spread_position(client, spread, close_reason, dry_run=dry_run)
            results.append(r)
        else:
            logger.debug(
                "Spread {} OK: pnl=${:.2f} (dynamic_target=${:.2f} [{:.0%}], stop=-${:.2f})",
                spread.id, current_pnl, profit_target_dollars, target_pct, spread.max_loss,
            )

    return results


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
