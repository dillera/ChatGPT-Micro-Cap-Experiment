"""Trading cycle orchestrator — sequences all pipeline stages.

A single call to run_cycle() executes the complete daily trading pipeline:
auth, sync, stop-loss check, consensus, sizing, order execution.

The lockfile prevents overlapping cron runs. Market-closed days exit
gracefully. Any unhandled exception is caught, logged, and reported
in the return dict.

Requirements covered: OPER-01, OPER-02
"""
from __future__ import annotations

import fcntl
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from loguru import logger

import yfinance as yf

from src.broker import TastytradeClient
from src.circuit_breaker import evaluate_circuit_breaker, get_cb_status, record_daily_snapshot
from src.config import get_settings
from src.consensus import run_consensus_cycle, run_discovery_cycle
from src.cooldown import filter_cooled_down, mark_evaluated
from src.db import get_db, init_db
from src.orders import execute_put_trade, execute_trade
from src.otc_filter import validate_symbols
from src.pdt import check_pdt_limit, get_day_trade_count
from src.research import run_research
from src.run_logger import write_run_log
from src.screener import get_screener_candidates
from src.stoploss import check_and_enforce_stops
from src.watchlist import get_active_symbols, remove_ticker


LOCK_PATH = Path(get_settings().db_path).parent / "cycle.lock"


def _acquire_lock():
    """Acquire exclusive lockfile. Returns file handle or None."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


def _release_lock(fh) -> None:
    """Release lockfile and close handle."""
    if fh is not None:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()
        except Exception:
            pass


def gather_candidates(snapshot, dry_run: bool) -> tuple[list[str], dict]:
    """Gather buy candidates from all three sources, deduplicate against positions.

    Sources:
      1. Manual watchlist (src/watchlist.py)
      2. Sector screener (src/screener.py)
      3. LLM discovery proposals (src/consensus.py)

    Args:
        snapshot: AccountSnapshot with current positions and buying_power.
        dry_run: Passed to screener/discovery for context (informational only).

    Returns:
        Tuple of (new_candidate_symbols, counts_dict).
        new_candidate_symbols excludes tickers already held.
    """
    # Source 0: research pipeline — find new small/mid-cap candidates and add to watchlist
    held_symbols = [p["symbol"] for p in snapshot.positions]
    try:
        research_added = run_research(held_symbols=held_symbols)
        if research_added:
            logger.info("Research added {} new symbols to watchlist", research_added)
    except Exception as e:
        logger.warning("Research pipeline failed (non-fatal): {}", e)

    # Source 1: manual watchlist — validate against OTC filter and purge rejects
    try:
        raw_watchlist = get_active_symbols()
        symbols_with_exchanges: list[tuple[str, str | None]] = []
        for sym in raw_watchlist:
            try:
                exchange = yf.Ticker(sym).fast_info.exchange
            except Exception:
                exchange = None
            symbols_with_exchanges.append((sym, exchange))
        watchlist, rejected = validate_symbols(symbols_with_exchanges)
        for sym in rejected:
            remove_ticker(sym)
            logger.warning("Watchlist: removed {} — failed OTC/exchange validation", sym)
    except Exception as e:
        logger.warning("Watchlist fetch failed (non-fatal): {}", e)
        watchlist = []

    # Source 2: sector screener
    try:
        screened = get_screener_candidates()
    except Exception as e:
        logger.warning("Screener fetch failed (non-fatal): {}", e)
        screened = []

    # Source 3: LLM discovery proposals (already handles its own errors)
    discovered = run_discovery_cycle(
        positions=snapshot.positions,
        watchlist=watchlist,
        buying_power=snapshot.buying_power,
    )

    all_candidates = set(watchlist) | set(screened) | set(discovered)
    held = {p["symbol"] for p in snapshot.positions}
    new_candidates_raw = sorted(all_candidates - held)

    # Apply 24h cooldown — skip symbols evaluated recently
    new_candidates = filter_cooled_down(new_candidates_raw)

    counts = {
        "watchlist": len(watchlist),
        "screener": len(screened),
        "discovered": len(discovered),
        "total_new": len(new_candidates),
        "cooled_down": len(new_candidates_raw) - len(new_candidates),
    }
    logger.info(
        "Gathered {} new candidates: {} watchlist, {} screener, {} discovered (deduped against {} held)",
        len(new_candidates), len(watchlist), len(screened), len(discovered), len(held),
    )
    return new_candidates, counts


def run_cycle(dry_run: bool = True) -> dict:
    """Run the complete daily trading cycle.

    Stages:
        0. Lockfile acquisition (prevent overlapping runs)
        1. Initialize DB and authenticate with tastytrade
        2. Market status check (skip weekends)
        3. Account sync (fetch snapshot, write to DB)
        4. Circuit breaker check (halt if tripped)
        5. Stop-loss enforcement (before LLM calls)
        6. Consensus (query bull/bear LLMs)
        7. Order execution (for approved trades)
        8. Post-trade snapshot
        9. Circuit breaker evaluation (post-trade loss check)
        10. Daily snapshot recording (equity/drawdown tracking)
        11. Write structured JSON run log

    Args:
        dry_run: If True, no real orders are submitted.

    Returns:
        Dict summarizing the cycle result for downstream logging.
    """
    lock_fh = None
    try:
        # --- Stage 0: Lockfile ---
        lock_fh = _acquire_lock()
        if lock_fh is None:
            logger.warning("Another cycle is already running (lockfile held)")
            return {"status": "skipped", "reason": "another cycle running"}

        logger.info("=== Trading cycle started (dry_run={}) ===", dry_run)

        # --- Stage 1: Initialize ---
        init_db()
        client = TastytradeClient()
        client.authenticate()
        logger.info("Stage 1 complete: DB initialized, broker authenticated")

        # --- Stage 2: Market status check ---
        now = datetime.now()
        weekday = now.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
        if weekday >= 5:
            logger.info("Market closed (weekend, weekday={})", weekday)
            return {
                "status": "skipped",
                "reason": "market closed (weekend)",
                "timestamp": now.isoformat(),
            }
        logger.info("Stage 2 complete: market status = open (weekday={})", weekday)

        # --- Stage 3: Account sync ---
        snapshot = client.get_account_snapshot()
        synced = client.sync_positions_to_db(snapshot)
        logger.info(
            "Stage 3 complete: synced {} positions, NLV=${:.2f}, BP=${:.2f}",
            synced, snapshot.net_liquidating_value, snapshot.buying_power,
        )

        # --- Stage 4: Circuit breaker check ---
        cb = get_cb_status()  # Handles HALTED_DAILY auto-reset on new calendar day
        if cb.status != "ACTIVE":
            override = os.environ.get("OVERRIDE_CIRCUIT_BREAKER") == "1"
            if override:
                conn = get_db()
                try:
                    conn.execute(
                        "UPDATE circuit_breaker SET status='ACTIVE', reset_at=? WHERE id=1",
                        (datetime.now().isoformat(),),
                    )
                    conn.commit()
                finally:
                    conn.close()
                logger.warning("Circuit breaker manually overridden from {}", cb.status)
            else:
                logger.warning("Circuit breaker is {}: {}", cb.status, cb.reason)
                return {
                    "status": "halted",
                    "reason": f"circuit_breaker: {cb.status}",
                    "timestamp": now.isoformat(),
                }
        logger.info("Stage 4 complete: circuit breaker = ACTIVE")

        # --- Stage 5: Stop-loss enforcement ---
        stop_results = check_and_enforce_stops(client, snapshot, dry_run)
        if stop_results:
            triggered = [r["symbol"] for r in stop_results]
            logger.info("Stage 5 complete: stops triggered for {}", triggered)
        else:
            logger.info("Stage 5 complete: no stop-losses triggered")

        # --- Stage 5.5: Gather buy candidates from watchlist + screener + LLM discovery ---
        new_candidates, candidate_counts = gather_candidates(snapshot, dry_run)
        logger.info("Stage 5.5 complete: {} new buy candidates gathered", len(new_candidates))

        # --- Stage 6: Consensus ---
        settings = get_settings()
        # Combine: existing positions (for SELL/HOLD) + new candidates (for BUY)
        position_symbols = [p["symbol"] for p in snapshot.positions]
        candidates = position_symbols + [c for c in new_candidates if c not in position_symbols]

        consensus = None
        order_results = []

        if candidates:
            try:
                consensus = run_consensus_cycle(
                    positions=snapshot.positions,
                    buying_power=snapshot.buying_power,
                    candidates=candidates,
                )
                logger.info(
                    "Stage 6 complete: {} approved trades, {} disagreements",
                    len(consensus.approved_trades), len(consensus.disagreed_symbols),
                )
                # Mark all evaluated symbols with cooldown so they aren't re-queried for 24h
                mark_evaluated(candidates)
            except Exception as e:
                logger.error("Consensus cycle failed: {}", e)
                # Continue to post-trade snapshot -- consensus failure is not fatal
                consensus = None
        else:
            logger.info("Stage 6 skipped: no candidates for consensus")

        # --- Stage 7: Order execution ---
        if consensus and consensus.approved_trades:
            for trade in consensus.approved_trades:
                try:
                    result = execute_trade(
                        client,
                        trade,
                        Decimal(str(snapshot.buying_power)),
                        dry_run,
                    )
                    order_results.append(result)
                    logger.info(
                        "Executed {}: {} {} -- {}",
                        trade.symbol, trade.action, result["status"],
                        result.get("reason", ""),
                    )
                except Exception as e:
                    logger.error("Order execution failed for {}: {}", trade.symbol, e)
                    order_results.append({
                        "status": "error",
                        "ticker": trade.symbol,
                        "reason": str(e),
                    })
            logger.info("Stage 7 complete: {} orders processed", len(order_results))
        else:
            logger.info("Stage 7 skipped: no approved trades")

        # --- Stage 7b: Bear-only put trades (bypass consensus — bull never recommends puts) ---
        if consensus:
            put_recs = [
                r for r in consensus.bear_analysis.recommendations
                if r.action == "BUY_PUT" and r.confidence >= settings.min_confidence
            ]
            if put_recs:
                logger.info("Stage 7b: {} BUY_PUT recommendations from bear model", len(put_recs))
                for rec in put_recs:
                    try:
                        result = execute_put_trade(
                            client, rec, Decimal(str(snapshot.buying_power)), dry_run
                        )
                        order_results.append(result)
                        logger.info(
                            "Put trade {}: {} -- {}",
                            rec.symbol, result["status"], result.get("reason", ""),
                        )
                    except Exception as e:
                        logger.error("Put trade failed for {}: {}", rec.symbol, e)
                        order_results.append({
                            "status": "error",
                            "ticker": rec.symbol,
                            "action": "BUY_PUT",
                            "reason": str(e),
                        })
            else:
                logger.info("Stage 7b: no BUY_PUT recommendations from bear model")

        # --- Stage 8: Post-trade snapshot ---
        post_snapshot = None
        try:
            post_snapshot = client.get_account_snapshot()
            logger.info(
                "Stage 8 complete: post-trade NLV=${:.2f}",
                post_snapshot.net_liquidating_value,
            )
        except Exception as e:
            logger.error("Post-trade snapshot failed: {}", e)

        # --- Build result (stages 9-11 will enrich this) ---
        cycle_result = {
            "status": "complete",
            "timestamp": datetime.now().isoformat(),
            "dry_run": dry_run,
            "account": {
                "balance": snapshot.cash_balance,
                "buying_power": snapshot.buying_power,
                "nlv": snapshot.net_liquidating_value,
                "positions_count": len(snapshot.positions),
            },
            "candidates": candidate_counts,
            "stop_loss_results": stop_results,
            "consensus": {
                "approved": len(consensus.approved_trades),
                "disagreed": consensus.disagreed_symbols,
            } if consensus else None,
            "order_results": order_results,
            "post_trade_nlv": post_snapshot.net_liquidating_value if post_snapshot else None,
        }

        # --- Stage 9: Circuit breaker evaluation (post-trade) ---
        if post_snapshot:
            try:
                cb_eval = evaluate_circuit_breaker(
                    snapshot.net_liquidating_value,
                    post_snapshot.net_liquidating_value,
                )
                cycle_result["circuit_breaker"] = {
                    "status": cb_eval.status,
                    "tripped_at": cb_eval.tripped_at,
                    "reason": cb_eval.reason,
                }
                logger.info("Stage 9 complete: circuit breaker eval = {}", cb_eval.status)
            except Exception as e:
                logger.error("Circuit breaker evaluation failed: {}", e)
                cycle_result["circuit_breaker"] = {"status": "error", "reason": str(e)}
        else:
            cycle_result["circuit_breaker"] = None

        # --- Stage 10: Daily snapshot recording ---
        if post_snapshot:
            try:
                ds = record_daily_snapshot(
                    post_snapshot,
                    previous_nlv=snapshot.net_liquidating_value,
                )
                cycle_result["daily_snapshot"] = {
                    "date": ds.snapshot_date,
                    "equity": ds.total_equity,
                    "pnl": ds.daily_pnl,
                    "pnl_pct": ds.daily_pnl_pct,
                    "peak": ds.peak_equity,
                    "drawdown_pct": ds.drawdown_pct,
                }
                logger.info("Stage 10 complete: daily snapshot recorded")
            except Exception as e:
                logger.error("Daily snapshot recording failed: {}", e)
                cycle_result["daily_snapshot"] = None
        else:
            cycle_result["daily_snapshot"] = None

        # --- Stage 11: Write run log ---
        try:
            write_run_log(cycle_result)
            logger.info("Stage 11 complete: run log written")
        except Exception as e:
            logger.error("Run log write failed: {}", e)

        logger.info("=== Trading cycle complete ===")
        return cycle_result

    except Exception as e:
        logger.exception("Cycle failed: {}", e)
        return {
            "status": "error",
            "reason": str(e),
            "timestamp": datetime.now().isoformat(),
        }
    finally:
        _release_lock(lock_fh)
