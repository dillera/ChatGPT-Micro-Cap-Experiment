"""Tests for order execution layer (BROK-04 and BROK-05).

Covers:
- BROK-04: Limit orders with dry_run validation before submission
- BROK-05: Companion GTC stop orders on every new buy (OTOCO)

Safety gates tested:
1. OTC filter validation
2. PDT limit check
3. Bid-ask spread > 5% rejection
4. Position sizing (0 shares = reject)
5. Dry-run pre-flight validation
6. Real submission + trade recording
7. Price sign convention (negative debit for buys)
8. Time-in-force correctness (DAY trigger, GTC stop)
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch, MagicMock, call

import pytest

from src.models import TradeRecommendation


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_client():
    """Return a mock TastytradeClient with get_quote and place_otoco_order."""
    client = MagicMock()
    # Default: healthy spread, bid=9.50, ask=10.00, spread ~5.1% (at boundary)
    # For most tests we want a narrow spread
    client.get_quote.return_value = (9.90, 10.10, 0.02)  # 2% spread
    client.place_otoco_order.return_value = {
        "order_response": MagicMock(order=MagicMock(id=12345)),
        "ticker": "RXRX",
        "shares": 100,
        "limit_price": 10.0,
        "stop_price": 8.50,
        "dry_run": True,
    }
    return client


@pytest.fixture()
def buy_rec():
    """Return a TradeRecommendation for a BUY."""
    return TradeRecommendation(
        action="BUY",
        symbol="RXRX",
        confidence=0.80,
        stop_loss_pct=0.15,
        reasoning="Strong pipeline data.",
    )


# ---------------------------------------------------------------------------
# Test 1: get_quote returns (bid, ask, spread_pct) tuple
# ---------------------------------------------------------------------------


class TestGetQuote:
    def test_get_quote_returns_bid_ask_spread(self):
        """get_quote returns (bid, ask, spread_pct) tuple for a ticker."""
        from src.broker import TastytradeClient

        client = TastytradeClient()
        client._session = MagicMock()

        expected_bid, expected_ask = 9.50, 10.50
        expected_mid = (expected_bid + expected_ask) / 2
        expected_spread = (expected_ask - expected_bid) / expected_mid

        async def fake_async_get_quote(ticker):
            return (expected_bid, expected_ask, expected_spread)

        client._async_get_quote = fake_async_get_quote

        bid, ask, spread = client.get_quote("RXRX")

        assert bid == expected_bid
        assert ask == expected_ask
        assert abs(spread - expected_spread) < 0.001


# ---------------------------------------------------------------------------
# Test 2: place_otoco_order constructs NewComplexOrder correctly
# ---------------------------------------------------------------------------


class TestPlaceOtocoOrder:
    def test_place_otoco_constructs_complex_order(self):
        """place_otoco_order constructs OTOCO with negative limit price (debit)."""
        from src.broker import TastytradeClient

        client = TastytradeClient()
        client._session = MagicMock()
        client._account = MagicMock()

        # Capture the complex order passed to place_complex_order
        captured_orders = []

        mock_symbol = MagicMock()
        # build_leg must return a real Leg -- use a spec-compliant mock
        from tastytrade.order import Leg, InstrumentType, OrderAction
        real_leg = Leg(
            instrument_type=InstrumentType.EQUITY,
            symbol="RXRX",
            action=OrderAction.BUY_TO_OPEN,
            quantity=Decimal("100"),
        )
        mock_symbol.build_leg.return_value = real_leg

        async def fake_get(session, ticker):
            return mock_symbol

        async def fake_place(session, order, dry_run=True):
            captured_orders.append(order)
            return MagicMock()

        with patch("tastytrade.instruments.Equity") as MockEquity:
            MockEquity.get = fake_get
            client._account.place_complex_order = fake_place

            result = client.place_otoco_order(
                "RXRX", 100, Decimal("10.00"), Decimal("8.50"), dry_run=True
            )

        assert len(captured_orders) == 1
        otoco = captured_orders[0]
        # Trigger order has negative price (debit for buy)
        assert otoco.trigger_order.price == Decimal("-10.00")

    def test_place_otoco_dry_run_true(self):
        """place_otoco_order with dry_run=True passes dry_run=True to place_complex_order."""
        from src.broker import TastytradeClient
        from tastytrade.order import Leg, InstrumentType, OrderAction

        client = TastytradeClient()
        client._session = MagicMock()
        client._account = MagicMock()

        real_leg = Leg(
            instrument_type=InstrumentType.EQUITY,
            symbol="RXRX",
            action=OrderAction.BUY_TO_OPEN,
            quantity=Decimal("100"),
        )
        mock_symbol = MagicMock()
        mock_symbol.build_leg.return_value = real_leg

        calls_made = []

        async def fake_get(session, ticker):
            return mock_symbol

        async def fake_place(session, order, dry_run=True):
            calls_made.append(dry_run)
            return MagicMock()

        with patch("tastytrade.instruments.Equity") as MockEquity:
            MockEquity.get = fake_get
            client._account.place_complex_order = fake_place

            client.place_otoco_order(
                "RXRX", 100, Decimal("10.00"), Decimal("8.50"), dry_run=True
            )

        assert calls_made == [True]

    def test_otoco_time_in_force(self):
        """OTOCO trigger order uses DAY, dependent stop uses GTC."""
        from src.broker import TastytradeClient
        from tastytrade.order import Leg, InstrumentType, OrderAction, OrderTimeInForce

        client = TastytradeClient()
        client._session = MagicMock()
        client._account = MagicMock()

        real_leg = Leg(
            instrument_type=InstrumentType.EQUITY,
            symbol="RXRX",
            action=OrderAction.BUY_TO_OPEN,
            quantity=Decimal("100"),
        )
        mock_symbol = MagicMock()
        mock_symbol.build_leg.return_value = real_leg

        captured_orders = []

        async def fake_get(session, ticker):
            return mock_symbol

        async def fake_place(session, order, dry_run=True):
            captured_orders.append(order)
            return MagicMock()

        with patch("tastytrade.instruments.Equity") as MockEquity:
            MockEquity.get = fake_get
            client._account.place_complex_order = fake_place

            client.place_otoco_order(
                "RXRX", 100, Decimal("10.00"), Decimal("8.50"), dry_run=True
            )

        otoco = captured_orders[0]
        assert otoco.trigger_order.time_in_force == OrderTimeInForce.DAY
        assert otoco.orders[0].time_in_force == OrderTimeInForce.GTC


# ---------------------------------------------------------------------------
# Test 3: execute_trade rejects ticker with spread > 5%
# ---------------------------------------------------------------------------


class TestSpreadGate:
    def test_rejects_wide_spread(self, mock_client, buy_rec):
        """execute_trade rejects when spread > 5%."""
        from src.orders import execute_trade

        mock_client.get_quote.return_value = (1.00, 1.20, 0.18)  # 18% spread

        with patch("src.orders.is_exchange_listed", return_value=True), \
             patch("src.orders.check_pdt_limit", return_value=True):
            result = execute_trade(mock_client, buy_rec, Decimal("10000"), dry_run=True)

        assert result["status"] == "rejected"
        assert "spread" in result["reason"].lower()


# ---------------------------------------------------------------------------
# Test 4: execute_trade rejects OTC tickers
# ---------------------------------------------------------------------------


class TestOTCGate:
    def test_rejects_otc_ticker(self, mock_client, buy_rec):
        """execute_trade rejects ticker that fails OTC filter validation."""
        from src.orders import execute_trade

        with patch("src.orders.is_exchange_listed", return_value=False):
            result = execute_trade(mock_client, buy_rec, Decimal("10000"), dry_run=True)

        assert result["status"] == "rejected"
        assert "otc" in result["reason"].lower() or "invalid" in result["reason"].lower()


# ---------------------------------------------------------------------------
# Test 5: execute_trade rejects when PDT limit reached
# ---------------------------------------------------------------------------


class TestPDTGate:
    def test_rejects_pdt_limit(self, mock_client, buy_rec):
        """execute_trade rejects when PDT limit is reached."""
        from src.orders import execute_trade

        with patch("src.orders.is_exchange_listed", return_value=True), \
             patch("src.orders.check_pdt_limit", return_value=False):
            result = execute_trade(mock_client, buy_rec, Decimal("10000"), dry_run=True)

        assert result["status"] == "rejected"
        assert "pdt" in result["reason"].lower()


# ---------------------------------------------------------------------------
# Test 6: execute_trade rejects when compute_shares returns 0
# ---------------------------------------------------------------------------


class TestSizingGate:
    def test_rejects_zero_shares(self, mock_client, buy_rec):
        """execute_trade rejects when compute_shares returns 0."""
        from src.orders import execute_trade

        with patch("src.orders.is_exchange_listed", return_value=True), \
             patch("src.orders.check_pdt_limit", return_value=True), \
             patch("src.orders.compute_shares", return_value=0):
            result = execute_trade(mock_client, buy_rec, Decimal("10000"), dry_run=True)

        assert result["status"] == "rejected"
        assert "size" in result["reason"].lower() or "insufficient" in result["reason"].lower()


# ---------------------------------------------------------------------------
# Test 7: execute_trade dry_run=True validates but does not submit real order
# ---------------------------------------------------------------------------


class TestDryRunGate:
    def test_dry_run_validates_only(self, mock_client, buy_rec):
        """execute_trade with dry_run=True calls place_otoco_order(dry_run=True) only."""
        from src.orders import execute_trade

        with patch("src.orders.is_exchange_listed", return_value=True), \
             patch("src.orders.check_pdt_limit", return_value=True), \
             patch("src.orders.compute_shares", return_value=100):
            result = execute_trade(mock_client, buy_rec, Decimal("10000"), dry_run=True)

        assert result["status"] == "dry_run"
        # Should only be called once with dry_run=True
        mock_client.place_otoco_order.assert_called_once()
        _, kwargs = mock_client.place_otoco_order.call_args
        assert kwargs.get("dry_run", True) is True


# ---------------------------------------------------------------------------
# Test 8: execute_trade dry_run=False calls dry_run=True THEN dry_run=False
# ---------------------------------------------------------------------------


class TestRealSubmission:
    def test_real_order_preflight_then_submit(self, mock_client, buy_rec, test_db):
        """execute_trade with dry_run=False calls dry_run=True then dry_run=False."""
        from src.orders import execute_trade

        with patch("src.orders.is_exchange_listed", return_value=True), \
             patch("src.orders.check_pdt_limit", return_value=True), \
             patch("src.orders.compute_shares", return_value=100), \
             patch("src.orders.get_db", return_value=test_db), \
             patch("src.orders.record_day_trade"):
            result = execute_trade(mock_client, buy_rec, Decimal("10000"), dry_run=False)

        assert result["status"] == "executed"
        assert mock_client.place_otoco_order.call_count == 2
        # First call: dry_run=True (preflight)
        first_call_kwargs = mock_client.place_otoco_order.call_args_list[0][1]
        assert first_call_kwargs["dry_run"] is True
        # Second call: dry_run=False (real)
        second_call_kwargs = mock_client.place_otoco_order.call_args_list[1][1]
        assert second_call_kwargs["dry_run"] is False


# ---------------------------------------------------------------------------
# Test 9: execute_trade records trade to SQLite
# ---------------------------------------------------------------------------


class TestTradeRecording:
    def test_records_trade_to_db(self, mock_client, buy_rec, test_db):
        """execute_trade records trade to trades table after successful order."""
        from src.orders import execute_trade

        with patch("src.orders.is_exchange_listed", return_value=True), \
             patch("src.orders.check_pdt_limit", return_value=True), \
             patch("src.orders.compute_shares", return_value=100), \
             patch("src.orders.get_db", return_value=test_db), \
             patch("src.orders.record_day_trade"):
            result = execute_trade(mock_client, buy_rec, Decimal("10000"), dry_run=False)

        rows = test_db.execute("SELECT * FROM trades").fetchall()
        assert len(rows) == 1
        trade = rows[0]
        assert trade["symbol"] == "RXRX"
        assert trade["action"] == "BUY"
        assert trade["shares"] == 100
        assert trade["source"] == "llm_consensus"


# ---------------------------------------------------------------------------
# Test 10: execute_trade uses negative price for buy debit
# ---------------------------------------------------------------------------


class TestPriceSignConvention:
    def test_negative_debit_for_buy(self, mock_client, buy_rec):
        """execute_trade uses negative price for buy debit (tastytrade convention)."""
        from src.orders import execute_trade

        with patch("src.orders.is_exchange_listed", return_value=True), \
             patch("src.orders.check_pdt_limit", return_value=True), \
             patch("src.orders.compute_shares", return_value=100):
            result = execute_trade(mock_client, buy_rec, Decimal("10000"), dry_run=True)

        call_kwargs = mock_client.place_otoco_order.call_args[1]
        # limit_price passed to broker should be positive; broker handles negation
        assert call_kwargs["limit_price"] > Decimal("0")


# ---------------------------------------------------------------------------
# Test 11: OTOCO trigger uses DAY, stop uses GTC (tested in broker tests above)
# (Covered in TestPlaceOtocoOrder.test_otoco_time_in_force)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 12: execute_trade returns correct result structure
# ---------------------------------------------------------------------------


class TestResultStructure:
    def test_successful_dry_run_returns_all_fields(self, mock_client, buy_rec):
        """execute_trade dry_run returns ticker, shares, limit_price, stop_price, spread_pct."""
        from src.orders import execute_trade

        with patch("src.orders.is_exchange_listed", return_value=True), \
             patch("src.orders.check_pdt_limit", return_value=True), \
             patch("src.orders.compute_shares", return_value=100):
            result = execute_trade(mock_client, buy_rec, Decimal("10000"), dry_run=True)

        assert result["status"] == "dry_run"
        assert result["ticker"] == "RXRX"
        assert result["shares"] == 100
        assert "limit_price" in result
        assert "stop_price" in result
        assert "spread_pct" in result
