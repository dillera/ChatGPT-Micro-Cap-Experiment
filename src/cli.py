"""CLI entry point for the Micro-Cap AI Trading Bot.

Run with:
  python -m src --dry-run        # Full cycle, no real orders
  python -m src                  # Full autonomous cycle
  python -m src --sync-only      # Only sync positions from tastytrade
"""
from __future__ import annotations

import argparse
import sys

from loguru import logger

from src.config import get_settings
from src.db import init_db
from src.logger import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Micro-Cap AI Trading Bot")
    parser.add_argument("--dry-run", action="store_true", help="Run full cycle without placing orders")
    parser.add_argument("--sync-only", action="store_true", help="Only sync positions, do not trade")
    args = parser.parse_args()

    settings = get_settings()
    if args.dry_run:
        settings.dry_run = True

    setup_logging(log_level=settings.log_level, log_dir=settings.log_dir)

    if args.sync_only:
        # Lightweight sync-only mode (preserved from Phase 1)
        init_db()
        from src.broker import TastytradeClient
        client = TastytradeClient()
        try:
            client.authenticate()
            snapshot = client.get_account_snapshot()
            synced = client.sync_positions_to_db(snapshot)
            logger.info("Synced {} positions", synced)
            print(f"Synced {synced} positions. NLV: ${snapshot.net_liquidating_value:.2f}")
        except Exception as e:
            logger.error("Sync failed: {}", e)
            sys.exit(1)
        return

    # Full trading cycle
    from src.cycle import run_cycle

    result = run_cycle(dry_run=settings.dry_run)

    # Map status to exit code: 0=success/skipped, 1=error
    status = result.get("status", "error")
    if status in ("complete", "skipped", "halted"):
        logger.info("Cycle finished: {} ({})", status, result.get("reason", ""))
        sys.exit(0)
    else:
        logger.error("Cycle failed: {} ({})", status, result.get("reason", ""))
        sys.exit(1)


if __name__ == "__main__":
    main()
