"""Bull/bear prompt templates for the multi-LLM consensus engine.

GPT-5.4-mini receives the BULL role (biased toward action).
Claude Sonnet 4.6 receives the BEAR role (biased toward caution).
"""
from __future__ import annotations

BULL_SYSTEM = """You are an aggressive micro-cap equity analyst looking for high-conviction
buying opportunities. Your job is to find the BEST opportunities in the current portfolio
and market. You should be biased toward action -- if there is a reasonable case to BUY,
make it. However, you must still provide honest confidence scores and stop-loss levels.
Only recommend tickers from the provided candidate list."""

BEAR_SYSTEM = """You are a skeptical risk analyst reviewing a micro-cap equity portfolio.
Your job is to find REASONS NOT TO TRADE. For every potential buy, articulate the downside
risks. For every held position, evaluate whether the stop-loss should trigger. You should
be biased toward caution -- if there is a reasonable case to HOLD or SELL, make it.
However, if a position truly has strong fundamentals, acknowledge it honestly with
appropriate confidence. Only recommend tickers from the provided candidate list."""


def build_user_prompt(
    positions: list[dict],
    buying_power: float,
    candidates: list[str],
) -> str:
    """Build the user prompt sent to both LLMs.

    Args:
        positions: List of position dicts with keys: symbol, shares, price, market_value
        buying_power: Available cash for new positions
        candidates: Ticker symbols to analyse

    Returns:
        Formatted prompt string with portfolio state, buying power, and candidates.
    """
    # Build holdings table
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

    candidate_list = ", ".join(candidates) if candidates else "None"

    prompt = f"""## Current Portfolio

{holdings_table}

## Available Capital

Buying Power: ${buying_power:,.2f}

## Candidate Tickers for Analysis

{candidate_list}

## Rules

- Full shares only (no fractional shares)
- Micro-cap focus (< $300M market cap)
- Every position MUST have a stop-loss
- Only recommend tickers from the candidate list above
- Provide confidence scores and reasoning for every recommendation
"""
    return prompt
