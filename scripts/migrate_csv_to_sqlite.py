"""One-time migration of CSV portfolio and trade data into SQLite.

Usage: uv run python scripts/migrate_csv_to_sqlite.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DATA_DIR, PROJECT_ROOT
from src.db import get_db, init_db

CSV_DIR = PROJECT_ROOT / "Scripts and CSV Files"
PORTFOLIO_CSV = CSV_DIR / "chatgpt_portfolio_update.csv"
TRADE_LOG_CSV = CSV_DIR / "chatgpt_trade_log.csv"


def _safe_float(val: object, default: float = 0.0) -> float:
    """Convert a value to float, returning default for NaN/None/empty."""
    if val is None:
        return default
    try:
        f = float(val)
        return default if math.isnan(f) else f
    except (ValueError, TypeError):
        return default


def migrate_portfolio(conn, portfolio_csv: Path) -> tuple[int, int]:
    """Migrate portfolio CSV into daily_snapshots and positions tables.

    Returns (snapshot_count, position_count).
    """
    df = pd.read_csv(portfolio_csv)

    # --- Daily snapshots from TOTAL rows ---
    totals = df[df["Ticker"] == "TOTAL"].copy()
    totals = totals.sort_values("Date")

    peak_equity = 0.0
    prev_equity = 0.0
    snapshot_count = 0

    for _, row in totals.iterrows():
        total_equity = _safe_float(row.get("Total Equity"))
        cash_balance = _safe_float(row.get("Cash Balance"))
        positions_value = _safe_float(row.get("Total Value"))
        daily_pnl = _safe_float(row.get("PnL"))

        # Calculate peak and drawdown
        if total_equity > peak_equity:
            peak_equity = total_equity
        drawdown_pct = ((peak_equity - total_equity) / peak_equity * 100) if peak_equity > 0 else 0.0

        # daily_pnl_pct relative to previous day equity
        daily_pnl_pct = (daily_pnl / prev_equity * 100) if prev_equity > 0 else 0.0
        prev_equity = total_equity

        conn.execute(
            """INSERT OR IGNORE INTO daily_snapshots
               (snapshot_date, total_equity, cash_balance, positions_value,
                daily_pnl, daily_pnl_pct, peak_equity, drawdown_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["Date"],
                total_equity,
                cash_balance,
                positions_value,
                daily_pnl,
                round(daily_pnl_pct, 4),
                peak_equity,
                round(drawdown_pct, 4),
            ),
        )
        snapshot_count += 1

    # --- Current positions from LATEST date only ---
    latest_date = totals["Date"].iloc[-1]
    position_rows = df[(df["Date"] == latest_date) & (df["Ticker"] != "TOTAL")]
    position_count = 0

    for _, row in position_rows.iterrows():
        stop_loss = _safe_float(row.get("Stop Loss")) or None
        conn.execute(
            """INSERT INTO positions
               (symbol, shares, buy_price, cost_basis, stop_loss, opened_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                row["Ticker"],
                _safe_float(row["Shares"]),
                _safe_float(row["Buy Price"]),
                _safe_float(row["Cost Basis"]),
                stop_loss,
                row["Date"],
                row["Date"],
            ),
        )
        position_count += 1

    conn.commit()
    return snapshot_count, position_count


def migrate_trades(conn, trade_csv: Path) -> int:
    """Migrate trade log CSV into trades table. Returns trade count."""
    df = pd.read_csv(trade_csv)
    trade_count = 0

    for _, row in df.iterrows():
        shares_bought = _safe_float(row.get("Shares Bought"))
        shares_sold = _safe_float(row.get("Shares Sold"))
        buy_price = _safe_float(row.get("Buy Price"))
        sell_price = _safe_float(row.get("Sell Price"))
        cost_basis = _safe_float(row.get("Cost Basis"))
        pnl = _safe_float(row.get("PnL"))
        reason = row.get("Reason")
        if pd.isna(reason):
            reason = None

        if shares_bought > 0:
            action = "BUY"
            shares = shares_bought
            price = buy_price
            total_value = cost_basis
        elif shares_sold > 0:
            action = "SELL"
            shares = shares_sold
            price = sell_price
            total_value = shares_sold * sell_price
        else:
            continue  # Skip rows with no shares

        conn.execute(
            """INSERT INTO trades
               (executed_at, symbol, action, shares, price, total_value,
                commission, reason, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["Date"],
                row["Ticker"],
                action,
                shares,
                price,
                round(total_value, 2),
                0.0,
                reason,
                "csv_migration",
            ),
        )
        trade_count += 1

    conn.commit()
    return trade_count


def main() -> None:
    db_path = str(DATA_DIR / "trading_bot.db")

    # Remove existing DB to ensure clean migration (idempotent)
    db_file = Path(db_path)
    if db_file.exists():
        db_file.unlink()

    init_db(db_path)
    conn = get_db(db_path)

    print(f"Migrating portfolio data from: {PORTFOLIO_CSV}")
    snapshots, positions = migrate_portfolio(conn, PORTFOLIO_CSV)
    print(f"  -> {snapshots} daily snapshots, {positions} current positions")

    print(f"Migrating trade log from: {TRADE_LOG_CSV}")
    trades = migrate_trades(conn, TRADE_LOG_CSV)
    print(f"  -> {trades} trades migrated")

    conn.close()
    print(f"\nMigration complete: {db_path}")
    print(f"  {snapshots} daily snapshots, {positions} positions, {trades} trades")


if __name__ == "__main__":
    main()
