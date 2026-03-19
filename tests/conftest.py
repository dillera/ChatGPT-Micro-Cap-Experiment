"""Shared test fixtures for the micro-cap trading bot."""
from __future__ import annotations

import sqlite3

import pytest

from src.config import Settings
from src.db import init_db, get_db


@pytest.fixture()
def mock_settings(monkeypatch):
    """Return a Settings instance with test API keys."""
    s = Settings(
        openai_api_key="test-openai-key-123",
        anthropic_api_key="test-anthropic-key-456",
        db_path=":memory:",
        dry_run=True,
    )
    # Patch get_settings to return our test settings
    monkeypatch.setattr("src.config._settings", s)
    return s


class _NonClosingConnection:
    """Wrapper that delegates all sqlite3.Connection methods but ignores close().

    This allows test fixtures to remain open for assertions after production
    code calls conn.close().
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def close(self):
        """No-op -- keeps the connection open for test assertions."""
        pass

    def real_close(self):
        """Actually close the underlying connection."""
        self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture()
def test_db():
    """Create an in-memory SQLite DB initialized with the full schema.

    Returns a wrapper that ignores close() so tests can assert after
    production code calls conn.close().
    """
    from src.db import SCHEMA_SQL

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO circuit_breaker (id, status) VALUES (1, 'ACTIVE')"
    )
    conn.commit()
    wrapper = _NonClosingConnection(conn)
    yield wrapper
    conn.close()


@pytest.fixture()
def sample_positions():
    """Return a list of position dicts matching AccountSnapshot.positions format."""
    return [
        {"symbol": "RXRX", "shares": 100.0, "price": 8.50, "market_value": 850.0},
        {"symbol": "ATOS", "shares": 200.0, "price": 3.25, "market_value": 650.0},
        {"symbol": "MNMD", "shares": 150.0, "price": 5.00, "market_value": 750.0},
    ]


@pytest.fixture()
def sample_bull_analysis():
    """Return a TradingAnalysis from the bull (GPT) model."""
    from src.models import TradingAnalysis, TradeRecommendation

    return TradingAnalysis(
        market_assessment="Micro-cap sector showing momentum with biotech catalysts.",
        recommendations=[
            TradeRecommendation(
                action="BUY",
                symbol="RXRX",
                confidence=0.8,
                stop_loss_pct=0.15,
                reasoning="Strong pipeline data and institutional interest building.",
            ),
            TradeRecommendation(
                action="HOLD",
                symbol="ATOS",
                confidence=0.5,
                stop_loss_pct=0.10,
                reasoning="Awaiting FDA decision, risk/reward unclear.",
            ),
        ],
    )


@pytest.fixture()
def sample_bear_analysis():
    """Return a TradingAnalysis from the bear (Claude) model."""
    from src.models import TradingAnalysis, TradeRecommendation

    return TradingAnalysis(
        market_assessment="Market volatility elevated; micro-caps face liquidity risk.",
        recommendations=[
            TradeRecommendation(
                action="BUY",
                symbol="RXRX",
                confidence=0.7,
                stop_loss_pct=0.20,
                reasoning="Despite risks, fundamentals are solid. Conservative entry warranted.",
            ),
            TradeRecommendation(
                action="SELL",
                symbol="ATOS",
                confidence=0.6,
                stop_loss_pct=0.10,
                reasoning="Downside risk outweighs potential upside pre-FDA.",
            ),
        ],
    )
