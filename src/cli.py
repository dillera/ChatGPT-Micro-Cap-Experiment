"""CLI entry point for the AI Options Trading Bot.

Run with:
  python -m src options --window morning [--dry-run]   # 0DTE ORB entry window
  python -m src options --window midday  [--dry-run]   # Mid-day mean reversion
  python -m src options --window close   [--dry-run]   # Pre-close positioning
  python -m src options --window monitor [--dry-run]   # Position management only
  python -m src status                                  # Today's P&L and open spreads
  python -m src close-all [--dry-run]                  # Emergency close all spreads

Legacy equity commands (preserved):
  python -m src --dry-run        # Full equity cycle, no real orders
  python -m src --sync-only      # Only sync positions from tastytrade
  python -m src watchlist add ABEO
  python -m src watchlist remove ABEO
  python -m src watchlist list
  python -m src screener [--sector biotech] [--add-to-watchlist]
  python -m src research
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


def _handle_screener(args: argparse.Namespace) -> None:
    """Handle `screener` subcommand — run sector screener and print results."""
    init_db()
    from src.screener import screen_sector, get_screener_candidates
    from src.config import get_settings

    settings = get_settings()
    sectors = [args.sector] if args.sector else settings.screener_sectors

    for sector in sectors:
        print(f"\nScreening sector: {sector}")
        tickers = screen_sector(sector)
        if tickers:
            print(f"  Found {len(tickers)} candidates:")
            for t in tickers:
                print(f"    {t}")
        else:
            print("  No candidates found (check yfinance connectivity).")

    if args.add_to_watchlist:
        from src.watchlist import add_ticker
        all_tickers = get_screener_candidates()
        added = 0
        for t in all_tickers:
            if add_ticker(t, notes=f"From screener ({', '.join(sectors)})"):
                added += 1
        print(f"\nAdded {added} new tickers to watchlist.")


def _handle_options_run(args: argparse.Namespace) -> None:
    """Handle `options` subcommand — run 0DTE options cycle for specified window."""
    settings = get_settings()
    if args.dry_run:
        settings.dry_run = True

    setup_logging(log_level=settings.log_level, log_dir=settings.log_dir)
    init_db()

    from src.options_cycle import run_options_cycle

    window = args.window
    if window == "all":
        # Evaluate windows in priority order; stop after first trade
        for w in ["morning", "midday", "close"]:
            result = run_options_cycle(window=w, dry_run=args.dry_run)
            if result.get("status") in ("complete",) and result.get("spreads_opened"):
                break
    else:
        result = run_options_cycle(window=window, dry_run=args.dry_run)

    status = result.get("status", "error")
    if status in ("complete", "skipped", "halted"):
        print(f"Options cycle [{window}]: {status} — {result.get('skip_reason', 'OK')}")
        print(f"Daily P&L: ${result.get('daily_pnl', 0.0):.2f}")
        sys.exit(0)
    else:
        logger.error("Options cycle error: {}", result.get("skip_reason", "unknown"))
        sys.exit(1)


def _handle_status(args: argparse.Namespace) -> None:
    """Handle `status` subcommand — print today's P&L and open spreads."""
    init_db()
    from src.daily_target import get_today_target
    from src.db import get_db

    state = get_today_target()
    print(f"\n=== Daily Options Status ({state.target_date}) ===")
    print(f"Target:     ${state.target_amount:.2f}")
    print(f"P&L:        ${state.realized_pnl:.2f}")
    print(f"Trades:     {state.trades_today}/{state.max_trades}")
    print(f"Target Hit: {'YES' if state.target_hit else 'no'}")
    print(f"Stop Hit:   {'YES' if state.stop_loss_hit else 'no'}")

    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM spread_positions WHERE status='OPEN'").fetchall()
    finally:
        conn.close()

    if rows:
        print(f"\n=== Open Spreads ({len(rows)}) ===")
        for r in rows:
            print(
                f"  [{r['id']}] {r['symbol']} {r['spread_type']} "
                f"${r['long_strike']:.0f}/${r['short_strike']:.0f} "
                f"exp={r['expiry']} x{r['contracts']} "
                f"debit=${r['debit_paid']:.2f} opened={r['opened_at'][:16]}"
            )
    else:
        print("\nNo open spreads.")


def _handle_close_all(args: argparse.Namespace) -> None:
    """Handle `close-all` subcommand — emergency close all open spreads."""
    init_db()
    setup_logging(log_level=get_settings().log_level, log_dir=get_settings().log_dir)

    from src.broker import TastytradeClient
    from src.orders import check_and_close_open_spreads

    client = TastytradeClient()
    client.authenticate()

    results = check_and_close_open_spreads(client, dry_run=args.dry_run)
    if results:
        print(f"Closed {len(results)} spread(s):")
        for r in results:
            pnl = r.get("realized_pnl", 0.0)
            print(f"  {r.get('symbol')} — P&L: ${pnl:.2f} ({r.get('status')})")
    else:
        print("No open spreads to close.")


def _handle_research() -> None:
    """Handle `research` subcommand — run multi-strategy pipeline."""
    from src.db import init_db
    from src.research import run_research

    init_db()
    added = run_research()
    print(f"Research complete: {added} new symbol(s) added to watchlist.")


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

    # --- screener subcommand ---
    screener_parser = subparsers.add_parser("screener", help="Run sector screener")
    screener_parser.add_argument(
        "--sector", type=str, default=None,
        help="Sector to screen (biotech, tech). Defaults to all configured sectors."
    )
    screener_parser.add_argument(
        "--add-to-watchlist", action="store_true",
        help="Automatically add all screener results to watchlist"
    )
    screener_parser.set_defaults(func=_handle_screener)

    # --- research subcommand ---
    research_parser = subparsers.add_parser(  # noqa: F841
        "research", help="Run multi-strategy research pipeline to populate watchlist"
    )
    research_parser.set_defaults(func=lambda _: _handle_research())

    # --- options subcommand (0DTE vertical spread trading) ---
    options_parser = subparsers.add_parser("options", help="Run 0DTE options trading cycle")
    options_parser.add_argument(
        "--window",
        choices=["morning", "midday", "close", "monitor", "all"],
        default="all",
        help="Trading window to run (default: all = evaluate all windows in order)",
    )
    options_parser.add_argument("--dry-run", action="store_true", help="Paper trade mode — no real orders")
    options_parser.set_defaults(func=_handle_options_run)

    # --- status subcommand ---
    status_parser = subparsers.add_parser("status", help="Show today's P&L and open spread positions")
    status_parser.set_defaults(func=_handle_status)

    # --- close-all subcommand ---
    close_parser = subparsers.add_parser("close-all", help="Emergency close all open spread positions")
    close_parser.add_argument("--dry-run", action="store_true", help="Dry-run close only")
    close_parser.set_defaults(func=_handle_close_all)

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
    elif args.command in ("screener", "research", "options", "status", "close-all"):
        args.func(args)
    elif args.command is None:
        # No subcommand = legacy equity trading cycle (backward compatible)
        _handle_run(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
