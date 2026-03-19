"""Tests for confidence-tiered position sizing (SIZE-01 through SIZE-04)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.sizing import compute_shares


class TestHighConviction:
    """SIZE-02: High conviction (>= 0.75) caps at 40% of buying power."""

    def test_high_conviction_basic(self):
        # 40% of 10000 = 4000, 4000/5 = 800 shares
        assert compute_shares(Decimal("10000"), Decimal("5.00"), 0.80) == 800

    def test_high_conviction_boundary(self):
        # 0.75 is >= 0.75, so high conviction
        assert compute_shares(Decimal("10000"), Decimal("5.00"), 0.75) == 800

    def test_high_conviction_low_price(self):
        # 40% of 500 = 200, 200/3 = 66 whole shares
        assert compute_shares(Decimal("500"), Decimal("3.00"), 0.80) == 66


class TestNormalConviction:
    """SIZE-03: Normal conviction (>= 0.6) caps at 20% of buying power."""

    def test_normal_conviction_basic(self):
        # 20% of 10000 = 2000, 2000/5 = 400 shares
        assert compute_shares(Decimal("10000"), Decimal("5.00"), 0.65) == 400

    def test_normal_conviction_boundary(self):
        # 0.60 is >= 0.60, so normal conviction
        assert compute_shares(Decimal("10000"), Decimal("5.00"), 0.60) == 400

    def test_normal_conviction_low_price(self):
        # 20% of 500 = 100, 100/3 = 33 whole shares, 33*3=99 >= 50
        assert compute_shares(Decimal("500"), Decimal("3.00"), 0.65) == 33


class TestBelowThreshold:
    """SIZE-01: Confidence below 0.6 returns 0 shares."""

    def test_below_threshold(self):
        assert compute_shares(Decimal("10000"), Decimal("5.00"), 0.55) == 0

    def test_zero_confidence(self):
        assert compute_shares(Decimal("10000"), Decimal("5.00"), 0.0) == 0


class TestMinimumTradeValue:
    """SIZE-04: No trade smaller than $50 notional."""

    def test_below_minimum_notional(self):
        # 20% of 100 = 20, below $50 min
        assert compute_shares(Decimal("100"), Decimal("60.00"), 0.65) == 0

    def test_shares_below_minimum(self):
        # 20% of 250 = 50, 50/60 = 0 whole shares
        assert compute_shares(Decimal("250"), Decimal("60.00"), 0.65) == 0


class TestEdgeCases:
    """Edge case handling."""

    def test_price_zero(self):
        assert compute_shares(Decimal("10000"), Decimal("0"), 0.80) == 0
