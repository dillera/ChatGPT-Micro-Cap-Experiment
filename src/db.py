from __future__ import annotations

import sqlite3
from pathlib import Path

from src.config import get_settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    shares      REAL NOT NULL,
    buy_price   REAL NOT NULL,
    cost_basis  REAL NOT NULL,
    stop_loss   REAL,
    opened_at   TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    executed_at     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    action          TEXT NOT NULL,
    shares          REAL NOT NULL,
    price           REAL NOT NULL,
    total_value     REAL NOT NULL,
    commission      REAL DEFAULT 0,
    stop_loss       REAL,
    reason          TEXT,
    order_id        TEXT,
    source          TEXT NOT NULL DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS daily_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT NOT NULL UNIQUE,
    total_equity    REAL NOT NULL,
    cash_balance    REAL NOT NULL,
    positions_value REAL NOT NULL,
    daily_pnl       REAL NOT NULL,
    daily_pnl_pct   REAL NOT NULL,
    peak_equity     REAL NOT NULL,
    drawdown_pct    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    called_at       TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt_hash     TEXT NOT NULL,
    raw_response    TEXT NOT NULL,
    parsed_ok       INTEGER NOT NULL,
    parse_error     TEXT,
    consensus_id    INTEGER
);

CREATE TABLE IF NOT EXISTS consensus_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    decided_at      TEXT NOT NULL,
    gpt4_audit_id   INTEGER NOT NULL,
    claude_audit_id INTEGER NOT NULL,
    agreed_tickers  TEXT,
    disagreed_tickers TEXT,
    trades_executed INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS circuit_breaker (
    id          INTEGER PRIMARY KEY DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'ACTIVE',
    tripped_at  TEXT,
    reason      TEXT,
    reset_at    TEXT
);

CREATE TABLE IF NOT EXISTS session_cache (
    id                  INTEGER PRIMARY KEY DEFAULT 1,
    serialized_session  TEXT NOT NULL,
    expires_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS day_trade_counter (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    traded_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_executed_at ON trades(executed_at);
CREATE INDEX IF NOT EXISTS idx_daily_snapshots_date ON daily_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_day_trade_counter_date ON day_trade_counter(traded_at);

CREATE TABLE IF NOT EXISTS watchlist (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol    TEXT NOT NULL,
    notes     TEXT,
    added_at  TEXT NOT NULL,
    active    INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlist_symbol ON watchlist(symbol);

CREATE TABLE IF NOT EXISTS screener_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sector      TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    market_cap  REAL,
    avg_volume  REAL,
    exchange    TEXT,
    cached_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_screener_cache_sector ON screener_cache(sector);

CREATE TABLE IF NOT EXISTS symbol_cooldown (
    symbol          TEXT PRIMARY KEY,
    last_evaluated_at TEXT NOT NULL,
    next_eval_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spread_positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    spread_type     TEXT NOT NULL,
    long_strike     REAL NOT NULL,
    short_strike    REAL NOT NULL,
    expiry          TEXT NOT NULL,
    dte_at_open     INTEGER NOT NULL,
    contracts       INTEGER NOT NULL DEFAULT 1,
    debit_paid      REAL NOT NULL,
    max_profit      REAL NOT NULL,
    max_loss        REAL NOT NULL,
    target_exit_pct REAL NOT NULL DEFAULT 0.50,
    opened_at       TEXT NOT NULL,
    closed_at       TEXT,
    status          TEXT NOT NULL DEFAULT 'OPEN',
    order_id        TEXT,
    long_occ        TEXT NOT NULL,
    short_occ       TEXT NOT NULL,
    daily_session   TEXT,
    entry_delta     REAL
);

CREATE TABLE IF NOT EXISTS daily_options_target (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_date     TEXT NOT NULL UNIQUE,
    target_amount   REAL NOT NULL DEFAULT 100.0,
    realized_pnl    REAL NOT NULL DEFAULT 0.0,
    unrealized_pnl  REAL NOT NULL DEFAULT 0.0,
    trades_today    INTEGER NOT NULL DEFAULT 0,
    max_trades      INTEGER NOT NULL DEFAULT 3,
    target_hit      INTEGER NOT NULL DEFAULT 0,
    stop_loss_hit   INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_spread_positions_symbol ON spread_positions(symbol);
CREATE INDEX IF NOT EXISTS idx_spread_positions_status ON spread_positions(status);
CREATE INDEX IF NOT EXISTS idx_spread_positions_opened_at ON spread_positions(opened_at);
CREATE INDEX IF NOT EXISTS idx_daily_options_target_date ON daily_options_target(target_date);
"""


def get_db(db_path: str | None = None) -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode and Row factory."""
    path = db_path or get_settings().db_path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | None = None) -> None:
    """Create all tables and indexes. Idempotent."""
    conn = get_db(db_path)
    conn.executescript(SCHEMA_SQL)
    # Ensure circuit_breaker has its single row
    conn.execute(
        "INSERT OR IGNORE INTO circuit_breaker (id, status) VALUES (1, 'ACTIVE')"
    )
    conn.commit()
    conn.close()
