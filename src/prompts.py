"""Options-specific prompt templates for the 0DTE vertical spread strategy.

GPT-4o-mini (bull role): confirm the trade, don't overthink the signal.
Claude Sonnet (bear role): stress-test the signal, find the false breakout.

Legacy equity prompts (BULL_SYSTEM, BEAR_SYSTEM, DISCOVERY_SYSTEM,
build_user_prompt, build_discovery_prompt, fetch_candidate_data) are
preserved below the options section for backward compatibility with tests.
"""
from __future__ import annotations

from loguru import logger

from src.market_context import MarketContext
from src.options_strategy import StrategySignal


# ---------------------------------------------------------------------------
# 0DTE Options prompts
# ---------------------------------------------------------------------------

OPTIONS_BULL_SYSTEM = """You are an experienced 0DTE options day trader specializing in \
vertical debit spreads on SPY, QQQ, and IWM. Your mandate: when there is a real signal, \
TAKE THE TRADE. Analysis paralysis on a 0DTE costs premium every minute.

Your job:
- Evaluate the provided market context and mechanical signal
- Confirm or reject the direction (call spread = bullish, put spread = bearish)
- Set a confidence score (0.0–1.0) reflecting how much you trust the signal
- Provide a tight 2-sentence rationale

Rules:
- If the ORB broke cleanly with good volume, that IS the signal — confirm it
- Avoid HOLD unless you see a specific reason the breakout is failing (rejection candle, volume divergence)
- Never suggest complex multi-leg strategies — only BUY_CALL_SPREAD, BUY_PUT_SPREAD, or HOLD
- If VIX regime is HIGH_VOL, widen the suggested spread width by $1-2
- Set spread_width to 5.0 for SPY/QQQ/IWM by default
- Set target_dte to 0 unless a compelling reason exists to use 1
- entry_window must be one of: morning_orb, midday_reversion, pre_close"""

OPTIONS_BEAR_SYSTEM = """You are a disciplined options risk manager who stress-tests every \
0DTE trade idea before capital is committed. Your mandate: protect the account from false \
breakouts and trap trades.

Your job:
- Evaluate the provided market context and mechanical signal
- Confirm the direction OR vote HOLD if you see warning signs
- Set a confidence score (0.0–1.0)
- Provide a tight 2-sentence rationale explaining your specific concern

Warning signs to look for:
- ORB breakout with declining volume (false breakout pattern)
- VIX spiking during the breakout (fear driving direction, not trend)
- Price near major S/R level that could reverse the breakout
- Daily P&L already close to stop-loss ($150) — reduce aggression
- Pre-market gap already extended — mean-reversion risk on further move

Rules:
- HOLD is a legitimate vote — use it when a signal is ambiguous, not just when you strongly disagree
- A BUY_CALL_SPREAD vote when the signal says PUT (or vice versa) counts as a veto — use only for clear misdirection
- entry_window must match the window provided in the market context
- spread_width: use 5.0 unless you have a specific reason to narrow or widen"""


def build_options_prompt(
    ctx: "MarketContext",
    signal: "StrategySignal",
    chain_summary: dict,
    daily_realized_pnl: float,
    trades_today: int,
    max_trades: int,
    daily_target: float,
    buying_power: float,
) -> str:
    """Build the options consensus prompt for both LLMs.

    Args:
        ctx: Current market context (VIX, ORB, VWAP, trend).
        signal: The mechanical strategy signal that triggered this cycle.
        chain_summary: Dict with available strikes near ATM.
        daily_realized_pnl: Today's realized P&L so far.
        trades_today: Number of trades already taken today.
        max_trades: Max trades allowed today.
        daily_target: Dollar target for the day.
        buying_power: Current available buying power.

    Returns:
        Formatted prompt string.
    """
    orb_str = (
        f"ORB High: ${ctx.orb_high:.2f} | ORB Low: ${ctx.orb_low:.2f}"
        if ctx.orb_high and ctx.orb_low
        else "ORB: Not yet formed"
    )
    above_str = ""
    if ctx.orb_formed and ctx.orb_high and ctx.orb_low:
        if ctx.above_orb:
            pct = (ctx.underlying_price - ctx.orb_high) / ctx.orb_high * 100
            above_str = f"→ Price is {pct:.2f}% ABOVE ORB high (bullish breakout)"
        elif ctx.below_orb:
            pct = (ctx.orb_low - ctx.underlying_price) / ctx.orb_low * 100
            above_str = f"→ Price is {pct:.2f}% BELOW ORB low (bearish breakdown)"
        else:
            above_str = "→ Price is inside the opening range (no breakout yet)"

    vwap_str = f"${ctx.vwap:.2f}" if ctx.vwap else "N/A"

    # Format strike ladder
    direction = signal.direction
    legs = chain_summary.get("calls" if direction == "CALL" else "puts", [])
    if legs:
        atm_strikes = sorted(legs, key=lambda s: abs(s["strike"] - ctx.underlying_price))[:6]
        strike_lines = ["| Strike | OCC Symbol |"]
        strike_lines.append("|--------|------------|")
        for s in sorted(atm_strikes, key=lambda x: x["strike"]):
            strike_lines.append(f"| ${s['strike']:.2f} | {s['occ_symbol']} |")
        strike_table = "\n".join(strike_lines)
    else:
        strike_table = "Strike data unavailable."

    pnl_pct = (daily_realized_pnl / daily_target * 100) if daily_target > 0 else 0

    prompt = f"""## Market Context

Symbol: {ctx.symbol}
Current Price: ${ctx.underlying_price:.2f}
Intraday Trend: {ctx.trend_bias}
VWAP: {vwap_str}

VIX: {ctx.vix:.1f} ({ctx.market_regime})
1-Day Change: {ctx.spy_1d_change_pct:+.2f}%

{orb_str}
{above_str}

## Mechanical Signal

Strategy: {signal.strategy}
Hypothesized Direction: {direction} ({"call debit spread" if direction == "CALL" else "put debit spread"})
Signal Reason: {signal.entry_reason}
Entry Window: {signal.strategy.lower().replace("_", "")} (suggested: {"morning_orb" if signal.strategy == "ORB" else "midday_reversion" if signal.strategy == "MEAN_REVERSION" else "pre_close"})

## Available Strikes (nearest ATM — {direction}s, {chain_summary.get("dte", "0")}DTE)

{strike_table}

Expiry: {chain_summary.get("expiry", "N/A")}

## Daily Progress

Realized P&L: ${daily_realized_pnl:.2f} / ${daily_target:.2f} target ({pnl_pct:.0f}%)
Trades today: {trades_today} / {max_trades}
Buying Power: ${buying_power:,.2f}

## Your Task

Evaluate this {direction} spread signal and respond with:
- action: BUY_CALL_SPREAD, BUY_PUT_SPREAD, or HOLD
- symbol: {ctx.symbol}
- confidence: 0.0–1.0
- spread_width: suggested dollar width (default 5.0 for {ctx.symbol})
- target_dte: 0 or 1
- entry_window: morning_orb / midday_reversion / pre_close
- reasoning: 2-sentence max

Only use BUY_CALL_SPREAD or BUY_PUT_SPREAD — not both. HOLD means skip this trade.
"""
    return prompt


# ---------------------------------------------------------------------------
# Legacy equity prompts — kept for backward compatibility with existing tests
# ---------------------------------------------------------------------------

import yfinance as yf  # noqa: E402


def fetch_candidate_data(symbols: list[str]) -> list[dict]:
    """Fetch basic market data for each candidate ticker via yfinance."""
    results = []
    for sym in symbols:
        try:
            info = yf.Ticker(sym).fast_info
            history = yf.Ticker(sym).history(period="5d")
            pct_5d = None
            if len(history) >= 2:
                pct_5d = (history["Close"].iloc[-1] / history["Close"].iloc[0] - 1) * 100
            results.append({
                "symbol": sym,
                "price": getattr(info, "last_price", None),
                "market_cap": getattr(info, "market_cap", None),
                "volume": getattr(info, "three_month_average_volume", None),
                "pct_5d": pct_5d,
            })
        except Exception as e:
            logger.warning("Could not fetch data for {}: {}", sym, e)
            results.append({"symbol": sym, "price": None, "market_cap": None, "volume": None, "pct_5d": None})
    return results


BASE_SYSTEM = """You are an expert swing trader and financial analyst. Your role is to analyze \
stock price data, charts and technical indicators that I provide to help identify promising \
swing trading opportunities. Focus on finding trades with a favorable risk-to-reward ratio \
that can be held for several days to weeks. Provide analysis of key support/resistance levels, \
momentum indicators, and chart patterns that signal potential swing trade setups. Prefer simple, \
directional trades rather than complex options strategies.

Pay special attention to:
* Technical chart patterns and price action
* Volume analysis
* Key moving averages (e.g., 20, 50, 200-day)
* Relative strength compared to market
* Potential catalysts for price movement"""

BULL_SYSTEM = BASE_SYSTEM + """

You are also an aggressive micro-cap equity analyst. Your mandate is to find trades — \
analysis paralysis costs money. From the candidate list, SELECT THE TOP 3-5 highest \
conviction BUY opportunities and recommend them. You MUST recommend at least 1 BUY \
unless the evidence is overwhelmingly against it. Use HOLD only for positions you \
already own that should be kept. Use SELL only for positions that are breaking down. \
Only use actions BUY, SELL, or HOLD — never BUY_PUT."""

BEAR_SYSTEM = BASE_SYSTEM + """

You are also a skeptical risk analyst reviewing a micro-cap equity portfolio. Your job is to \
find REASONS NOT TO TRADE. For every potential buy, articulate the downside risks. For every \
held position, evaluate whether the stop-loss should trigger. You should be biased toward \
caution -- if there is a reasonable case to HOLD or SELL, make it. However, if a position \
truly has strong fundamentals, acknowledge it honestly with appropriate confidence.

When you have HIGH conviction (confidence >= 0.75) that a candidate will DECLINE, use \
action BUY_PUT instead of SELL. BUY_PUT means we will buy a 30-day put option to profit \
from the expected decline — it is a directional bearish trade, not a short sale. \
Only recommend tickers from the provided candidate list."""

DISCOVERY_SYSTEM = """You are a micro-cap equity scout. Your job is to identify 3-5 promising
micro-cap stocks (market cap < $300M) that are NOT already in the portfolio or watchlist.
Focus on sectors with catalyst potential: biotech (FDA decisions, trial results),
technology (emerging small-caps with revenue growth), and special situations.

For each suggestion, provide the ticker symbol, a BUY action, a confidence score (0.6-0.9),
a stop_loss_pct (0.05-0.20), and brief reasoning. Only suggest stocks listed on major US
exchanges (NYSE, NASDAQ). Do NOT suggest OTC or pink sheet stocks."""


def build_discovery_prompt(
    positions: list[dict],
    watchlist: list[str],
    buying_power: float,
) -> str:
    if positions:
        lines = ["| Symbol | Shares | Avg Price | Market Value |"]
        lines.append("|--------|--------|-----------|--------------|")
        for p in positions:
            lines.append(
                f"| {p['symbol']} | {p['shares']:.0f} | "
                f"${p['price']:.2f} | ${p['market_value']:.2f} |"
            )
        holdings_table = "\n".join(lines)
    else:
        holdings_table = "No current holdings."

    watchlist_str = ", ".join(watchlist) if watchlist else "None"

    return f"""## Current Portfolio (DO NOT suggest these)

{holdings_table}

## Active Watchlist (DO NOT suggest these)

{watchlist_str}

## Available Capital

Buying Power: ${buying_power:,.2f}

## Task

Suggest 3-5 NEW micro-cap tickers NOT in the portfolio or watchlist above.
Focus on biotech, tech, or special situation catalysts. Every recommendation
must have action=BUY, a confidence score, a stop_loss_pct, and reasoning.
Only suggest tickers on NYSE or NASDAQ — no OTC or pink sheets.
"""


def build_user_prompt(
    positions: list[dict],
    buying_power: float,
    candidates: list[str],
) -> str:
    if positions:
        lines = ["| Symbol | Shares | Avg Price | Market Value |"]
        lines.append("|--------|--------|-----------|--------------|")
        for p in positions:
            lines.append(
                f"| {p['symbol']} | {p['shares']:.0f} | "
                f"${p['price']:.2f} | ${p['market_value']:.2f} |"
            )
        holdings_table = "\n".join(lines)
    else:
        holdings_table = "No current holdings."

    from src.config import get_settings
    raw_data = fetch_candidate_data(candidates)
    candidate_data = sorted(
        raw_data,
        key=lambda c: c["pct_5d"] if c["pct_5d"] is not None else -999,
        reverse=True,
    )[: get_settings().max_candidates_per_cycle]

    if candidate_data:
        lines = ["| Symbol | Price | Market Cap | Avg Volume | 5d Change |"]
        lines.append("|--------|-------|------------|------------|-----------|")
        for c in candidate_data:
            price = f"${c['price']:.2f}" if c["price"] else "N/A"
            mcap = f"${c['market_cap']/1e6:.0f}M" if c["market_cap"] else "N/A"
            vol = f"{c['volume']:,.0f}" if c["volume"] else "N/A"
            chg = f"{c['pct_5d']:+.1f}%" if c["pct_5d"] is not None else "N/A"
            lines.append(f"| {c['symbol']} | {price} | {mcap} | {vol} | {chg} |")
        candidate_table = "\n".join(lines)
    else:
        candidate_table = "None"

    return f"""## Current Portfolio

{holdings_table}

## Available Capital

Buying Power: ${buying_power:,.2f}

## Candidate Tickers for Analysis

{candidate_table}

## Rules

- Full shares only (no fractional shares)
- Every position MUST have a stop-loss between 5% and 25%
- Only recommend tickers from the candidate list above
- You MUST provide a recommendation for EVERY ticker in the table above
- Confidence scores must be between 0.0 and 1.0
- The goal is to FIND TRADES — defaulting to HOLD on everything is not acceptable
"""
