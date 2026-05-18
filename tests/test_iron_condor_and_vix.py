"""Tests for iron condor signal and VIX-based position sizing."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.market_context import MarketContext
from src.options_strategy import (
    evaluate_iron_condor_signal,
    get_vix_size_multiplier,
)


def _ctx(trend_bias="NEUTRAL", market_regime="LOW_VOL", vix=12.0,
         rsi_14=50.0, vwap_slope=0.0) -> MarketContext:
    return MarketContext(
        timestamp="2026-01-01T10:00:00", symbol="SPY",
        underlying_price=500.0, vix=vix,
        market_regime=market_regime, trend_bias=trend_bias,
        orb_high=None, orb_low=None, orb_formed=False,
        above_orb=None, below_orb=None, spy_1d_change_pct=0.0,
        vwap=500.0, rsi_14=rsi_14, vwap_slope=vwap_slope,
    )


def _cfg(condor_otm=0.015, spread_width=5.0):
    cfg = MagicMock()
    cfg.options_condor_otm_pct = condor_otm
    cfg.options_spread_width = spread_width
    cfg.vix_size_low_vol = 1.5
    cfg.vix_size_normal = 1.0
    cfg.vix_size_high_vol = 0.5
    return cfg


# ---------------------------------------------------------------------------
# evaluate_iron_condor_signal
# ---------------------------------------------------------------------------

class TestEvaluateIronCondorSignal:
    def test_fires_in_low_vol_neutral(self):
        sig = evaluate_iron_condor_signal(_ctx(), _cfg())
        assert sig is not None
        assert sig.strategy == "IRON_CONDOR"
        assert sig.direction == "NEUTRAL"

    def test_blocked_in_normal_regime(self):
        assert evaluate_iron_condor_signal(_ctx(market_regime="NORMAL"), _cfg()) is None

    def test_blocked_in_high_vol(self):
        assert evaluate_iron_condor_signal(_ctx(market_regime="HIGH_VOL", vix=28.0), _cfg()) is None

    def test_blocked_when_bullish(self):
        assert evaluate_iron_condor_signal(_ctx(trend_bias="BULLISH"), _cfg()) is None

    def test_blocked_when_bearish(self):
        assert evaluate_iron_condor_signal(_ctx(trend_bias="BEARISH"), _cfg()) is None

    def test_blocked_by_extreme_rsi_high(self):
        assert evaluate_iron_condor_signal(_ctx(rsi_14=70.0), _cfg()) is None

    def test_blocked_by_extreme_rsi_low(self):
        assert evaluate_iron_condor_signal(_ctx(rsi_14=34.9), _cfg()) is None

    def test_blocked_by_strong_slope(self):
        assert evaluate_iron_condor_signal(_ctx(vwap_slope=0.0015), _cfg()) is None

    def test_market_regime_set_on_signal(self):
        sig = evaluate_iron_condor_signal(_ctx(), _cfg())
        assert sig.market_regime == "LOW_VOL"

    def test_confidence_above_zero(self):
        sig = evaluate_iron_condor_signal(_ctx(), _cfg())
        assert sig.confidence > 0

    def test_passes_when_data_unavailable(self):
        ctx = _ctx(rsi_14=None, vwap_slope=None)
        sig = evaluate_iron_condor_signal(ctx, _cfg())
        assert sig is not None


# ---------------------------------------------------------------------------
# get_vix_size_multiplier
# ---------------------------------------------------------------------------

class TestGetVixSizeMultiplier:
    def test_low_vol_returns_1_5(self):
        cfg = _cfg()
        assert get_vix_size_multiplier("LOW_VOL", cfg) == 1.5

    def test_normal_returns_1_0(self):
        cfg = _cfg()
        assert get_vix_size_multiplier("NORMAL", cfg) == 1.0

    def test_high_vol_returns_0_5(self):
        cfg = _cfg()
        assert get_vix_size_multiplier("HIGH_VOL", cfg) == 0.5

    def test_extreme_returns_1_0_safety_default(self):
        cfg = _cfg()
        assert get_vix_size_multiplier("EXTREME", cfg) == 1.0

    def test_low_vol_scales_contracts_up(self):
        cfg = _cfg()
        base = 2
        scaled = max(1, min(int(base * get_vix_size_multiplier("LOW_VOL", cfg)), 5))
        assert scaled == 3

    def test_high_vol_scales_contracts_down(self):
        cfg = _cfg()
        base = 2
        scaled = max(1, int(base * get_vix_size_multiplier("HIGH_VOL", cfg)))
        assert scaled == 1
