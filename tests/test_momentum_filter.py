"""Tests for the RSI + VWAP-slope momentum entry filter."""
from __future__ import annotations

import pandas as pd
import pytest

from src.market_context import MarketContext, _compute_rsi, _compute_vwap_slope
from src.options_strategy import passes_momentum_filter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(rsi=None, slope=None, trend_bias="NEUTRAL", market_regime="NORMAL") -> MarketContext:
    return MarketContext(
        timestamp="2026-01-01T10:00:00",
        symbol="SPY",
        underlying_price=500.0,
        vix=15.0,
        market_regime=market_regime,
        trend_bias=trend_bias,
        orb_high=None,
        orb_low=None,
        orb_formed=False,
        above_orb=None,
        below_orb=None,
        spy_1d_change_pct=0.0,
        vwap=500.0,
        rsi_14=rsi,
        vwap_slope=slope,
    )


# ---------------------------------------------------------------------------
# passes_momentum_filter — debit spreads
# ---------------------------------------------------------------------------

class TestDebitCallFilter:
    def test_passes_with_bullish_rsi_and_positive_slope(self):
        ok, _ = passes_momentum_filter(_ctx(rsi=55.0, slope=0.0002), "CALL")
        assert ok

    def test_passes_exactly_at_rsi_threshold(self):
        ok, _ = passes_momentum_filter(_ctx(rsi=45.0, slope=0.0001), "CALL")
        assert ok

    def test_blocked_by_low_rsi(self):
        ok, reason = passes_momentum_filter(_ctx(rsi=44.9, slope=0.001), "CALL")
        assert not ok
        assert "RSI" in reason

    def test_blocked_by_negative_slope(self):
        ok, reason = passes_momentum_filter(_ctx(rsi=55.0, slope=-0.0001), "CALL")
        assert not ok
        assert "slope" in reason

    def test_passes_when_both_none(self):
        ok, reason = passes_momentum_filter(_ctx(rsi=None, slope=None), "CALL")
        assert ok
        assert "bypassed" in reason

    def test_passes_when_only_rsi_ok(self):
        ok, _ = passes_momentum_filter(_ctx(rsi=50.0, slope=None), "CALL")
        assert ok

    def test_blocked_when_only_rsi_bad(self):
        ok, _ = passes_momentum_filter(_ctx(rsi=40.0, slope=None), "CALL")
        assert not ok


class TestDebitPutFilter:
    def test_passes_with_bearish_rsi_and_negative_slope(self):
        ok, _ = passes_momentum_filter(_ctx(rsi=45.0, slope=-0.0002), "PUT")
        assert ok

    def test_passes_exactly_at_rsi_threshold(self):
        ok, _ = passes_momentum_filter(_ctx(rsi=55.0, slope=-0.0001), "PUT")
        assert ok

    def test_blocked_by_high_rsi(self):
        ok, reason = passes_momentum_filter(_ctx(rsi=55.1, slope=-0.001), "PUT")
        assert not ok
        assert "RSI" in reason

    def test_blocked_by_positive_slope(self):
        ok, reason = passes_momentum_filter(_ctx(rsi=45.0, slope=0.0001), "PUT")
        assert not ok
        assert "slope" in reason


# ---------------------------------------------------------------------------
# passes_momentum_filter — credit spreads (looser)
# ---------------------------------------------------------------------------

class TestCreditPutFilter:
    def test_passes_in_normal_conditions(self):
        ok, _ = passes_momentum_filter(_ctx(rsi=50.0, slope=0.0), "PUT", is_credit=True)
        assert ok

    def test_passes_at_rsi_boundary(self):
        ok, _ = passes_momentum_filter(_ctx(rsi=30.0, slope=0.0), "PUT", is_credit=True)
        assert ok

    def test_blocked_below_rsi_30(self):
        ok, reason = passes_momentum_filter(_ctx(rsi=29.9, slope=0.0), "PUT", is_credit=True)
        assert not ok
        assert "RSI" in reason

    def test_blocked_by_strong_downtrend(self):
        ok, reason = passes_momentum_filter(_ctx(rsi=40.0, slope=-0.0011), "PUT", is_credit=True)
        assert not ok
        assert "slope" in reason

    def test_passes_with_slight_downtrend(self):
        ok, _ = passes_momentum_filter(_ctx(rsi=40.0, slope=-0.0009), "PUT", is_credit=True)
        assert ok


class TestCreditCallFilter:
    def test_passes_in_normal_conditions(self):
        ok, _ = passes_momentum_filter(_ctx(rsi=50.0, slope=0.0), "CALL", is_credit=True)
        assert ok

    def test_passes_at_rsi_boundary(self):
        ok, _ = passes_momentum_filter(_ctx(rsi=70.0, slope=0.0), "CALL", is_credit=True)
        assert ok

    def test_blocked_above_rsi_70(self):
        ok, reason = passes_momentum_filter(_ctx(rsi=70.1, slope=0.0), "CALL", is_credit=True)
        assert not ok
        assert "RSI" in reason

    def test_blocked_by_strong_uptrend(self):
        ok, reason = passes_momentum_filter(_ctx(rsi=60.0, slope=0.0011), "CALL", is_credit=True)
        assert not ok
        assert "slope" in reason


# ---------------------------------------------------------------------------
# _compute_rsi
# ---------------------------------------------------------------------------

class TestComputeRSI:
    def _closes(self, values):
        return pd.Series(values, dtype=float)

    def test_returns_none_if_too_few_bars(self):
        assert _compute_rsi(self._closes([1.0] * 10)) is None

    def test_all_gains_returns_100(self):
        closes = self._closes([100.0 + i for i in range(20)])
        rsi = _compute_rsi(closes)
        assert rsi == 100.0

    def test_all_losses_returns_zero(self):
        closes = self._closes([100.0 - i * 0.5 for i in range(20)])
        rsi = _compute_rsi(closes)
        assert rsi is not None
        assert rsi < 10

    def test_flat_prices_treated_as_all_loss(self):
        closes = self._closes([100.0] * 20)
        rsi = _compute_rsi(closes)
        # All diffs are 0 → avg_loss=0 → RSI=100
        assert rsi == 100.0

    def test_returns_float_between_0_and_100(self):
        import random; random.seed(42)
        closes = self._closes([100.0 + random.gauss(0, 1) for _ in range(30)])
        rsi = _compute_rsi(closes)
        assert rsi is not None
        assert 0 <= rsi <= 100


# ---------------------------------------------------------------------------
# _compute_vwap_slope
# ---------------------------------------------------------------------------

class TestComputeVwapSlope:
    def _bars(self, closes, volumes=None):
        n = len(closes)
        closes = [float(c) for c in closes]
        if volumes is None:
            volumes = [1_000_000] * n
        return pd.DataFrame({
            "Open": closes,
            "High": [c + 0.1 for c in closes],
            "Low": [c - 0.1 for c in closes],
            "Close": closes,
            "Volume": volumes,
        })

    def test_returns_none_if_too_few_bars(self):
        assert _compute_vwap_slope(self._bars([500.0] * 5)) is None

    def test_positive_slope_for_rising_prices(self):
        closes = [500.0 + i * 0.5 for i in range(15)]
        slope = _compute_vwap_slope(self._bars(closes))
        assert slope is not None
        assert slope > 0

    def test_negative_slope_for_falling_prices(self):
        closes = [500.0 - i * 0.5 for i in range(15)]
        slope = _compute_vwap_slope(self._bars(closes))
        assert slope is not None
        assert slope < 0

    def test_near_zero_slope_for_flat_prices(self):
        closes = [500.0] * 15
        slope = _compute_vwap_slope(self._bars(closes))
        assert slope is not None
        assert abs(slope) < 1e-6

    def test_empty_dataframe_returns_none(self):
        assert _compute_vwap_slope(pd.DataFrame()) is None
