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
