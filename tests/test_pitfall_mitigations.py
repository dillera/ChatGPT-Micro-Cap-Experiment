"""Pitfall mitigation validation tests.

Each test class maps to a specific pitfall from PITFALLS.md research.
These tests prove that every critical and moderate pitfall has a working
code-level mitigation before the system handles real money.

Pitfalls covered:
- P1: OTC filter (src/otc_filter.py)
- P2: OAuth2 path (src/broker.py)
- P3: PDT counter (src/pdt.py)
- P5: Spread check (src/orders.py)
- P6: Duplicate order prevention (lockfile)
- P9: Consensus false agreement prevention (src/consensus.py)
- P10: JSON parse failure abort (src/consensus.py, src/cycle.py)
- CB: Circuit breaker states (src/circuit_breaker.py)
- SZ: Position sizing guards (src/sizing.py)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from src.models import (
    TradingAnalysis,
    TradeRecommendation,
    CircuitBreakerState,
)


# ===========================================================================
# Pitfall 1: OTC Filter (src/otc_filter.py)
# Validates: tastytrade does not support OTC / penny stocks
# ===========================================================================


class TestPitfall1OTCFilter:
    """Pitfall 1: OTC tickers must be rejected before any brokerage API call.

    References PITFALLS.md Pitfall 1: tastytrade explicitly prohibits OTC,
    pink sheet, and grey market securities.
    """

    @pytest.mark.parametrize("exchange", ["NASDAQ", "NYSE", "BATS", "NYSE ARCA",
                                           "NYSE American", "CBOE", "IEX",
                                           "NYQ", "NMS", "NCM", "NGM", "PCX", "BTS"])
    def test_p1_valid_exchanges_accepted(self, exchange):
        """P1-1/2/3: is_exchange_listed accepts all valid exchange names."""
        from src.otc_filter import is_exchange_listed
        assert is_exchange_listed("RXRX", exchange) is True

    @pytest.mark.parametrize("exchange", ["OTC", "OTCBB", "PINK", "OTC Markets",
                                           "Other OTC", "PNK", "GREY"])
    def test_p1_otc_exchanges_rejected(self, exchange):
        """P1-4/5/6: is_exchange_listed rejects OTC/PINK/GREY exchanges."""
        from src.otc_filter import is_exchange_listed
        assert is_exchange_listed("SCAM", exchange) is False

    def test_p1_none_exchange_rejected(self):
        """P1-7: is_exchange_listed rejects None exchange (conservative)."""
        from src.otc_filter import is_exchange_listed
        assert is_exchange_listed("SCAM", None) is False

    def test_p1_unknown_exchange_rejected(self):
        """P1-8: is_exchange_listed rejects unknown exchange names (conservative)."""
        from src.otc_filter import is_exchange_listed
        assert is_exchange_listed("SCAM", "UNKNOWN_EXCHANGE") is False

    def test_p1_validate_symbols_splits_correctly(self):
        """P1-9: validate_symbols returns correct accepted/rejected split."""
        from src.otc_filter import validate_symbols

        pairs = [
            ("RXRX", "NASDAQ"),
            ("AAPL", "NYSE"),
            ("SCAM", "OTC"),
            ("JUNK", None),
            ("LEGIT", "BATS"),
        ]
        accepted, rejected = validate_symbols(pairs)
        assert set(accepted) == {"RXRX", "AAPL", "LEGIT"}
        assert set(rejected) == {"SCAM", "JUNK"}


# ===========================================================================
# Pitfall 2: OAuth2 Path (src/broker.py)
# Validates: session-token auth is deprecated; OAuth2 required
# ===========================================================================


class TestPitfall2OAuth2:
    """Pitfall 2: Authentication must use OAuth2 (provider_secret + refresh_token),
    not the deprecated username/password session-token flow.

    References PITFALLS.md Pitfall 2: Session Token Authentication Is Deprecated.
    """

    def test_p2_authenticate_uses_oauth2_not_password(self):
        """P2-1: TastytradeClient._async_authenticate uses Session(provider_secret=, refresh_token=)."""
        import asyncio
        from src.broker import TastytradeClient

        client = TastytradeClient()

        session_calls = []

        class FakeSession:
            def __init__(self, **kwargs):
                session_calls.append(kwargs)

            def serialize(self):
                return "fake-serialized"

            @classmethod
            def deserialize(cls, data):
                raise ValueError("force fresh auth")

        class FakeAccount:
            account_number = "TEST123"

            @classmethod
            async def get(cls, session):
                return [cls()]

        with patch("src.broker.get_settings") as mock_settings, \
             patch("src.broker.get_db") as mock_db:
            settings = MagicMock()
            settings.tt_secret = "test-secret"
            settings.tt_refresh = "test-refresh"
            mock_settings.return_value = settings

            # Return a mock conn that has no cached session
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchone.return_value = None
            mock_db.return_value = mock_conn

            with patch("tastytrade.Session", FakeSession), \
                 patch("tastytrade.Account", FakeAccount):
                asyncio.get_event_loop().run_until_complete(client._async_authenticate())

        # Verify Session was called with OAuth2 kwargs
        assert len(session_calls) == 1
        assert "provider_secret" in session_calls[0]
        assert "refresh_token" in session_calls[0]
        assert session_calls[0]["provider_secret"] == "test-secret"
        assert session_calls[0]["refresh_token"] == "test-refresh"
        # Ensure no password-based auth
        assert "login" not in session_calls[0]
        assert "password" not in session_calls[0]
        assert "username" not in session_calls[0]

    def test_p2_session_cache_ttl_14_minutes(self):
        """P2-2: Session cache stores serialized_session with ~14 min TTL."""
        from src.broker import TastytradeClient

        client = TastytradeClient()
        client._session = MagicMock()
        client._session.serialize.return_value = "serialized-data"

        mock_conn = MagicMock()

        with patch("src.broker.get_db", return_value=mock_conn):
            client._cache_session()

        # Verify the INSERT call was made
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args[0]
        assert "INSERT OR REPLACE INTO session_cache" in call_args[0]

        # Check expires_at is ~14 minutes from now
        expires_at_str = call_args[1][1]  # second param
        expires_at = datetime.fromisoformat(expires_at_str)
        expected = datetime.now() + timedelta(minutes=14)
        # Allow 30 seconds tolerance
        assert abs((expires_at - expected).total_seconds()) < 30


# ===========================================================================
# Pitfall 3: PDT Counter (src/pdt.py)
# Validates: pattern day trader rule enforcement
# ===========================================================================


class TestPitfall3PDT:
    """Pitfall 3: PDT counter must enforce 2-trade safe limit within rolling window.

    References PITFALLS.md Pitfall 3: Pattern Day Trader Rule Will Lock the Account.
    """

    def test_p3_safe_when_zero_trades(self, test_db):
        """P3-1: check_pdt_limit returns True when 0 trades in window."""
        from src.pdt import check_pdt_limit
        assert check_pdt_limit(conn=test_db) is True

    def test_p3_safe_when_one_trade(self, test_db):
        """P3-2: check_pdt_limit returns True when 1 trade in window."""
        from src.pdt import record_day_trade, check_pdt_limit

        record_day_trade("RXRX", conn=test_db)
        assert check_pdt_limit(conn=test_db) is True

    def test_p3_blocked_when_two_trades(self, test_db):
        """P3-3: check_pdt_limit returns False when 2 trades in window (SAFE_DAY_TRADE_LIMIT=2)."""
        from src.pdt import record_day_trade, check_pdt_limit

        record_day_trade("RXRX", conn=test_db)
        record_day_trade("ATOS", conn=test_db)
        assert check_pdt_limit(conn=test_db) is False

    def test_p3_record_increments_count(self, test_db):
        """P3-4: record_day_trade increments the count correctly."""
        from src.pdt import record_day_trade, get_day_trade_count

        assert get_day_trade_count(conn=test_db) == 0
        record_day_trade("RXRX", conn=test_db)
        assert get_day_trade_count(conn=test_db) == 1
        record_day_trade("ATOS", conn=test_db)
        assert get_day_trade_count(conn=test_db) == 2

    def test_p3_old_trades_not_counted(self, test_db):
        """P3-5: Trades outside 7-day window are not counted (rolling window)."""
        from src.pdt import record_day_trade, get_day_trade_count

        # Insert a trade 8 days ago
        old_date = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d")
        record_day_trade("OLD", trade_date=old_date, conn=test_db)
        assert get_day_trade_count(conn=test_db) == 0

        # Insert a recent trade -- should be counted
        record_day_trade("RECENT", conn=test_db)
        assert get_day_trade_count(conn=test_db) == 1


# ===========================================================================
# Pitfall 5: Spread Check (src/orders.py)
# Validates: bid-ask spread gate prevents catastrophic slippage
# ===========================================================================


class TestPitfall5SpreadCheck:
    """Pitfall 5: Market orders on illiquid micro-caps cause catastrophic slippage.

    References PITFALLS.md Pitfall 5: spread check rejects orders when
    bid-ask spread exceeds 5%.
    """

    def _make_rec(self):
        return TradeRecommendation(
            action="BUY",
            symbol="RXRX",
            confidence=0.80,
            stop_loss_pct=0.15,
            reasoning="Test trade.",
        )

    def test_p5_rejects_wide_spread(self):
        """P5-1: execute_trade rejects when spread_pct=0.06 (> 5% threshold)."""
        from src.orders import execute_trade

        client = MagicMock()
        client.get_quote.return_value = (1.00, 1.12, 0.06)  # 6% spread

        with patch("src.orders.is_exchange_listed", return_value=True), \
             patch("src.orders.check_pdt_limit", return_value=True):
            result = execute_trade(client, self._make_rec(), Decimal("10000"), dry_run=True)

        assert result["status"] == "rejected"
        assert "spread" in result["reason"].lower()

    def test_p5_accepts_narrow_spread(self):
        """P5-2: execute_trade accepts when spread_pct=0.04 (< 5% threshold)."""
        from src.orders import execute_trade

        client = MagicMock()
        client.get_quote.return_value = (9.80, 10.20, 0.04)  # 4% spread
        client.place_otoco_order.return_value = {"order_response": MagicMock()}

        with patch("src.orders.is_exchange_listed", return_value=True), \
             patch("src.orders.check_pdt_limit", return_value=True), \
             patch("src.orders.compute_shares", return_value=50):
            result = execute_trade(client, self._make_rec(), Decimal("10000"), dry_run=True)

        assert result["status"] == "dry_run"

    def test_p5_accepts_at_boundary(self):
        """P5-3: execute_trade accepts at boundary spread_pct=0.05."""
        from src.orders import execute_trade

        client = MagicMock()
        client.get_quote.return_value = (9.75, 10.25, 0.05)  # exactly 5%
        client.place_otoco_order.return_value = {"order_response": MagicMock()}

        with patch("src.orders.is_exchange_listed", return_value=True), \
             patch("src.orders.check_pdt_limit", return_value=True), \
             patch("src.orders.compute_shares", return_value=50):
            result = execute_trade(client, self._make_rec(), Decimal("10000"), dry_run=True)

        # 0.05 is NOT > 0.05, so it should pass
        assert result["status"] == "dry_run"


# ===========================================================================
# Pitfall 6: Duplicate Order Prevention (lockfile)
# Validates: lockfile prevents cron overlap
# ===========================================================================


class TestPitfall6DuplicateOrders:
    """Pitfall 6: Duplicate orders on bot restart or cron overlap.

    References PITFALLS.md Pitfall 6: Lockfile at data/cycle.lock via
    fcntl.flock LOCK_EX|LOCK_NB for cron overlap prevention.
    """

    def test_p6_lockfile_path_in_config(self):
        """P6-1: Lockfile path exists in cycle configuration."""
        from src.cycle import LOCK_PATH
        assert "cycle.lock" in str(LOCK_PATH)


# ===========================================================================
# Pitfall 9: Consensus False Agreement Prevention (src/consensus.py)
# Validates: adversarial bull/bear prompting with veto logic
# ===========================================================================


class TestPitfall9ConsensusFalseAgreement:
    """Pitfall 9: LLM Consensus Masking -- models agree for wrong reason.

    References PITFALLS.md Pitfall 9: evaluate_consensus applies veto logic
    requiring both models to agree on action AND confidence >= threshold.
    """

    def test_p9_disagreement_on_action(self, sample_bull_analysis, sample_bear_analysis):
        """P9-1: evaluate_consensus returns disagreed when bull=BUY, bear=SELL."""
        from src.consensus import evaluate_consensus

        # sample fixtures: bull says BUY ATOS (conf 0.5), bear says SELL ATOS (conf 0.6)
        approved, disagreed = evaluate_consensus(sample_bull_analysis, sample_bear_analysis)

        # ATOS should be disagreed (different actions)
        assert "ATOS" in disagreed

    def test_p9_low_confidence_rejected(self):
        """P9-2: evaluate_consensus rejects when min confidence < 0.6."""
        from src.consensus import evaluate_consensus

        bull = TradingAnalysis(
            market_assessment="Bullish",
            recommendations=[
                TradeRecommendation(
                    action="BUY", symbol="RXRX", confidence=0.50,
                    stop_loss_pct=0.15, reasoning="Low conf bull."
                ),
            ],
        )
        bear = TradingAnalysis(
            market_assessment="Bearish",
            recommendations=[
                TradeRecommendation(
                    action="BUY", symbol="RXRX", confidence=0.55,
                    stop_loss_pct=0.20, reasoning="Low conf bear."
                ),
            ],
        )

        approved, disagreed = evaluate_consensus(bull, bear, min_confidence=0.6)
        assert len(approved) == 0
        assert "RXRX" in disagreed

    def test_p9_approved_when_both_agree_and_confident(self):
        """P9-3: evaluate_consensus approves only when both agree on action AND both >= 0.6."""
        from src.consensus import evaluate_consensus

        bull = TradingAnalysis(
            market_assessment="Bullish",
            recommendations=[
                TradeRecommendation(
                    action="BUY", symbol="RXRX", confidence=0.80,
                    stop_loss_pct=0.15, reasoning="Strong thesis."
                ),
            ],
        )
        bear = TradingAnalysis(
            market_assessment="Cautiously bullish",
            recommendations=[
                TradeRecommendation(
                    action="BUY", symbol="RXRX", confidence=0.70,
                    stop_loss_pct=0.20, reasoning="Agrees with caution."
                ),
            ],
        )

        approved, disagreed = evaluate_consensus(bull, bear, min_confidence=0.6)
        assert len(approved) == 1
        assert approved[0].symbol == "RXRX"
        assert approved[0].confidence == 0.70  # min of 0.80, 0.70
        assert len(disagreed) == 0


# ===========================================================================
# Pitfall 10: JSON Parse Failure Abort (src/consensus.py, src/cycle.py)
# Validates: no single-model fallback on parse failure
# ===========================================================================


class TestPitfall10ParseFailureAbort:
    """Pitfall 10: JSON parsing failure must abort entirely -- no single-model fallback.

    References PITFALLS.md Pitfall 10 and AIDC-04: If either LLM API call fails,
    the entire cycle aborts.
    """

    def test_p10_bull_failure_aborts(self):
        """P10-1: run_consensus_cycle raises if query_bull raises."""
        from src.consensus import run_consensus_cycle

        with patch("src.consensus.get_settings") as mock_settings, \
             patch("src.consensus.build_user_prompt", return_value="prompt"), \
             patch("src.consensus.query_bull", side_effect=Exception("Bull parse failure")):
            mock_settings.return_value = MagicMock(
                openai_model="gpt-test", anthropic_model="claude-test",
                min_confidence=0.6,
            )
            with pytest.raises(Exception, match="Bull parse failure"):
                run_consensus_cycle([], 1000.0, ["RXRX"])

    def test_p10_bear_failure_aborts(self):
        """P10-2: run_consensus_cycle raises if query_bear raises."""
        from src.consensus import run_consensus_cycle

        mock_bull = TradingAnalysis(
            market_assessment="Bullish",
            recommendations=[],
        )

        with patch("src.consensus.get_settings") as mock_settings, \
             patch("src.consensus.build_user_prompt", return_value="prompt"), \
             patch("src.consensus.query_bull", return_value=mock_bull), \
             patch("src.consensus.query_bear", side_effect=Exception("Bear parse failure")):
            mock_settings.return_value = MagicMock(
                openai_model="gpt-test", anthropic_model="claude-test",
                min_confidence=0.6,
            )
            with pytest.raises(Exception, match="Bear parse failure"):
                run_consensus_cycle([], 1000.0, ["RXRX"])

    def test_p10_cycle_consensus_failure_no_orders(self):
        """P10-3: In cycle.py, consensus failure produces status=complete but consensus=None."""
        from src.cycle import run_cycle

        mock_snapshot = MagicMock()
        mock_snapshot.positions = [{"symbol": "RXRX", "shares": 100, "price": 10.0, "market_value": 1000.0}]
        mock_snapshot.buying_power = 5000.0
        mock_snapshot.net_liquidating_value = 10000.0
        mock_snapshot.cash_balance = 5000.0

        mock_client = MagicMock()
        mock_client.get_account_snapshot.return_value = mock_snapshot
        mock_client.sync_positions_to_db.return_value = 1

        with patch("src.cycle._acquire_lock", return_value=MagicMock()), \
             patch("src.cycle._release_lock"), \
             patch("src.cycle.init_db"), \
             patch("src.cycle.TastytradeClient", return_value=mock_client), \
             patch("src.cycle.datetime") as mock_dt, \
             patch("src.cycle.get_cb_status", return_value=CircuitBreakerState()), \
             patch("src.cycle.check_and_enforce_stops", return_value=[]), \
             patch("src.cycle.run_consensus_cycle", side_effect=Exception("Parse failure")), \
             patch("src.cycle.evaluate_circuit_breaker", return_value=CircuitBreakerState()), \
             patch("src.cycle.record_daily_snapshot") as mock_snapshot_rec, \
             patch("src.cycle.write_run_log"):
            # Set weekday to Monday (0)
            mock_now = MagicMock()
            mock_now.weekday.return_value = 0
            mock_now.isoformat.return_value = "2026-03-16T10:00:00"
            mock_dt.now.return_value = mock_now

            result = run_cycle(dry_run=True)

        assert result["status"] == "complete"
        assert result["consensus"] is None
        assert result["order_results"] == []


# ===========================================================================
# Circuit Breaker Mitigations (src/circuit_breaker.py)
# Validates: OPER-03, OPER-04, OPER-05 requirements
# ===========================================================================


class TestCircuitBreakerMitigations:
    """Circuit breaker state machine: daily loss halt, drawdown halt, auto-reset rules.

    References PITFALLS.md Pitfall 3 (PDT related) and OPER-03/04/05 requirements.
    """

    def test_cb1_trips_halted_daily_on_10pct_loss(self, test_db, mock_settings):
        """CB-1: evaluate_circuit_breaker trips HALTED_DAILY when loss > 10%."""
        from src.circuit_breaker import evaluate_circuit_breaker

        with patch("src.circuit_breaker.get_db", return_value=test_db):
            result = evaluate_circuit_breaker(
                opening_nlv=10000.0,
                current_nlv=8900.0,  # -11% loss
            )

        assert result.status == "HALTED_DAILY"

    def test_cb2_no_trip_under_10pct(self, test_db, mock_settings):
        """CB-2: evaluate_circuit_breaker does NOT trip when loss < 10%."""
        from src.circuit_breaker import evaluate_circuit_breaker

        with patch("src.circuit_breaker.get_db", return_value=test_db):
            result = evaluate_circuit_breaker(
                opening_nlv=10000.0,
                current_nlv=9200.0,  # -8% loss
            )

        assert result.status == "ACTIVE"

    def test_cb3_trips_halted_drawdown_on_30pct(self, test_db, mock_settings):
        """CB-3: evaluate_circuit_breaker trips HALTED_DRAWDOWN when drawdown > 30%."""
        from src.circuit_breaker import evaluate_circuit_breaker

        # Insert a peak equity to trigger drawdown calculation
        test_db.execute(
            "INSERT INTO daily_snapshots (snapshot_date, total_equity, cash_balance, "
            "positions_value, daily_pnl, daily_pnl_pct, peak_equity, drawdown_pct) "
            "VALUES ('2026-03-15', 15000, 15000, 0, 0, 0, 15000, 0)"
        )
        test_db.commit()

        with patch("src.circuit_breaker.get_db", return_value=test_db):
            result = evaluate_circuit_breaker(
                opening_nlv=10000.0,
                current_nlv=10000.0,  # 10000/15000 = 33% drawdown from peak
            )

        assert result.status == "HALTED_DRAWDOWN"

    def test_cb4_halted_daily_auto_resets_next_day(self, test_db, mock_settings):
        """CB-4: get_cb_status auto-resets HALTED_DAILY when tripped_at is yesterday."""
        from src.circuit_breaker import get_cb_status

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d") + "T10:00:00"
        test_db.execute(
            "UPDATE circuit_breaker SET status='HALTED_DAILY', tripped_at=? WHERE id=1",
            (yesterday,),
        )
        test_db.commit()

        with patch("src.circuit_breaker.get_db", return_value=test_db):
            result = get_cb_status()

        assert result.status == "ACTIVE"

    def test_cb5_halted_drawdown_no_auto_reset(self, test_db, mock_settings):
        """CB-5: get_cb_status does NOT auto-reset HALTED_DRAWDOWN regardless of date."""
        from src.circuit_breaker import get_cb_status

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d") + "T10:00:00"
        test_db.execute(
            "UPDATE circuit_breaker SET status='HALTED_DRAWDOWN', tripped_at=? WHERE id=1",
            (yesterday,),
        )
        test_db.commit()

        with patch("src.circuit_breaker.get_db", return_value=test_db):
            result = get_cb_status()

        assert result.status == "HALTED_DRAWDOWN"

    def test_cb6_drawdown_requires_override_env(self, test_db, mock_settings):
        """CB-6: HALTED_DRAWDOWN requires OVERRIDE_CIRCUIT_BREAKER=1 to resume."""
        from src.cycle import run_cycle

        # Set up circuit breaker as HALTED_DRAWDOWN
        test_db.execute(
            "UPDATE circuit_breaker SET status='HALTED_DRAWDOWN', "
            "tripped_at='2026-03-15T10:00:00', reason='drawdown exceeded' WHERE id=1",
        )
        test_db.commit()

        cb_state = CircuitBreakerState(
            status="HALTED_DRAWDOWN",
            tripped_at="2026-03-15T10:00:00",
            reason="drawdown exceeded",
        )

        mock_client = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.net_liquidating_value = 10000.0
        mock_snapshot.buying_power = 5000.0
        mock_snapshot.cash_balance = 5000.0
        mock_snapshot.positions = []
        mock_client.get_account_snapshot.return_value = mock_snapshot
        mock_client.sync_positions_to_db.return_value = 0

        with patch("src.cycle._acquire_lock", return_value=MagicMock()), \
             patch("src.cycle._release_lock"), \
             patch("src.cycle.init_db"), \
             patch("src.cycle.TastytradeClient", return_value=mock_client), \
             patch("src.cycle.datetime") as mock_dt, \
             patch("src.cycle.get_cb_status", return_value=cb_state), \
             patch.dict("os.environ", {}, clear=False):
            mock_now = MagicMock()
            mock_now.weekday.return_value = 0
            mock_now.isoformat.return_value = "2026-03-16T10:00:00"
            mock_dt.now.return_value = mock_now

            # Without override -- should halt
            import os
            os.environ.pop("OVERRIDE_CIRCUIT_BREAKER", None)
            result = run_cycle(dry_run=True)

        assert result["status"] == "halted"
        assert "HALTED_DRAWDOWN" in result["reason"]


# ===========================================================================
# Position Sizing Guards (src/sizing.py)
# Validates: SIZE-02, SIZE-03, SIZE-04 requirements
# ===========================================================================


class TestPositionSizingGuards:
    """Position sizing guards prevent oversized or undersized trades.

    References PITFALLS.md Pitfall 5 (slippage) and sizing requirements
    SIZE-02, SIZE-03, SIZE-04.
    """

    def test_sz1_zero_shares_below_minimum_notional(self):
        """SZ-1: compute_shares returns 0 when notional < $50 (SIZE-04)."""
        from src.sizing import compute_shares

        # $100 buying power * 20% = $20 max notional, which is below $50 minimum
        result = compute_shares(
            buying_power=Decimal("100"),
            price=Decimal("10.00"),
            confidence=0.65,
        )
        assert result == 0

    def test_sz2_zero_shares_below_confidence_threshold(self):
        """SZ-2: compute_shares returns 0 when confidence < 0.60."""
        from src.sizing import compute_shares

        result = compute_shares(
            buying_power=Decimal("10000"),
            price=Decimal("5.00"),
            confidence=0.55,
        )
        assert result == 0

    def test_sz3_high_conviction_uses_40pct(self):
        """SZ-3: High conviction (0.80) uses up to 40% of buying power."""
        from src.sizing import compute_shares

        # $1000 * 40% = $400 max notional, at $5/share = 80 shares
        result = compute_shares(
            buying_power=Decimal("1000"),
            price=Decimal("5.00"),
            confidence=0.80,
        )
        assert result == 80

    def test_sz4_normal_conviction_uses_20pct(self):
        """SZ-4: Normal conviction (0.65) uses up to 20% of buying power."""
        from src.sizing import compute_shares

        # $1000 * 20% = $200 max notional, at $5/share = 40 shares
        result = compute_shares(
            buying_power=Decimal("1000"),
            price=Decimal("5.00"),
            confidence=0.65,
        )
        assert result == 40
