"""0DTE options trading cycle orchestrator.

Time-windowed replacement for the equity run_cycle(). The old cycle.py
is preserved on disk but this module is called for options trading.

Windows:
  morning  — ORB entry (9:45-10:15 ET)
  midday   — mean reversion (11:00-14:00 ET)
  close    — pre-close positioning (15:00-15:45 ET)
  monitor  — position management only, no new trades

Usage:
  python -m src options --window morning [--dry-run]
"""
from __future__ import annotations

import fcntl
import os
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from src.config import get_settings, PROJECT_ROOT
from src.db import get_db, init_db
from src.run_logger import write_run_log

ET = ZoneInfo("America/New_York")
LOCK_PATH = PROJECT_ROOT / "data" / "options_cycle.lock"


def _acquire_lock() -> int | None:
    """Acquire an exclusive file lock. Returns file descriptor or None if already locked."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        return None


def _release_lock(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def _is_market_day() -> bool:
    """Return False on weekends."""
    return datetime.now(ET).weekday() < 5


def run_options_cycle(window: str, dry_run: bool = True) -> dict:
    """Run one time-windowed options trading cycle.

    Stages:
     0. Lockfile acquisition
     1. DB init + TastyTrade auth
     2. Market day check
     3. VIX extreme check (skip if EXTREME)
     4. Account sync
     5. Circuit breaker check
     6. Manage open spreads (check_and_close_open_spreads)
     7. Daily target gate — skip 8-11 if gated (or window==monitor)
     8. Select underlying symbol for today
     9. Fetch option chain
    10. Fetch market context
    11. Evaluate strategy signal for this window
    12. If signal: run options consensus
    13. If approved: execute spread trade
    14. Record daily snapshot
    15. Write run log

    Args:
        window: "morning", "midday", "close", or "monitor".
        dry_run: If True, no real orders are submitted.

    Returns:
        Dict with cycle results suitable for write_run_log.
    """
    settings = get_settings()
    started_at = datetime.now(ET).isoformat()
    result: dict = {
        "window": window,
        "dry_run": dry_run,
        "started_at": started_at,
        "status": "error",
        "spreads_opened": [],
        "spreads_closed": [],
        "daily_pnl": 0.0,
        "signal": None,
        "consensus": None,
        "skip_reason": None,
    }

    # Stage 0: lockfile
    fd = _acquire_lock()
    if fd is None:
        result["status"] = "skipped"
        result["skip_reason"] = "another cycle already running"
        logger.info("Options cycle: lockfile busy, skipping")
        write_run_log(result)
        return result

    try:
        # Stage 1: DB + auth
        init_db()
        from src.broker import TastytradeClient
        client = TastytradeClient()
        client.authenticate()

        # Stage 2: market day check
        if not _is_market_day():
            result["status"] = "skipped"
            result["skip_reason"] = "weekend"
            write_run_log(result)
            return result

        # Stage 3: fetch VIX early for extreme check
        from src.market_context import MarketContext, fetch_market_context, select_symbol_for_today, classify_market_regime
        from src.market_context import _fetch_vix
        vix = _fetch_vix()
        regime = classify_market_regime(vix)
        if regime == "EXTREME":
            result["status"] = "skipped"
            result["skip_reason"] = f"VIX={vix:.1f} EXTREME — no trades today"
            logger.warning("VIX EXTREME ({:.1f}) — halting options trading", vix)
            write_run_log(result)
            return result

        # Stage 4: account sync
        snapshot = client.get_account_snapshot()
        buying_power = snapshot.buying_power
        logger.info("Account: BP=${:.2f}", buying_power)

        # Stage 5: circuit breaker
        from src.circuit_breaker import get_cb_status
        cb = get_cb_status()
        if cb.status != "ACTIVE":
            result["status"] = "halted"
            result["skip_reason"] = f"circuit breaker: {cb.status} — {cb.reason}"
            logger.warning("Circuit breaker active: {}", cb.reason)
            write_run_log(result)
            return result

        # Stage 6: manage open spreads (runs on EVERY window, including monitor)
        from src.orders import check_and_close_open_spreads
        closed = check_and_close_open_spreads(client, dry_run=dry_run)
        result["spreads_closed"] = closed
        if closed:
            logger.info("Managed {} spread(s)", len(closed))

        # Stage 7: daily target gate
        from src.daily_target import get_today_target, should_take_trade
        daily_state = get_today_target()
        result["daily_pnl"] = daily_state.realized_pnl

        if window == "monitor":
            result["status"] = "complete"
            result["skip_reason"] = "monitor window — position management only"
            write_run_log(result)
            return result

        allowed, gate_reason = should_take_trade(daily_state, window)
        if not allowed:
            result["status"] = "skipped"
            result["skip_reason"] = gate_reason
            logger.info("Daily gate blocked: {}", gate_reason)
            write_run_log(result)
            return result

        # Stage 8: select symbol
        symbol = select_symbol_for_today(settings.options_universe)
        logger.info("Selected underlying: {}", symbol)

        # Stage 9: fetch option chain
        chain = client.get_option_chain(symbol, dte_target=0)
        if chain is None:
            result["status"] = "skipped"
            result["skip_reason"] = f"no options chain for {symbol}"
            write_run_log(result)
            return result

        # Stage 10: fetch market context
        ctx = fetch_market_context(symbol)

        # Stage 11: evaluate debit signal; fall back to credit spread if none
        from src.options_strategy import evaluate_signal_for_window, evaluate_credit_spread_signal
        signal = evaluate_signal_for_window(
            ctx, window,
            trades_today=daily_state.trades_today,
            max_trades=daily_state.max_trades,
            config=settings,
        )
        if signal is None:
            logger.info("No debit signal for {} window — trying credit spread fallback", window)
            signal = evaluate_credit_spread_signal(ctx, settings)

        result["signal"] = signal.__dict__ if signal else None

        if signal is None:
            result["status"] = "skipped"
            result["skip_reason"] = f"no signal for {window} window"
            write_run_log(result)
            return result

        # Stage 12: LLM consensus
        from src.consensus import run_options_consensus_cycle
        chain_summary = {
            "symbol": symbol,
            "expiry": chain["expiry"],
            "dte": chain["dte"],
            "calls": chain["calls"][:10],
            "puts": chain["puts"][:10],
        }
        consensus = run_options_consensus_cycle(
            ctx=ctx,
            signal=signal,
            chain_summary=chain_summary,
            daily_realized_pnl=daily_state.realized_pnl,
            trades_today=daily_state.trades_today,
            max_trades=daily_state.max_trades,
            buying_power=buying_power,
        )
        result["consensus"] = {
            "approved": len(consensus.approved_trades),
            "disagreed": consensus.disagreed,
        }

        if not consensus.approved_trades:
            result["status"] = "skipped"
            result["skip_reason"] = "consensus: no approved trades"
            write_run_log(result)
            return result

        # Stage 13: execute spread — route to credit or debit executor by strategy type
        rec = consensus.approved_trades[0]
        is_credit = signal.strategy.startswith("CREDIT_")
        if is_credit:
            from src.orders import execute_credit_spread_trade
            trade_result = execute_credit_spread_trade(
                client=client,
                rec=rec,
                signal=signal,
                chain=chain,
                buying_power=buying_power,
                dry_run=dry_run,
            )
        else:
            from src.orders import execute_spread_trade
            trade_result = execute_spread_trade(
                client=client,
                rec=rec,
                signal=signal,
                chain=chain,
                buying_power=buying_power,
                dry_run=dry_run,
            )
        result["spreads_opened"] = [trade_result]
        logger.info("Spread trade result: {}", trade_result.get("status"))

        # Stage 14: record daily snapshot
        from src.circuit_breaker import record_daily_snapshot, evaluate_circuit_breaker
        snapshot2 = client.get_account_snapshot()
        record_daily_snapshot(snapshot2)
        evaluate_circuit_breaker(snapshot2)

        # Stage 15: update daily pnl in result
        daily_state_updated = get_today_target()
        result["daily_pnl"] = daily_state_updated.realized_pnl
        result["status"] = "complete"

    except Exception as e:
        logger.exception("Options cycle error: {}", e)
        result["status"] = "error"
        result["skip_reason"] = str(e)

    finally:
        _release_lock(fd)
        result["finished_at"] = datetime.now(ET).isoformat()
        write_run_log(result)

    return result
