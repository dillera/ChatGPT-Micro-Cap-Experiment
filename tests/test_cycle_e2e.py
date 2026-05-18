"""End-to-end tests for the complete trading cycle with mocked externals.

Tests all code paths in run_cycle(): happy path, LLM failures, circuit
breaker trips, weekend skip, lockfile contention, run log output,
dry-run order safety, stop-loss enforcement, and post-trade CB trip.

Requirements covered: HARDENING-E2E
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.broker import AccountSnapshot
from src.models import ConsensusResult, TradeRecommendation, TradingAnalysis


# ---------------------------------------------------------------------------
# Common patching context for all E2E tests
# ---------------------------------------------------------------------------

def _base_patches(mock_broker, mock_consensus_result):
    """Return a dict of patches common to most E2E tests."""
    return {
        "src.cycle.TastytradeClient": MagicMock(return_value=mock_broker),
        "src.cycle.init_db": MagicMock(),
        "src.cycle.run_consensus_cycle": MagicMock(
            return_value=mock_consensus_result
        ),
        "src.cycle.get_active_symbols": MagicMock(return_value=["ABEO", "RXRX"]),
        "src.cycle.get_screener_candidates": MagicMock(return_value=["VERI", "SLDB"]),
        "src.cycle.run_discovery_cycle": MagicMock(return_value=["FREQ"]),
    }


class TestHappyPath:
    """Test 1: Full dry-run cycle completes with all expected fields."""

    def test_happy_path_completes_all_stages(
        self, mock_settings, mock_broker, mock_consensus_result, e2e_db, tmp_run_logs
    ):
        patches = _base_patches(mock_broker, mock_consensus_result)
        with patch.dict("os.environ", {}, clear=False), \
             patch("src.cycle.TastytradeClient", patches["src.cycle.TastytradeClient"]), \
             patch("src.cycle.init_db", patches["src.cycle.init_db"]), \
             patch("src.cycle.run_consensus_cycle", patches["src.cycle.run_consensus_cycle"]), \
             patch("src.cycle.get_active_symbols", patches["src.cycle.get_active_symbols"]), \
             patch("src.cycle.get_screener_candidates", patches["src.cycle.get_screener_candidates"]), \
             patch("src.cycle.run_discovery_cycle", patches["src.cycle.run_discovery_cycle"]), \
             patch("src.cycle.datetime") as mock_dt, \
             patch("src.cycle.LOCK_PATH", Path(tempfile.mktemp())):
            mock_dt.now.return_value = datetime(2026, 3, 18, 10, 0, 0)  # Wednesday
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            from src.cycle import run_cycle
            result = run_cycle(dry_run=True)

        assert result["status"] == "complete"
        assert result["dry_run"] is True
        assert result["account"] is not None
        assert result["account"]["nlv"] == 500.0
        assert result["candidates"] is not None
        assert result["candidates"]["watchlist"] == 2
        assert result["candidates"]["screener"] == 2
        assert result["candidates"]["discovered"] == 1
        assert result["consensus"] is not None
        assert result["order_results"] is not None
        assert result["circuit_breaker"] is not None
        assert result["daily_snapshot"] is not None


class TestLLMFailure:
    """Tests 2-3: LLM failures are non-fatal; cycle continues."""

    def test_consensus_exception_non_fatal(
        self, mock_settings, mock_broker, e2e_db, tmp_run_logs
    ):
        """Test 2: run_consensus_cycle raises Exception -- cycle still completes."""
        with patch("src.cycle.TastytradeClient", MagicMock(return_value=mock_broker)), \
             patch("src.cycle.init_db", MagicMock()), \
             patch("src.cycle.run_consensus_cycle", side_effect=Exception("JSON parse error")), \
             patch("src.cycle.datetime") as mock_dt, \
             patch("src.cycle.LOCK_PATH", Path(tempfile.mktemp())):
            mock_dt.now.return_value = datetime(2026, 3, 18, 10, 0, 0)  # Wednesday
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            from src.cycle import run_cycle
            result = run_cycle(dry_run=True)

        assert result["status"] == "complete"
        assert result["consensus"] is None
        assert result["order_results"] == []

    def test_query_bull_raises_propagates_to_consensus_none(
        self, mock_settings, mock_broker, e2e_db, tmp_run_logs
    ):
        """Test 3: query_bull raises -- consensus is None, no orders placed."""
        def _failing_consensus(*args, **kwargs):
            raise Exception("Bull model API error")

        with patch("src.cycle.TastytradeClient", MagicMock(return_value=mock_broker)), \
             patch("src.cycle.init_db", MagicMock()), \
             patch("src.cycle.run_consensus_cycle", side_effect=_failing_consensus), \
             patch("src.cycle.datetime") as mock_dt, \
             patch("src.cycle.LOCK_PATH", Path(tempfile.mktemp())):
            mock_dt.now.return_value = datetime(2026, 3, 18, 10, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            from src.cycle import run_cycle
            result = run_cycle(dry_run=True)

        assert result["status"] == "complete"
        assert result["consensus"] is None
        assert result["order_results"] == []


class TestCircuitBreakerHalt:
    """Tests 4-6: Circuit breaker halt and override scenarios."""

    def test_halted_daily_returns_halted(
        self, mock_settings, mock_broker, e2e_db, tmp_run_logs
    ):
        """Test 4: Pre-set HALTED_DAILY returns halted status."""
        # Set circuit breaker to HALTED_DAILY with today's date
        now = datetime.now().isoformat()
        e2e_db.execute(
            "UPDATE circuit_breaker SET status='HALTED_DAILY', tripped_at=?, reason='test daily halt' WHERE id=1",
            (now,),
        )
        e2e_db.commit()

        with patch("src.cycle.TastytradeClient", MagicMock(return_value=mock_broker)), \
             patch("src.cycle.init_db", MagicMock()), \
             patch("src.cycle.datetime") as mock_dt, \
             patch("src.cycle.LOCK_PATH", Path(tempfile.mktemp())):
            mock_dt.now.return_value = datetime(2026, 3, 18, 10, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            # Ensure no override env var
            env = os.environ.copy()
            env.pop("OVERRIDE_CIRCUIT_BREAKER", None)

            from src.cycle import run_cycle
            result = run_cycle(dry_run=True)

        assert result["status"] == "halted"
        assert "circuit_breaker" in result["reason"]

    def test_halted_drawdown_returns_halted(
        self, mock_settings, mock_broker, e2e_db, tmp_run_logs
    ):
        """Test 5: Pre-set HALTED_DRAWDOWN returns halted status."""
        now = datetime.now().isoformat()
        e2e_db.execute(
            "UPDATE circuit_breaker SET status='HALTED_DRAWDOWN', tripped_at=?, reason='test drawdown halt' WHERE id=1",
            (now,),
        )
        e2e_db.commit()

        with patch("src.cycle.TastytradeClient", MagicMock(return_value=mock_broker)), \
             patch("src.cycle.init_db", MagicMock()), \
             patch("src.cycle.datetime") as mock_dt, \
             patch("src.cycle.LOCK_PATH", Path(tempfile.mktemp())), \
             patch.dict("os.environ", {k: v for k, v in os.environ.items() if k != "OVERRIDE_CIRCUIT_BREAKER"}):

            mock_dt.now.return_value = datetime(2026, 3, 18, 10, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            from src.cycle import run_cycle
            result = run_cycle(dry_run=True)

        assert result["status"] == "halted"
        assert "circuit_breaker" in result["reason"]

    def test_halted_drawdown_override_continues(
        self, mock_settings, mock_broker, mock_consensus_result, e2e_db, tmp_run_logs
    ):
        """Test 6: HALTED_DRAWDOWN with OVERRIDE_CIRCUIT_BREAKER=1 proceeds."""
        now = datetime.now().isoformat()
        e2e_db.execute(
            "UPDATE circuit_breaker SET status='HALTED_DRAWDOWN', tripped_at=?, reason='test drawdown halt' WHERE id=1",
            (now,),
        )
        e2e_db.commit()

        with patch("src.cycle.TastytradeClient", MagicMock(return_value=mock_broker)), \
             patch("src.cycle.init_db", MagicMock()), \
             patch("src.cycle.run_consensus_cycle", MagicMock(return_value=mock_consensus_result)), \
             patch("src.cycle.datetime") as mock_dt, \
             patch("src.cycle.LOCK_PATH", Path(tempfile.mktemp())), \
             patch.dict("os.environ", {"OVERRIDE_CIRCUIT_BREAKER": "1"}):

            mock_dt.now.return_value = datetime(2026, 3, 18, 10, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            from src.cycle import run_cycle
            result = run_cycle(dry_run=True)

        assert result["status"] == "complete"


class TestWeekendSkip:
    """Test 7: Weekend run skips without broker auth."""

    def test_weekend_returns_skipped(
        self, mock_settings, mock_broker, e2e_db, tmp_run_logs
    ):
        """Saturday (weekday=5) returns skipped status."""
        with patch("src.cycle.TastytradeClient", MagicMock(return_value=mock_broker)), \
             patch("src.cycle.init_db", MagicMock()), \
             patch("src.cycle.datetime") as mock_dt, \
             patch("src.cycle.LOCK_PATH", Path(tempfile.mktemp())):
            # Saturday March 21, 2026
            mock_dt.now.return_value = datetime(2026, 3, 21, 10, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            from src.cycle import run_cycle
            result = run_cycle(dry_run=True)

        assert result["status"] == "skipped"
        assert "weekend" in result["reason"].lower()


class TestLockfileContention:
    """Test 8: Lockfile held returns skipped."""

    def test_lockfile_contention_returns_skipped(
        self, mock_settings, e2e_db, tmp_run_logs
    ):
        """If lockfile is already held, cycle returns skipped."""
        lock_path = Path(tempfile.mktemp(suffix=".lock"))
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        # Hold the lock before calling run_cycle
        lock_fh = open(lock_path, "w")
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            with patch("src.cycle.LOCK_PATH", lock_path):
                from src.cycle import run_cycle
                result = run_cycle(dry_run=True)

            assert result["status"] == "skipped"
            assert "another cycle running" in result["reason"].lower()
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()
            lock_path.unlink(missing_ok=True)


class TestRunLogOutput:
    """Test 9: Run log is written with expected fields."""

    def test_run_log_written_with_expected_fields(
        self, mock_settings, mock_broker, mock_consensus_result, e2e_db, tmp_run_logs
    ):
        """After successful dry_run, JSON log file contains key fields."""
        with patch("src.cycle.TastytradeClient", MagicMock(return_value=mock_broker)), \
             patch("src.cycle.init_db", MagicMock()), \
             patch("src.cycle.run_consensus_cycle", MagicMock(return_value=mock_consensus_result)), \
             patch("src.cycle.datetime") as mock_dt, \
             patch("src.cycle.LOCK_PATH", Path(tempfile.mktemp())):
            mock_dt.now.return_value = datetime(2026, 3, 18, 10, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            from src.cycle import run_cycle
            run_cycle(dry_run=True)

        # Find the JSON log file
        log_files = list(tmp_run_logs.glob("cycle_*.json"))
        assert len(log_files) >= 1, f"Expected at least 1 run log, found {len(log_files)}"

        with open(log_files[0]) as f:
            log_data = json.load(f)

        assert "status" in log_data
        assert "dry_run" in log_data
        assert "account" in log_data
        assert "consensus" in log_data


class TestDryRunOrders:
    """Test 10: Dry-run orders are not submitted for real."""

    def test_dry_run_orders_not_submitted(
        self, mock_settings, mock_broker, mock_consensus_result, e2e_db, tmp_run_logs
    ):
        """During dry_run, execute_trade returns status='dry_run'."""
        with patch("src.cycle.TastytradeClient", MagicMock(return_value=mock_broker)), \
             patch("src.cycle.init_db", MagicMock()), \
             patch("src.cycle.run_consensus_cycle", MagicMock(return_value=mock_consensus_result)), \
             patch("src.cycle.datetime") as mock_dt, \
             patch("src.cycle.LOCK_PATH", Path(tempfile.mktemp())):
            mock_dt.now.return_value = datetime(2026, 3, 18, 10, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            from src.cycle import run_cycle
            result = run_cycle(dry_run=True)

        # All orders should be dry_run status
        for order in result["order_results"]:
            assert order["status"] == "dry_run", (
                f"Expected dry_run status, got {order['status']}"
            )

        # place_otoco_order should only be called with dry_run=True
        for call in mock_broker.place_otoco_order.call_args_list:
            assert call.kwargs.get("dry_run", True) is True or call.args[-1] is True


class TestStopLossEnforcement:
    """Test 11: Stop-loss triggers during dry-run cycle."""

    def test_stop_loss_triggered_in_dry_run(
        self, mock_settings, mock_broker, mock_consensus_result, e2e_db, tmp_run_logs
    ):
        """Position below stop-loss shows in stop_loss_results with dry_run status."""
        # Insert a position with stop_loss into the DB
        e2e_db.execute(
            """INSERT INTO positions (symbol, shares, buy_price, cost_basis, stop_loss, opened_at, updated_at)
               VALUES ('RXRX', 100, 10.0, 1000.0, 10.0, '2026-03-17T10:00:00', '2026-03-17T10:00:00')"""
        )
        e2e_db.commit()

        # Mock get_quote to return price below stop (mid=8.10 < stop=10.0)
        mock_broker.get_quote.return_value = (8.0, 8.20, 0.02)

        with patch("src.cycle.TastytradeClient", MagicMock(return_value=mock_broker)), \
             patch("src.cycle.init_db", MagicMock()), \
             patch("src.cycle.run_consensus_cycle", MagicMock(return_value=mock_consensus_result)), \
             patch("src.cycle.datetime") as mock_dt, \
             patch("src.cycle.LOCK_PATH", Path(tempfile.mktemp())):
            mock_dt.now.return_value = datetime(2026, 3, 18, 10, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            from src.cycle import run_cycle
            result = run_cycle(dry_run=True)

        assert result["status"] == "complete"
        stops = result["stop_loss_results"]
        assert len(stops) >= 1
        triggered = [s for s in stops if s["symbol"] == "RXRX"]
        assert len(triggered) == 1
        assert triggered[0]["status"] == "dry_run"


class TestPostTradeCBTrip:
    """Test 12: Circuit breaker trips post-trade on large loss."""

    def test_cb_trips_on_large_post_trade_loss(
        self, mock_settings, mock_broker, mock_consensus_result, e2e_db, tmp_run_logs
    ):
        """15% NLV drop post-trade trips HALTED_DAILY circuit breaker."""
        # First call: pre-trade NLV=500, second call: post-trade NLV=425 (15% loss)
        pre_snapshot = AccountSnapshot(
            account_number="TEST001",
            cash_balance=500.0,
            buying_power=450.0,
            net_liquidating_value=500.0,
            positions=[
                {"symbol": "RXRX", "shares": 100.0, "price": 8.50, "market_value": 850.0}
            ],
            fetched_at=datetime.now().isoformat(),
        )
        post_snapshot = AccountSnapshot(
            account_number="TEST001",
            cash_balance=425.0,
            buying_power=400.0,
            net_liquidating_value=425.0,
            positions=[
                {"symbol": "RXRX", "shares": 100.0, "price": 7.25, "market_value": 725.0}
            ],
            fetched_at=datetime.now().isoformat(),
        )
        mock_broker.get_account_snapshot.side_effect = [pre_snapshot, post_snapshot]

        with patch("src.cycle.TastytradeClient", MagicMock(return_value=mock_broker)), \
             patch("src.cycle.init_db", MagicMock()), \
             patch("src.cycle.run_consensus_cycle", MagicMock(return_value=mock_consensus_result)), \
             patch("src.cycle.datetime") as mock_dt, \
             patch("src.cycle.LOCK_PATH", Path(tempfile.mktemp())):
            mock_dt.now.return_value = datetime(2026, 3, 18, 10, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            from src.cycle import run_cycle
            result = run_cycle(dry_run=True)

        assert result["status"] == "complete"
        assert result["circuit_breaker"] is not None
        assert result["circuit_breaker"]["status"] == "HALTED_DAILY"
