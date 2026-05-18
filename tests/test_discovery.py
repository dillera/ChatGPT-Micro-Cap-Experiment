"""Tests for LLM discovery prompt and OTC validation of proposals.

Covers run_discovery_cycle() in src/consensus.py:
- NYSE ticker accepted
- OTC ticker rejected
- Already-in-positions ticker still returned (dedup happens in cycle.py)
- LLM API failure returns empty list
- Both models fail returns empty list
- Single model partial failure
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.models import TradeRecommendation, TradingAnalysis


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_analysis(*symbols: str) -> TradingAnalysis:
    """Build a TradingAnalysis with BUY recommendations for the given symbols."""
    return TradingAnalysis(
        market_assessment="Test market assessment",
        recommendations=[
            TradeRecommendation(
                action="BUY",
                symbol=sym,
                confidence=0.75,
                stop_loss_pct=0.10,
                reasoning="Test reasoning",
            )
            for sym in symbols
        ],
    )


def _mock_yf_info(exchange: str | None):
    """Return a mock yf.Ticker where .info['exchange'] == exchange."""
    ticker_mock = MagicMock()
    ticker_mock.info = {"exchange": exchange} if exchange else {}
    return ticker_mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunDiscoveryCycle:

    def test_nyse_ticker_accepted(self, mock_settings, e2e_db):
        """Ticker on NYSE exchange passes OTC filter and is returned."""
        bull_analysis = _make_analysis("FREQ")
        bear_analysis = _make_analysis("FREQ")

        with patch("src.consensus._query_bull_discovery", return_value=bull_analysis), \
             patch("src.consensus._query_bear_discovery", return_value=bear_analysis), \
             patch("src.consensus.yf.Ticker", return_value=_mock_yf_info("NYSE")):
            from src.consensus import run_discovery_cycle
            result = run_discovery_cycle([], [], 1000.0)

        assert "FREQ" in result

    def test_otc_ticker_rejected(self, mock_settings, e2e_db):
        """Ticker on OTC exchange is rejected by OTC filter."""
        bull_analysis = _make_analysis("OTCX")
        bear_analysis = _make_analysis("OTCX")

        with patch("src.consensus._query_bull_discovery", return_value=bull_analysis), \
             patch("src.consensus._query_bear_discovery", return_value=bear_analysis), \
             patch("src.consensus.yf.Ticker", return_value=_mock_yf_info("OTC")):
            from src.consensus import run_discovery_cycle
            result = run_discovery_cycle([], [], 1000.0)

        assert "OTCX" not in result

    def test_unknown_exchange_rejected(self, mock_settings, e2e_db):
        """Ticker with unknown exchange is rejected conservatively."""
        bull_analysis = _make_analysis("UNKX")
        bear_analysis = _make_analysis("UNKX")

        with patch("src.consensus._query_bull_discovery", return_value=bull_analysis), \
             patch("src.consensus._query_bear_discovery", return_value=bear_analysis), \
             patch("src.consensus.yf.Ticker", return_value=_mock_yf_info("UNKNOWN_EXCHANGE")):
            from src.consensus import run_discovery_cycle
            result = run_discovery_cycle([], [], 1000.0)

        assert "UNKX" not in result

    def test_ticker_in_positions_still_returned(self, mock_settings, e2e_db):
        """Dedup against positions happens in cycle.py, not here.

        run_discovery_cycle() returns the ticker even if it is in positions.
        The caller (gather_candidates) removes it.
        """
        positions = [{"symbol": "RXRX", "shares": 100, "price": 8.0, "market_value": 800.0}]
        bull_analysis = _make_analysis("RXRX")
        bear_analysis = _make_analysis("RXRX")

        with patch("src.consensus._query_bull_discovery", return_value=bull_analysis), \
             patch("src.consensus._query_bear_discovery", return_value=bear_analysis), \
             patch("src.consensus.yf.Ticker", return_value=_mock_yf_info("NASDAQ")):
            from src.consensus import run_discovery_cycle
            result = run_discovery_cycle(positions, [], 1000.0)

        # Should still be in result -- dedup is the caller's job
        assert "RXRX" in result

    def test_bull_api_failure_returns_empty_list(self, mock_settings, e2e_db):
        """If both models fail, run_discovery_cycle returns []."""
        with patch("src.consensus._query_bull_discovery", side_effect=Exception("API error")), \
             patch("src.consensus._query_bear_discovery", side_effect=Exception("API error")):
            from src.consensus import run_discovery_cycle
            result = run_discovery_cycle([], [], 1000.0)

        assert result == []

    def test_single_model_failure_uses_other(self, mock_settings, e2e_db):
        """If only one model fails, the other's proposals are still validated."""
        bear_analysis = _make_analysis("VERI")

        with patch("src.consensus._query_bull_discovery", side_effect=Exception("API error")), \
             patch("src.consensus._query_bear_discovery", return_value=bear_analysis), \
             patch("src.consensus.yf.Ticker", return_value=_mock_yf_info("NASDAQ")):
            from src.consensus import run_discovery_cycle
            result = run_discovery_cycle([], [], 1000.0)

        assert "VERI" in result

    def test_yfinance_lookup_failure_rejects_ticker(self, mock_settings, e2e_db):
        """If yfinance lookup fails for a symbol, it is treated as unknown (rejected)."""
        bull_analysis = _make_analysis("BADTICKER")
        bear_analysis = _make_analysis("BADTICKER")

        ticker_mock = MagicMock()
        ticker_mock.info = {}  # No exchange key -> None

        with patch("src.consensus._query_bull_discovery", return_value=bull_analysis), \
             patch("src.consensus._query_bear_discovery", return_value=bear_analysis), \
             patch("src.consensus.yf.Ticker", return_value=ticker_mock):
            from src.consensus import run_discovery_cycle
            result = run_discovery_cycle([], [], 1000.0)

        # exchange=None -> rejected by otc_filter
        assert "BADTICKER" not in result

    def test_nasdaq_ticker_accepted(self, mock_settings, e2e_db):
        """Ticker on NASDAQ exchange passes OTC filter."""
        bull_analysis = _make_analysis("SLDB")
        bear_analysis = _make_analysis("SLDB")

        with patch("src.consensus._query_bull_discovery", return_value=bull_analysis), \
             patch("src.consensus._query_bear_discovery", return_value=bear_analysis), \
             patch("src.consensus.yf.Ticker", return_value=_mock_yf_info("NMS")):
            from src.consensus import run_discovery_cycle
            result = run_discovery_cycle([], [], 1000.0)

        assert "SLDB" in result

    def test_mixed_proposals_filtered_correctly(self, mock_settings, e2e_db):
        """NYSE ticker accepted, OTC ticker rejected in same call."""
        bull_analysis = _make_analysis("GOOD")
        bear_analysis = _make_analysis("BAD")

        def _mock_ticker(sym):
            mock = MagicMock()
            mock.info = {"exchange": "NYSE" if sym == "GOOD" else "OTC"}
            return mock

        with patch("src.consensus._query_bull_discovery", return_value=bull_analysis), \
             patch("src.consensus._query_bear_discovery", return_value=bear_analysis), \
             patch("src.consensus.yf.Ticker", side_effect=_mock_ticker):
            from src.consensus import run_discovery_cycle
            result = run_discovery_cycle([], [], 1000.0)

        assert "GOOD" in result
        assert "BAD" not in result
