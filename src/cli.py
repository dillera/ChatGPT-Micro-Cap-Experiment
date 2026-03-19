"""CLI entry point for the Micro-Cap AI Trading Bot.

Run with:
  python -m src --dry-run        # Full cycle, no real orders
  python -m src                  # Full autonomous cycle
  python -m src --sync-only      # Only sync positions from tastytrade
  python -m src watchlist add ABEO   # Add ticker to watchlist
  python -m src watchlist remove ABEO # Remove ticker from watchlist
  python -m src watchlist list       # List active watchlist tickers
"""
from __future__ import annotations

import argparse
import sys

from loguru import logger

from src.config import get_settings
from src.db import init_db
from src.logger import setup_logging


def _handle_watchlist_add(args: argparse.Namespace) -> None:
    """Handle `watchlist add` subcommand."""
    init_db()
    from src.watchlist import add_ticker

    result = add_ticker(args.ticker, notes=args.notes)
    if result:
        print(f"Added {args.ticker.upper()} to watchlist.")
    else:
        print(f"{args.ticker.upper()} is already on the watchlist.")


def _handle_watchlist_remove(args: argparse.Namespace) -> None:
    """Handle `watchlist remove` subcommand."""
    init_db()
    from src.watchlist import remove_ticker

    result = remove_ticker(args.ticker)
    if result:
        print(f"Removed {args.ticker.upper()} from watchlist.")
    else:
        print(f"{args.ticker.upper()} is not on the watchlist.")


def _handle_watchlist_list(args: argparse.Namespace) -> None:
    """Handle `watchlist list` subcommand."""
    init_db()
    from src.watchlist import list_tickers

    tickers = list_tickers()
    if tickers:
        print("Active watchlist tickers:")
        for t in tickers:
            print(f"  {t}")
    else:
        print("Watchlist is empty.")


def _handle_run(args: argparse.Namespace) -> None:
    """Handle the default run behavior (trading cycle)."""
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Micro-Cap AI Trading Bot")
    subparsers = parser.add_subparsers(dest="command")

    # --- watchlist subcommand ---
    watchlist_parser = subparsers.add_parser("watchlist", help="Manage watchlist tickers")
    watchlist_sub = watchlist_parser.add_subparsers(dest="watchlist_action")

    add_parser = watchlist_sub.add_parser("add", help="Add a ticker to the watchlist")
    add_parser.add_argument("ticker", type=str, help="Ticker symbol to add")
    add_parser.add_argument("--notes", type=str, default=None, help="Optional notes")
    add_parser.set_defaults(func=_handle_watchlist_add)

    remove_parser = watchlist_sub.add_parser("remove", help="Remove a ticker from the watchlist")
    remove_parser.add_argument("ticker", type=str, help="Ticker symbol to remove")
    remove_parser.set_defaults(func=_handle_watchlist_remove)

    list_parser = watchlist_sub.add_parser("list", help="List active watchlist tickers")
    list_parser.set_defaults(func=_handle_watchlist_list)

    # --- default run behavior (backward compatible) ---
    # When no subcommand is given, treat as trading cycle run
    parser.add_argument("--dry-run", action="store_true", help="Run full cycle without placing orders")
    parser.add_argument("--sync-only", action="store_true", help="Only sync positions, do not trade")

    args = parser.parse_args()

    if args.command == "watchlist":
        if hasattr(args, "func"):
            args.func(args)
        else:
            watchlist_parser.print_help()
    elif args.command is None:
        # No subcommand = trading cycle (backward compatible)
        _handle_run(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
