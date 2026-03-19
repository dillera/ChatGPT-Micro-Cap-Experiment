"""Shared test fixtures for the micro-cap trading bot."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.broker import AccountSnapshot
from src.config import Settings
from src.db import init_db, get_db, SCHEMA_SQL
from src.models import ConsensusResult, TradeRecommendation, TradingAnalysis


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


# ---------------------------------------------------------------------------
# E2E cycle test fixtures (Phase 4 hardening)
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_broker():
    """Fully mocked TastytradeClient for E2E cycle tests."""
    broker = MagicMock()
    broker.authenticate.return_value = None
    broker.get_account_snapshot.return_value = AccountSnapshot(
        account_number="TEST001",
        cash_balance=500.0,
        buying_power=450.0,
        net_liquidating_value=500.0,
        positions=[
            {"symbol": "RXRX", "shares": 100.0, "price": 8.50, "market_value": 850.0}
        ],
        fetched_at=datetime.now().isoformat(),
    )
    broker.sync_positions_to_db.return_value = 1
    broker.get_quote.return_value = (9.90, 10.10, 0.02)
    broker.place_otoco_order.return_value = {
        "order_response": MagicMock(order=MagicMock(id=99999)),
        "ticker": "RXRX",
        "shares": 5,
        "limit_price": 10.0,
        "stop_price": 8.50,
        "dry_run": True,
    }
    return broker


@pytest.fixture()
def mock_consensus_result():
    """ConsensusResult with one approved BUY trade for E2E tests."""
    bull = TradingAnalysis(
        market_assessment="Bullish on micro-cap biotech sector.",
        recommendations=[
            TradeRecommendation(
                action="BUY",
                symbol="RXRX",
                confidence=0.80,
                stop_loss_pct=0.15,
                reasoning="Strong pipeline data supports upside.",
            ),
        ],
    )
    bear = TradingAnalysis(
        market_assessment="Cautious but RXRX fundamentals are solid.",
        recommendations=[
            TradeRecommendation(
                action="BUY",
                symbol="RXRX",
                confidence=0.80,
                stop_loss_pct=0.15,
                reasoning="Fundamentals support entry despite macro risk.",
            ),
        ],
    )
    return ConsensusResult(
        approved_trades=[
            TradeRecommendation(
                action="BUY",
                symbol="RXRX",
                confidence=0.80,
                stop_loss_pct=0.15,
                reasoning="BULL: Strong pipeline | BEAR: Fundamentals solid",
            ),
        ],
        bull_analysis=bull,
        bear_analysis=bear,
        all_symbols_seen=["RXRX"],
        disagreed_symbols=[],
    )


@pytest.fixture()
def e2e_db(monkeypatch):
    """In-memory SQLite DB shared across all modules for E2E tests.

    Patches get_db in src.db and every module that imports it so all
    production code shares the same in-memory connection during a test.
    """
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

    def _get_db(*args, **kwargs):
        return wrapper

    # Patch get_db in the canonical module and every consumer
    monkeypatch.setattr("src.db.get_db", _get_db)
    monkeypatch.setattr("src.cycle.get_db", _get_db)
    monkeypatch.setattr("src.circuit_breaker.get_db", _get_db)
    monkeypatch.setattr("src.orders.get_db", _get_db)
    monkeypatch.setattr("src.consensus.get_db", _get_db)
    monkeypatch.setattr("src.pdt.get_db", _get_db)
    monkeypatch.setattr("src.stoploss.get_db", _get_db)
    monkeypatch.setattr("src.watchlist.get_db", _get_db)
    monkeypatch.setattr("src.screener.get_db", _get_db)

    yield wrapper
    conn.close()


@pytest.fixture()
def tmp_run_logs(tmp_path, monkeypatch):
    """Temporary directory for run logs, patched into src.run_logger."""
    log_dir = tmp_path / "run_logs"
    log_dir.mkdir()
    monkeypatch.setattr("src.run_logger.RUN_LOG_DIR", log_dir)
    return log_dir
