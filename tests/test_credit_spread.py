"""Tests for credit spread signal evaluation and strike selection."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.market_context import MarketContext
from src.options_strategy import (
    StrategySignal,
    compute_credit_spread_contracts,
    evaluate_credit_spread_signal,
    select_credit_spread,
)


def _make_ctx(trend_bias="NEUTRAL", market_regime="NORMAL", vix=15.0, price=500.0) -> MarketContext:
    return MarketContext(
        timestamp="2026-01-01T10:00:00",
        symbol="SPY",
        underlying_price=price,
        vix=vix,
        market_regime=market_regime,
        trend_bias=trend_bias,
        orb_high=None,
        orb_low=None,
        orb_formed=False,
        above_orb=None,
        below_orb=None,
        spy_1d_change_pct=0.0,
        vwap=price,
    )


def _make_config(spread_width=5.0, credit_otm_pct=0.01):
    cfg = MagicMock()
    cfg.options_spread_width = spread_width
    cfg.options_credit_otm_pct = credit_otm_pct
    return cfg


def _make_chain(symbol="SPY", price=500.0, spread_width=5.0, otm_pct=0.01):
    """Build a minimal synthetic chain around price."""
    strikes = [price - 10, price - 5, price - 1, price, price + 1, price + 5, price + 10]
    def leg(s):
        return {
            "strike": s,
            "occ_symbol": f"SPY{s:.0f}",
            "streamer_symbol": f".SPY{s:.0f}",
            "option_type": "P",
        }
    return {
        "symbol": symbol,
        "expiry": "2026-01-01",
        "dte": 0,
        "underlying_price": price,
        "puts": [leg(s) for s in strikes],
        "calls": [leg(s) for s in strikes],
    }


# ---------------------------------------------------------------------------
# evaluate_credit_spread_signal
# ---------------------------------------------------------------------------

class TestEvaluateCreditSpreadSignal:
    def test_neutral_bias_returns_put_credit(self):
        ctx = _make_ctx(trend_bias="NEUTRAL", market_regime="NORMAL")
        sig = evaluate_credit_spread_signal(ctx, _make_config())
        assert sig is not None
        assert sig.strategy == "CREDIT_PUT"
        assert sig.direction == "PUT"

    def test_bullish_bias_returns_put_credit(self):
        ctx = _make_ctx(trend_bias="BULLISH")
        sig = evaluate_credit_spread_signal(ctx, _make_config())
        assert sig is not None
        assert sig.strategy == "CREDIT_PUT"
        assert sig.direction == "PUT"

    def test_bearish_bias_returns_call_credit(self):
        ctx = _make_ctx(trend_bias="BEARISH")
        sig = evaluate_credit_spread_signal(ctx, _make_config())
        assert sig is not None
        assert sig.strategy == "CREDIT_CALL"
        assert sig.direction == "CALL"

    def test_high_vol_regime_returns_none(self):
        ctx = _make_ctx(market_regime="HIGH_VOL", vix=28.0)
        sig = evaluate_credit_spread_signal(ctx, _make_config())
        assert sig is None

    def test_extreme_regime_returns_none(self):
        ctx = _make_ctx(market_regime="EXTREME", vix=40.0)
        sig = evaluate_credit_spread_signal(ctx, _make_config())
        assert sig is None

    def test_zero_price_returns_none(self):
        ctx = _make_ctx(price=0.0)
        sig = evaluate_credit_spread_signal(ctx, _make_config())
        assert sig is None

    def test_confidence_above_zero(self):
        ctx = _make_ctx()
        sig = evaluate_credit_spread_signal(ctx, _make_config())
        assert sig.confidence > 0

    def test_dte_is_zero(self):
        ctx = _make_ctx()
        sig = evaluate_credit_spread_signal(ctx, _make_config())
        assert sig.suggested_dte == 0


# ---------------------------------------------------------------------------
# select_credit_spread
# ---------------------------------------------------------------------------

class TestSelectCreditSpread:
    def test_put_credit_finds_two_different_legs(self):
        chain = _make_chain(price=500.0)
        result = select_credit_spread(chain, "PUT", 500.0, 5.0, 0.01)
        assert result is not None
        short_leg, long_leg = result
        assert short_leg["occ_symbol"] != long_leg["occ_symbol"]

    def test_call_credit_finds_two_different_legs(self):
        chain = _make_chain(price=500.0)
        result = select_credit_spread(chain, "CALL", 500.0, 5.0, 0.01)
        assert result is not None
        short_leg, long_leg = result
        assert short_leg["occ_symbol"] != long_leg["occ_symbol"]

    def test_put_credit_short_below_price(self):
        chain = _make_chain(price=500.0)
        result = select_credit_spread(chain, "PUT", 500.0, 5.0, 0.01)
        assert result is not None
        short_leg, _ = result
        assert short_leg["strike"] < 500.0

    def test_call_credit_short_above_price(self):
        chain = _make_chain(price=500.0)
        result = select_credit_spread(chain, "CALL", 500.0, 5.0, 0.01)
        assert result is not None
        short_leg, _ = result
        assert short_leg["strike"] > 500.0

    def test_put_credit_long_below_short(self):
        chain = _make_chain(price=500.0)
        result = select_credit_spread(chain, "PUT", 500.0, 5.0, 0.01)
        assert result is not None
        short_leg, long_leg = result
        assert long_leg["strike"] < short_leg["strike"]

    def test_call_credit_long_above_short(self):
        chain = _make_chain(price=500.0)
        result = select_credit_spread(chain, "CALL", 500.0, 5.0, 0.01)
        assert result is not None
        short_leg, long_leg = result
        assert long_leg["strike"] > short_leg["strike"]

    def test_empty_chain_returns_none(self):
        chain = {"puts": [], "calls": [], "underlying_price": 500.0}
        assert select_credit_spread(chain, "PUT", 500.0, 5.0, 0.01) is None

    def test_single_leg_chain_returns_none(self):
        chain = {
            "puts": [{"strike": 495.0, "occ_symbol": "SPY495", "streamer_symbol": ".SPY495"}],
            "calls": [],
            "underlying_price": 500.0,
        }
        assert select_credit_spread(chain, "PUT", 500.0, 5.0, 0.01) is None


# ---------------------------------------------------------------------------
# compute_credit_spread_contracts
# ---------------------------------------------------------------------------

class TestComputeCreditSpreadContracts:
    def test_basic_sizing(self):
        # width=$5, credit=$1 → max_loss_per_contract=$400 → with $400 budget → 1 contract
        contracts = compute_credit_spread_contracts(
            max_loss_per_trade=400.0, width=5.0, net_credit=1.0, max_contracts=5
        )
        assert contracts == 1

    def test_larger_budget_scales_up(self):
        contracts = compute_credit_spread_contracts(
            max_loss_per_trade=1600.0, width=5.0, net_credit=1.0, max_contracts=5
        )
        assert contracts == 4

    def test_capped_at_max_contracts(self):
        contracts = compute_credit_spread_contracts(
            max_loss_per_trade=100_000.0, width=5.0, net_credit=1.0, max_contracts=5
        )
        assert contracts == 5

    def test_minimum_one_contract(self):
        contracts = compute_credit_spread_contracts(
            max_loss_per_trade=1.0, width=5.0, net_credit=1.0, max_contracts=5
        )
        assert contracts == 1

    def test_zero_or_negative_max_loss_per_contract_returns_zero(self):
        # credit >= width → max_loss_per_contract <= 0
        contracts = compute_credit_spread_contracts(
            max_loss_per_trade=500.0, width=5.0, net_credit=5.0, max_contracts=5
        )
        assert contracts == 0
