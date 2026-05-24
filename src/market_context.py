"""Market context for 0DTE options trading.

Fetches VIX, intraday ORB levels, VWAP, and trend bias via yfinance.
All time comparisons use America/New_York timezone.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from loguru import logger

ET = ZoneInfo("America/New_York")


@dataclass
class MarketContext:
    timestamp: str
    symbol: str
    underlying_price: float
    vix: float
    market_regime: str          # LOW_VOL, NORMAL, HIGH_VOL, EXTREME
    trend_bias: str             # BULLISH, BEARISH, NEUTRAL
    orb_high: float | None
    orb_low: float | None
    orb_formed: bool
    above_orb: bool | None      # price > orb_high
    below_orb: bool | None      # price < orb_low
    spy_1d_change_pct: float
    vwap: float | None
    rsi_14: float | None        # 14-period RSI on 5-min bars
    vwap_slope: float | None    # fractional VWAP change over last 6 bars (30 min)
    hma_trend: str = "FLAT"          # RISING, FALLING, FLAT — from HMA(20) on 5-min closes
    alligator_state: str = "SLEEPING"  # EATING_UP, EATING_DOWN, SLEEPING
    macro_risk_day: bool = False     # True when a high-impact macro event is scheduled today (FMP)
    macro_events: list = None        # List of event dicts from FMP economic calendar


def fetch_market_context(symbol: str = "SPY") -> MarketContext:
    """Fetch current market context via yfinance 5-min bars + VIX.

    Downloads 5-day 5-min OHLCV for the underlying to compute:
    - ORB (high/low of first 15-min bar after 9:30 ET)
    - Current price position relative to ORB
    - VIX level and regime
    - Intraday trend bias from VWAP

    Returns MarketContext dataclass.
    """
    now_et = datetime.now(ET)
    timestamp = now_et.isoformat()

    # VIX
    vix = _fetch_vix()
    regime = classify_market_regime(vix)

    # 5-min bars
    try:
        bars = yf.download(symbol, period="5d", interval="5m", progress=False, auto_adjust=True)
    except Exception as e:
        logger.warning("Failed to download 5m bars for {}: {}", symbol, e)
        bars = pd.DataFrame()

    # Today's bars only
    if not bars.empty:
        # Handle MultiIndex columns from yfinance
        if isinstance(bars.columns, pd.MultiIndex):
            bars = bars.xs(symbol, axis=1, level=1) if symbol in bars.columns.get_level_values(1) else bars.droplevel(1, axis=1)

        today_str = now_et.strftime("%Y-%m-%d")
        bars.index = pd.to_datetime(bars.index)
        if bars.index.tz is None:
            bars.index = bars.index.tz_localize("UTC")
        bars.index = bars.index.tz_convert(ET)
        bars_today = bars[bars.index.date == now_et.date()]
    else:
        bars_today = pd.DataFrame()

    underlying_price = 0.0
    if not bars_today.empty:
        underlying_price = float(bars_today["Close"].iloc[-1])

    spy_1d_change_pct = _compute_1d_change(bars, symbol)

    orb_result = get_orb_range(bars_today)
    orb_high: float | None = None
    orb_low: float | None = None
    orb_formed = False

    if orb_result is not None:
        orb_high, orb_low = orb_result
        orb_formed = True

    above_orb = (underlying_price > orb_high) if orb_formed and orb_high else None
    below_orb = (underlying_price < orb_low) if orb_formed and orb_low else None

    vwap = compute_vwap(bars_today) if not bars_today.empty else None
    trend_bias = get_trend_bias(vwap, underlying_price) if vwap else "NEUTRAL"

    rsi_14 = _compute_rsi(bars_today["Close"]) if not bars_today.empty else None
    vwap_slope = _compute_vwap_slope(bars_today) if not bars_today.empty else None
    hma_trend = _compute_hma_trend(bars_today["Close"]) if not bars_today.empty else "FLAT"
    alligator_state = _compute_alligator_state(bars_today["Close"]) if not bars_today.empty else "SLEEPING"

    from src.fmp_calendar import fetch_macro_risk
    macro = fetch_macro_risk()
    macro_risk_day = macro.is_risk_day
    macro_events = macro.events

    logger.info(
        "Market context: {} @ ${:.2f} | VIX={:.1f} ({}) | ORB [{} - {}] | {} | VWAP={} | RSI={} | slope={} | HMA={} | Alligator={}",
        symbol, underlying_price, vix, regime,
        f"${orb_low:.2f}" if orb_low else "N/A",
        f"${orb_high:.2f}" if orb_high else "N/A",
        trend_bias,
        f"${vwap:.2f}" if vwap else "N/A",
        f"{rsi_14:.1f}" if rsi_14 is not None else "N/A",
        f"{vwap_slope:.4f}" if vwap_slope is not None else "N/A",
        hma_trend,
        alligator_state,
    )

    return MarketContext(
        timestamp=timestamp,
        symbol=symbol,
        underlying_price=underlying_price,
        vix=vix,
        market_regime=regime,
        trend_bias=trend_bias,
        orb_high=orb_high,
        orb_low=orb_low,
        orb_formed=orb_formed,
        above_orb=above_orb,
        below_orb=below_orb,
        spy_1d_change_pct=spy_1d_change_pct,
        vwap=vwap,
        rsi_14=rsi_14,
        vwap_slope=vwap_slope,
        hma_trend=hma_trend,
        alligator_state=alligator_state,
        macro_risk_day=macro_risk_day,
        macro_events=macro_events,
    )


def get_orb_range(bars_today: pd.DataFrame) -> tuple[float, float] | None:
    """Return (high, low) of the first 15-min bar after 9:30 ET.

    Requires today's 5-min bars with a timezone-aware DatetimeIndex (ET).
    Returns None if the opening range hasn't formed yet or data is unavailable.
    """
    if bars_today.empty:
        return None

    orb_start = time(9, 30)
    orb_end = time(9, 45)

    orb_bars = bars_today[
        (bars_today.index.time >= orb_start) &
        (bars_today.index.time < orb_end)
    ]

    if len(orb_bars) < 3:
        return None

    return float(orb_bars["High"].max()), float(orb_bars["Low"].min())


def compute_vwap(bars_today: pd.DataFrame) -> float | None:
    """Compute VWAP for today's 5-min bars."""
    if bars_today.empty or "Volume" not in bars_today.columns:
        return None
    try:
        typical = (bars_today["High"] + bars_today["Low"] + bars_today["Close"]) / 3
        total_vol = bars_today["Volume"].sum()
        if total_vol == 0:
            return None
        return float((typical * bars_today["Volume"]).sum() / total_vol)
    except Exception:
        return None


def classify_market_regime(vix: float) -> str:
    """Map VIX level to named market regime."""
    if vix < 15:
        return "LOW_VOL"
    if vix < 25:
        return "NORMAL"
    if vix < 35:
        return "HIGH_VOL"
    return "EXTREME"


def get_trend_bias(vwap: float | None, current_price: float) -> str:
    """Intraday trend bias based on price vs VWAP."""
    if vwap is None or vwap <= 0 or current_price <= 0:
        return "NEUTRAL"
    deviation = (current_price - vwap) / vwap
    if deviation > 0.001:
        return "BULLISH"
    if deviation < -0.001:
        return "BEARISH"
    return "NEUTRAL"


def _fetch_vix() -> float:
    """Fetch current VIX level via yfinance. Returns 20.0 on failure."""
    try:
        return float(yf.Ticker("^VIX").fast_info.last_price or 20.0)
    except Exception as e:
        logger.warning("VIX fetch failed: {}", e)
        return 20.0


def _compute_1d_change(bars: pd.DataFrame, symbol: str) -> float:
    """Compute 1-day percent change from daily bars."""
    try:
        daily = yf.download(symbol, period="5d", interval="1d", progress=False, auto_adjust=True)
        if isinstance(daily.columns, pd.MultiIndex):
            daily = daily.droplevel(1, axis=1)
        if len(daily) >= 2:
            prev_close = float(daily["Close"].iloc[-2])
            last_close = float(daily["Close"].iloc[-1])
            return (last_close - prev_close) / prev_close * 100
    except Exception:
        pass
    return 0.0


def _compute_rsi(closes: pd.Series, period: int = 14) -> float | None:
    """Compute RSI-14 on a close price series using Wilder's EWM smoothing."""
    if closes is None or len(closes) < period + 1:
        return None
    try:
        delta = closes.diff().dropna()
        gains = delta.clip(lower=0)
        losses = (-delta).clip(lower=0)
        avg_gain = gains.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1]
        avg_loss = losses.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1]
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100.0 - (100.0 / (1 + rs)), 2)
    except Exception:
        return None


def _compute_vwap_slope(bars_today: pd.DataFrame, n_bars: int = 6) -> float | None:
    """VWAP slope as fractional change over the last n_bars (default 6 × 5min = 30 min)."""
    if bars_today.empty or len(bars_today) < n_bars + 1:
        return None
    try:
        typical = (bars_today["High"] + bars_today["Low"] + bars_today["Close"]) / 3
        cum_tpv = (typical * bars_today["Volume"]).cumsum()
        cum_vol = bars_today["Volume"].cumsum()
        rolling_vwap = (cum_tpv / cum_vol).replace(0, float("nan")).dropna()
        if len(rolling_vwap) < n_bars + 1:
            return None
        past_vwap = float(rolling_vwap.iloc[-n_bars])
        current_vwap = float(rolling_vwap.iloc[-1])
        if past_vwap <= 0:
            return None
        return (current_vwap - past_vwap) / past_vwap
    except Exception:
        return None


def _wma(series: pd.Series, period: int) -> pd.Series:
    """Weighted moving average with linearly increasing weights."""
    weights = pd.Series(range(1, period + 1), dtype=float)
    return series.rolling(period).apply(
        lambda x: float((x * weights.values[:len(x)]).sum() / weights.values[:len(x)].sum()),
        raw=True,
    )


def _compute_hma(closes: pd.Series, period: int = 20) -> pd.Series | None:
    """Hull Moving Average: WMA(2·WMA(n/2) − WMA(n), √n).

    Returns a Series aligned to closes, or None if insufficient data.
    """
    sqrt_p = max(2, int(period ** 0.5))
    half = max(2, period // 2)
    min_bars = period + sqrt_p
    if closes is None or len(closes) < min_bars:
        return None
    try:
        raw = 2 * _wma(closes, half) - _wma(closes, period)
        return _wma(raw, sqrt_p)
    except Exception:
        return None


def _compute_hma_trend(closes: pd.Series, period: int = 20) -> str:
    """Return RISING, FALLING, or FLAT based on the last two HMA values."""
    hma = _compute_hma(closes, period)
    if hma is None:
        return "FLAT"
    valid = hma.dropna()
    if len(valid) < 2:
        return "FLAT"
    prev, curr = float(valid.iloc[-2]), float(valid.iloc[-1])
    threshold = abs(curr) * 0.00005  # 0.005% noise filter
    if curr > prev + threshold:
        return "RISING"
    if curr < prev - threshold:
        return "FALLING"
    return "FLAT"


def _compute_alligator_state(closes: pd.Series) -> str:
    """Bill Williams Alligator: classify market as EATING_UP, EATING_DOWN, or SLEEPING.

    Uses unshifted SMMA lines — shifts are display offsets, not relevant for signal logic.
    Jaw = SMMA(13), Teeth = SMMA(8), Lips = SMMA(5).
    EATING_UP:   lips > teeth > jaw (all lines fanning upward)
    EATING_DOWN: lips < teeth < jaw (all lines fanning downward)
    SLEEPING:    lines tangled or neutral
    """
    if closes is None or len(closes) < 13:
        return "SLEEPING"
    try:
        def smma(s: pd.Series, n: int) -> float:
            return float(s.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean().iloc[-1])

        jaw = smma(closes, 13)
        teeth = smma(closes, 8)
        lips = smma(closes, 5)

        if lips > teeth > jaw:
            return "EATING_UP"
        if lips < teeth < jaw:
            return "EATING_DOWN"
        return "SLEEPING"
    except Exception:
        return "SLEEPING"


def select_symbol_for_today(universe: list[str]) -> str:
    """Pick which symbol from the universe has 0DTE available today.

    SPY: Mon/Wed/Fri; QQQ: Tue/Thu; IWM: every day (fallback).
    Returns the first symbol in universe that has a 0DTE today.
    """
    now_et = datetime.now(ET)
    weekday = now_et.weekday()  # 0=Mon, 4=Fri

    # 0DTE schedule by symbol
    dte_schedule: dict[str, set[int]] = {
        "SPY": {0, 2, 4},   # Mon, Wed, Fri
        "QQQ": {1, 3},       # Tue, Thu
        "IWM": {0, 1, 2, 3, 4},  # all days
    }

    for sym in universe:
        schedule = dte_schedule.get(sym, set())
        if weekday in schedule:
            return sym

    return universe[-1] if universe else "SPY"
