"""Tests for time-of-day dynamic profit targets."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.daily_target import get_dynamic_profit_target

ET = ZoneInfo("America/New_York")


def _et(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 5, 18, hour, minute, tzinfo=ET)


# ---------------------------------------------------------------------------
# Debit spreads — close aggressively (theta hurts)
# ---------------------------------------------------------------------------

class TestDebitDynamicTargets:
    @pytest.mark.parametrize("spread_type", ["CALL_DEBIT", "PUT_DEBIT"])
    def test_early_morning_returns_50pct(self, spread_type):
        # Before 11:00 — got a quick move, take 50%
        assert get_dynamic_profit_target(spread_type, _et(9, 45)) == 0.50
        assert get_dynamic_profit_target(spread_type, _et(10, 59)) == 0.50

    @pytest.mark.parametrize("spread_type", ["CALL_DEBIT", "PUT_DEBIT"])
    def test_midday_returns_40pct(self, spread_type):
        assert get_dynamic_profit_target(spread_type, _et(11, 0)) == 0.40
        assert get_dynamic_profit_target(spread_type, _et(12, 30)) == 0.40
        assert get_dynamic_profit_target(spread_type, _et(12, 59)) == 0.40

    @pytest.mark.parametrize("spread_type", ["CALL_DEBIT", "PUT_DEBIT"])
    def test_late_returns_30pct(self, spread_type):
        assert get_dynamic_profit_target(spread_type, _et(13, 0)) == 0.30
        assert get_dynamic_profit_target(spread_type, _et(14, 30)) == 0.30
        assert get_dynamic_profit_target(spread_type, _et(15, 44)) == 0.30

    @pytest.mark.parametrize("spread_type", ["CALL_DEBIT", "PUT_DEBIT"])
    def test_target_decreases_over_day(self, spread_type):
        early = get_dynamic_profit_target(spread_type, _et(10, 0))
        midday = get_dynamic_profit_target(spread_type, _et(12, 0))
        late = get_dynamic_profit_target(spread_type, _et(14, 0))
        assert early > midday > late

    def test_exact_11am_cutoff(self):
        # Just before 11:00 is still early
        assert get_dynamic_profit_target("CALL_DEBIT", _et(10, 59)) == 0.50
        # At 11:00 exactly switches to midday
        assert get_dynamic_profit_target("CALL_DEBIT", _et(11, 0)) == 0.40

    def test_exact_1pm_cutoff(self):
        assert get_dynamic_profit_target("PUT_DEBIT", _et(12, 59)) == 0.40
        assert get_dynamic_profit_target("PUT_DEBIT", _et(13, 0)) == 0.30


# ---------------------------------------------------------------------------
# Credit spreads — let theta cook (lower early, higher late)
# ---------------------------------------------------------------------------

class TestCreditDynamicTargets:
    @pytest.mark.parametrize("spread_type", ["CALL_CREDIT", "PUT_CREDIT"])
    def test_early_morning_returns_25pct(self, spread_type):
        assert get_dynamic_profit_target(spread_type, _et(9, 45)) == 0.25
        assert get_dynamic_profit_target(spread_type, _et(11, 30)) == 0.25
        assert get_dynamic_profit_target(spread_type, _et(11, 59)) == 0.25

    @pytest.mark.parametrize("spread_type", ["CALL_CREDIT", "PUT_CREDIT"])
    def test_midday_returns_40pct(self, spread_type):
        assert get_dynamic_profit_target(spread_type, _et(12, 0)) == 0.40
        assert get_dynamic_profit_target(spread_type, _et(13, 0)) == 0.40
        assert get_dynamic_profit_target(spread_type, _et(13, 59)) == 0.40

    @pytest.mark.parametrize("spread_type", ["CALL_CREDIT", "PUT_CREDIT"])
    def test_late_returns_60pct(self, spread_type):
        assert get_dynamic_profit_target(spread_type, _et(14, 0)) == 0.60
        assert get_dynamic_profit_target(spread_type, _et(15, 0)) == 0.60
        assert get_dynamic_profit_target(spread_type, _et(15, 44)) == 0.60

    @pytest.mark.parametrize("spread_type", ["CALL_CREDIT", "PUT_CREDIT"])
    def test_target_increases_over_day(self, spread_type):
        early = get_dynamic_profit_target(spread_type, _et(10, 0))
        midday = get_dynamic_profit_target(spread_type, _et(13, 0))
        late = get_dynamic_profit_target(spread_type, _et(15, 0))
        assert early < midday < late

    def test_exact_noon_cutoff(self):
        assert get_dynamic_profit_target("PUT_CREDIT", _et(11, 59)) == 0.25
        assert get_dynamic_profit_target("PUT_CREDIT", _et(12, 0)) == 0.40

    def test_exact_2pm_cutoff(self):
        assert get_dynamic_profit_target("CALL_CREDIT", _et(13, 59)) == 0.40
        assert get_dynamic_profit_target("CALL_CREDIT", _et(14, 0)) == 0.60


# ---------------------------------------------------------------------------
# Defaults to current time when now_et is None
# ---------------------------------------------------------------------------

class TestDefaultsToNow:
    def test_returns_float_when_no_time_given(self):
        result = get_dynamic_profit_target("CALL_DEBIT")
        assert isinstance(result, float)
        assert 0.0 < result <= 1.0

    def test_returns_float_for_credit_no_time(self):
        result = get_dynamic_profit_target("PUT_CREDIT")
        assert isinstance(result, float)
        assert 0.0 < result <= 1.0
