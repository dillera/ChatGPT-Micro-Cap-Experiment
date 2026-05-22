"""Trading cycle orchestrator — schedules morning/midday/close/monitor windows.

Run with:
  uv run python -m src scheduler [--dry-run]

Windows fire on market days at these ET times:
  09:45  morning  — ORB entry
  10:30  monitor  — position management
  11:00  midday   — mean reversion entry
  12:00  monitor  — position management
  13:00  monitor  — position management
  15:00  close    — pre-close positioning
  15:30  monitor  — final position check
  15:50  eod      — force-close safety net (closes any open spreads)
"""
from __future__ import annotations

import signal
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from src.db import init_db
from src.logger import setup_logging
from src.config import get_settings

ET = ZoneInfo("America/New_York")


def _is_market_day() -> bool:
    return datetime.now(ET).weekday() < 5


def _run_window(window: str, dry_run: bool) -> None:
    """Execute one options cycle window. Swallows all exceptions so the scheduler stays alive."""
    if not _is_market_day():
        logger.info("Scheduler: {} skipped — not a market day", window)
        return

    now = datetime.now(ET).strftime("%H:%M")
    logger.info("Scheduler: firing window={} dry_run={} at {}", window, dry_run, now)

    try:
        from src.options_cycle import run_options_cycle
        result = run_options_cycle(window=window, dry_run=dry_run)
        status = result.get("status", "error")
        pnl = result.get("daily_pnl", 0.0)
        reason = result.get("skip_reason") or "OK"
        logger.info(
            "Scheduler: window={} status={} pnl=${:.2f} reason={}",
            window, status, pnl, reason,
        )
    except Exception:
        logger.exception("Scheduler: unhandled error in window={}", window)


def _run_equity_orb(dry_run: bool) -> None:
    """Run the equity ORB strategy across the configured universe.

    Fires once per morning (9:45 ET). Evaluates SPY, QQQ, IWM and takes
    the highest-confidence breakout signal via the arbiter pattern.
    """
    if not _is_market_day():
        return

    logger.info("Scheduler: firing equity ORB scan dry={}", dry_run)
    try:
        from src.broker import TastytradeClient
        from src.market_context import fetch_market_context
        from src.equity_orb_strategy import evaluate_equity_orb_signal, execute_equity_orb_trade

        client = TastytradeClient()
        client.authenticate()
        snapshot = client.get_account_snapshot()
        buying_power = snapshot.buying_power

        symbols = ["SPY", "QQQ", "IWM"]
        best_signal = None

        for sym in symbols:
            ctx = fetch_market_context(sym)
            sig = evaluate_equity_orb_signal(ctx, buying_power)
            if sig is None:
                continue
            if best_signal is None or sig.confidence > best_signal.confidence:
                best_signal = sig

        if best_signal is None:
            logger.info("Scheduler: equity ORB — no breakout signals this morning")
            return

        logger.info(
            "Scheduler: equity ORB winner: {} {} confidence={}",
            best_signal.direction, best_signal.symbol, best_signal.confidence,
        )
        result = execute_equity_orb_trade(client, best_signal, dry_run=dry_run)
        logger.info("Scheduler: equity ORB result: {}", result.get("status"))

    except Exception:
        logger.exception("Scheduler: equity ORB error")


def _run_hma_momentum_scan(dry_run: bool) -> None:
    """Midday HMA + Alligator equity momentum scan across SPY, QQQ, IWM.

    Fires at 11:00 ET after the ORB window has closed. Picks the highest-
    confidence directional signal from the three symbols and executes if
    confidence exceeds 0.70.
    """
    if not _is_market_day():
        return

    logger.info("Scheduler: firing HMA+Alligator equity momentum scan dry={}", dry_run)
    try:
        from src.broker import TastytradeClient
        from src.market_context import fetch_market_context
        from src.hma_alligator_strategy import evaluate_hma_equity_signal, execute_hma_equity_trade

        client = TastytradeClient()
        client.authenticate()
        snapshot = client.get_account_snapshot()
        buying_power = snapshot.buying_power

        symbols = ["SPY", "QQQ", "IWM"]
        best_signal = None

        for sym in symbols:
            ctx = fetch_market_context(sym)
            sig = evaluate_hma_equity_signal(ctx, buying_power)
            if sig is None:
                continue
            if best_signal is None or sig.confidence > best_signal.confidence:
                best_signal = sig

        if best_signal is None:
            logger.info("Scheduler: HMA scan — no momentum signal this midday")
            return

        if best_signal.confidence < 0.70:
            logger.info(
                "Scheduler: HMA scan — best signal conf={:.2f} below 0.70 threshold, skipping",
                best_signal.confidence,
            )
            return

        logger.info(
            "Scheduler: HMA equity winner: {} {} HMA={} Alli={} conf={}",
            best_signal.direction, best_signal.symbol,
            best_signal.hma_trend, best_signal.alligator_state, best_signal.confidence,
        )
        result = execute_hma_equity_trade(client, best_signal, dry_run=dry_run)
        logger.info("Scheduler: HMA equity result: {}", result.get("status"))

    except Exception:
        logger.exception("Scheduler: HMA+Alligator momentum scan error")


def _run_eod(dry_run: bool) -> None:
    """Safety-net monitor run at 15:50 — the existing 15:45 time gate in
    check_and_close_open_spreads will force-close any 0DTE spreads still open."""
    logger.info("Scheduler: EOD safety-net monitor run")
    _run_window("monitor", dry_run)


def _heartbeat() -> None:
    now = datetime.now(ET).strftime("%Y-%m-%d %H:%M %Z")
    logger.info("Scheduler: heartbeat — alive at {}", now)


def build_scheduler(dry_run: bool) -> BlockingScheduler:
    """Construct and return a configured BlockingScheduler (not yet started)."""
    scheduler = BlockingScheduler(timezone=ET)

    # Equity ORB: fires at 9:45 alongside the options morning window
    scheduler.add_job(
        _run_equity_orb,
        trigger=CronTrigger(day_of_week="mon-fri", hour=9, minute=45, timezone=ET),
        args=[dry_run],
        id="equity_orb_0945",
        name="equity-orb 09:45 ET",
        misfire_grace_time=120,
        coalesce=True,
        max_instances=1,
    )

    # HMA + Alligator equity momentum scan — fires at midday alongside the options midday window
    scheduler.add_job(
        _run_hma_momentum_scan,
        trigger=CronTrigger(day_of_week="mon-fri", hour=11, minute=0, timezone=ET),
        args=[dry_run],
        id="hma_momentum_1100",
        name="hma-alligator-equity 11:00 ET",
        misfire_grace_time=120,
        coalesce=True,
        max_instances=1,
    )

    window_schedule = [
        ("morning", 9, 45),
        ("monitor", 10, 30),
        ("midday", 11, 0),
        ("monitor", 12, 0),
        ("monitor", 13, 0),
        ("close", 15, 0),
        ("monitor", 15, 30),
    ]

    for window, hour, minute in window_schedule:
        scheduler.add_job(
            _run_window,
            trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=ET),
            args=[window, dry_run],
            id=f"{window}_{hour:02d}{minute:02d}",
            name=f"options-{window} {hour:02d}:{minute:02d} ET",
            misfire_grace_time=120,
            coalesce=True,
            max_instances=1,
        )

    scheduler.add_job(
        _run_eod,
        trigger=CronTrigger(day_of_week="mon-fri", hour=15, minute=50, timezone=ET),
        args=[dry_run],
        id="eod_1550",
        name="options-eod 15:50 ET",
        misfire_grace_time=60,
        coalesce=True,
        max_instances=1,
    )

    # Hourly heartbeat so we know the daemon is alive
    scheduler.add_job(
        _heartbeat,
        trigger=CronTrigger(minute=0),
        id="heartbeat",
        name="heartbeat",
    )

    return scheduler


def run_scheduler(dry_run: bool = True) -> None:
    """Start the blocking scheduler. Runs until SIGINT/SIGTERM."""
    settings = get_settings()
    setup_logging(log_level=settings.log_level, log_dir=settings.log_dir)
    init_db()

    scheduler = build_scheduler(dry_run=dry_run)

    def _shutdown(sig, frame):
        logger.info("Scheduler: received signal {}, shutting down", sig)
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    mode = "DRY RUN" if dry_run else "LIVE"
    logger.info("Scheduler: starting ({}) — {} jobs registered", mode, len(scheduler.get_jobs()))
    for job in scheduler.get_jobs():
        next_run = getattr(job, "next_run_time", None) or "pending"
        logger.info("  {} — next: {}", job.name, next_run)

    scheduler.start()
