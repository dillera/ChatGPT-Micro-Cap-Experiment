"""CLI entry point for the Micro-Cap AI Trading Bot.

Run with: python -m src --dry-run
"""
from __future__ import annotations

import argparse
import sys

from loguru import logger

from src.config import get_settings
from src.db import init_db
from src.logger import setup_logging
from src.pdt import check_pdt_limit, get_day_trade_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Micro-Cap AI Trading Bot")
    parser.add_argument("--dry-run", action="store_true", help="Run without placing orders")
    parser.add_argument("--sync-only", action="store_true", help="Only sync positions, do not trade")
    args = parser.parse_args()

    settings = get_settings()
    if args.dry_run:
        settings.dry_run = True

    setup_logging(log_level=settings.log_level, log_dir=settings.log_dir)
    logger.info("Starting trading bot (dry_run={})", settings.dry_run)

    # Initialize database
    init_db()

    # Check PDT limit
    day_trade_count = get_day_trade_count()
    pdt_ok = check_pdt_limit()
    logger.info("PDT day-trade count: {count}/3 (safe to trade: {ok})",
                count=day_trade_count, ok=pdt_ok)

    # Connect to tastytrade
    from src.broker import TastytradeClient

    client = TastytradeClient()
    try:
        client.authenticate()
    except Exception as e:
        logger.error("Failed to authenticate with tastytrade: {}", e)
        sys.exit(1)

    # Fetch account state
    try:
        snapshot = client.get_account_snapshot()
    except Exception as e:
        logger.error("Failed to fetch account snapshot: {}", e)
        sys.exit(1)

    logger.info("Account: {acct}", acct=snapshot.account_number)
    logger.info("Cash balance: ${cash:.2f}", cash=snapshot.cash_balance)
    logger.info("Buying power: ${bp:.2f}", bp=snapshot.buying_power)
    logger.info("Net liquidating value: ${nlv:.2f}", nlv=snapshot.net_liquidating_value)
    logger.info("Positions: {count}", count=len(snapshot.positions))

    for pos in snapshot.positions:
        logger.info("  {symbol}: {shares} shares @ ${price:.2f} (value: ${value:.2f})",
                     symbol=pos["symbol"], shares=pos["shares"],
                     price=pos["price"], value=pos["market_value"])

    # Sync positions to SQLite
    synced = client.sync_positions_to_db(snapshot)
    logger.info("Synced {count} positions to SQLite", count=synced)

    # Print dry-run summary
    if settings.dry_run:
        print("\n--- DRY RUN SUMMARY ---")
        print(f"Account: {snapshot.account_number}")
        print(f"Cash Balance: ${snapshot.cash_balance:.2f}")
        print(f"Buying Power: ${snapshot.buying_power:.2f}")
        print(f"Net Liquidating Value: ${snapshot.net_liquidating_value:.2f}")
        print(f"Positions: {len(snapshot.positions)}")
        print(f"PDT Day Trades: {day_trade_count}/3")
        for pos in snapshot.positions:
            print(f"  {pos['symbol']}: {pos['shares']} shares @ ${pos['price']:.2f}")
        print("--- END DRY RUN ---")

    logger.info("Bot run complete (dry_run={})", settings.dry_run)


if __name__ == "__main__":
    main()
