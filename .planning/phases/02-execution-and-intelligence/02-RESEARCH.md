# Phase 2: Execution and Intelligence - Research

**Researched:** 2026-03-19
**Domain:** Order execution (tastytrade SDK), multi-LLM consensus engine, position sizing
**Confidence:** HIGH

## Summary

Phase 2 transforms the bot from a read-only brokerage client into a live trading system. Three modules must be built: (1) an order execution layer that places limit orders and companion GTC stop orders via the tastytrade SDK, (2) a multi-LLM consensus engine that queries both GPT-4's successor and Claude with adversarial bull/bear prompts and requires agreement before any trade, and (3) a confidence-tiered position sizing module.

The tastytrade SDK v12.2.0 provides all needed order primitives: `NewOrder` with `OrderType.LIMIT` for entries, `OrderType.STOP` with `stop_trigger` for protective stops, `OrderTimeInForce.GTC` for overnight protection, `NewComplexOrder` for OTOCO (buy + take-profit + stop-loss as a single atomic submission), and `dry_run=True` for pre-flight validation. The existing `TastytradeClient` in `src/broker.py` already has the async-to-sync facade pattern; new methods follow the same `_run()` pattern.

For LLM integration, both OpenAI and Anthropic now support native structured output with Pydantic models. OpenAI uses `client.chat.completions.parse(response_format=MyModel)` and Anthropic uses `client.messages.parse(output_format=MyModel)`. This eliminates the fragile regex-based JSON extraction in `simple_automation.py`. The current production model IDs are `gpt-5.4-mini` (OpenAI) and `claude-sonnet-4-6` (Anthropic) -- both cost-effective and sufficient for structured trading analysis.

**Primary recommendation:** Use Pydantic `BaseModel` for all LLM response schemas, native structured output from both SDKs (no regex parsing), OTOCO complex orders for atomic buy+stop placement, and the `DXLinkStreamer` for live bid/ask spread checks before order submission.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
No locked decisions -- user opted for Claude's discretion on all implementation choices.

### Claude's Discretion
User opted for Claude to make all implementation decisions. The following areas are open:

**Adversarial Prompting Strategy:**
- How to structure bull/bear role assignment (which model gets which role, rotation, or random)
- Prompt template design (what portfolio state, market data, and instructions each model receives)
- How to handle the existing `simple_automation.py` prompt pattern -- extend or replace
- Whether to use structured output (JSON mode / function calling) vs free-form with parsing

**Order Mechanics:**
- Limit order pricing strategy (at bid, at ask, midpoint, or offset from last price)
- Spread threshold for rejecting illiquid tickers before order attempt
- GTC stop companion order timing (immediate after buy fill, or same API call)
- How `dry_run` mode interacts with order placement (tastytrade SDK has `dry_run=True` preview)

**Ticker Selection:**
- Source of candidate tickers for buy recommendations (existing portfolio, watchlist, LLM-proposed, or pre-screened)
- Whether to pass a candidate list to LLMs or let them propose freely (with symbol validation after)
- How to handle LLM-hallucinated tickers (validation via OTC filter + tastytrade symbol lookup)

**Consensus Edge Cases:**
- Both models say SELL same ticker -> execute sell
- Both say BUY different tickers -> treat as disagreement (HOLD) or execute both?
- One BUY one HOLD -> HOLD (veto system)
- Both say HOLD -> no action, log reasoning
- Confidence averaging vs minimum for threshold check
- What happens when one LLM API call fails (abort cycle, not single-model fallback per research)

**Position Sizing:**
- Kelly-inspired formula: high conviction (>=0.75) -> 40% buying power, normal (>=0.6) -> 20%
- $50 minimum trade size (commission protection)
- How to handle fractional shares if tastytrade supports them
- Whether to round down to whole shares or use notional orders

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| BROK-04 | System places limit orders with dry_run validation before submission | tastytrade SDK `NewOrder` with `OrderType.LIMIT`, `dry_run=True` on `place_order()`, `DXLinkStreamer` for spread check |
| BROK-05 | System files companion GTC stop orders on every new buy for overnight protection | `NewComplexOrder` OTOCO pattern OR sequential `OrderType.STOP` + `OrderTimeInForce.GTC` + `stop_trigger` |
| AIDC-01 | System queries both GPT-4 and Claude with adversarial prompts (bull/bear) | OpenAI `gpt-5.4-mini` + Anthropic `claude-sonnet-4-6`; structured output via `parse()` on both SDKs |
| AIDC-02 | Both models must agree on action for trade to execute (veto consensus) | Consensus matcher compares `TradeRecommendation` objects; disagreement = HOLD |
| AIDC-03 | Both models must report confidence >= 0.6 for trade to proceed | Pydantic schema enforces `confidence: float` field with `ge=0.0, le=1.0`; threshold check in consensus logic |
| AIDC-04 | Disagreements default to HOLD with full logging of each model's reasoning | `llm_audit` and `consensus_decisions` tables already in SQLite schema; log raw responses always |
| SIZE-01 | Position sizing computed programmatically from confidence scores and buying power | Sizing module takes `buying_power`, `min(confidence_gpt, confidence_claude)`, outputs share count |
| SIZE-02 | High conviction (>= 0.75) allows up to 40% of buying power per trade | Tiered sizing: `if min_confidence >= 0.75: max_pct = 0.40` |
| SIZE-03 | Normal conviction (>= 0.6) allows up to 20% of buying power per trade | Tiered sizing: `elif min_confidence >= 0.60: max_pct = 0.20` |
| SIZE-04 | No trade smaller than $50 (commission protection) | Floor check: `if notional < 50.0: reject trade` |
</phase_requirements>

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| tastytrade (tastyware) | 12.2.0 | Order placement, stop orders, quote streaming | Already in project; `NewOrder`, `NewComplexOrder`, `DXLinkStreamer` provide all needed primitives |
| openai | >=2.29.0 | GPT-5.4-mini structured trading analysis | Already in project; `client.chat.completions.parse()` gives native Pydantic output |
| anthropic | >=0.86.0 | Claude Sonnet 4.6 adversarial consensus | Already in project; `client.messages.parse()` gives native Pydantic output |
| pydantic | 2.x (via pydantic-settings) | LLM response validation, trade recommendation schemas | Already a dependency; both LLM SDKs integrate natively |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| loguru | >=0.7.0 | Structured logging for all consensus and order events | Already in project; every LLM call and order must be logged |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Native structured output | instructor library | Adds dependency; both SDKs now have built-in Pydantic support |
| OTOCO complex order | Sequential buy then stop | OTOCO is atomic (stop guaranteed if buy fills); sequential risks gap between fill and stop placement |
| gpt-5.4-mini | gpt-5.4 (full) | Full model 3-5x more expensive; mini sufficient for structured trading analysis |
| claude-sonnet-4-6 | claude-haiku-4-5 | Haiku is cheaper but less capable at nuanced financial reasoning; Sonnet is the right balance |

**Installation:**
```bash
# All dependencies already in pyproject.toml -- no new packages needed
uv sync
```

**Version verification:** All versions confirmed current via PyPI and official docs as of 2026-03-19.

## Architecture Patterns

### Recommended Project Structure
```
src/
  consensus.py       # Multi-LLM consensus engine (AIDC-01 through AIDC-04)
  sizing.py           # Position sizing module (SIZE-01 through SIZE-04)
  orders.py           # Order execution layer (BROK-04, BROK-05)
  broker.py           # Existing -- extend with place_order, place_stop, get_quote
  models.py           # Existing -- add TradeRecommendation, ConsensusDecision, OrderResult
  prompts.py          # Bull/bear prompt templates (replaces simple_automation.py patterns)
  config.py           # Existing -- add consensus/sizing config fields
  db.py               # Existing -- no schema changes needed (tables already exist)
  otc_filter.py       # Existing -- called to validate LLM-proposed tickers
  pdt.py              # Existing -- called before any buy execution
```

### Pattern 1: Adversarial Bull/Bear Prompting

**What:** Assign GPT-5.4-mini the BULL role (argue for buying opportunities, optimistic framing) and Claude Sonnet 4.6 the BEAR role (argue for risks, pessimistic framing). Both receive identical portfolio state and market data, but different system prompts that prime their analytical lens.

**When to use:** Every consensus cycle. Fixed role assignment (not rotating) because it simplifies debugging and prompt tuning.

**Why this assignment:** GPT models tend toward confident, assertive recommendations. Claude models tend toward cautious, hedged analysis. Assigning GPT=BULL and Claude=BEAR amplifies their natural tendencies, maximizing the chance that genuine disagreement surfaces. If both agree to BUY despite Claude being primed to find risks, that is a stronger signal than two models primed the same way agreeing.

**Example:**
```python
# Source: Research recommendation based on LLM behavioral patterns
from pydantic import BaseModel, Field
from typing import Literal

class TradeRecommendation(BaseModel):
    """Schema for LLM trading response. Used with native structured output."""
    action: Literal["BUY", "SELL", "HOLD"]
    symbol: str = Field(description="Ticker symbol, e.g. RXRX")
    confidence: float = Field(ge=0.0, le=1.0, description="0.0-1.0 conviction level")
    stop_loss_pct: float = Field(ge=0.01, le=0.50, description="Stop loss as % below entry")
    reasoning: str = Field(max_length=500, description="2-3 sentence rationale")

class TradingAnalysis(BaseModel):
    """Top-level response schema for each LLM."""
    market_assessment: str = Field(max_length=300)
    recommendations: list[TradeRecommendation]

# BULL prompt (GPT-5.4-mini)
BULL_SYSTEM = """You are an aggressive micro-cap equity analyst looking for high-conviction
buying opportunities. Your job is to find the BEST opportunities in the current portfolio
and market. You should be biased toward action -- if there is a reasonable case to BUY,
make it. However, you must still provide honest confidence scores and stop-loss levels.
Only recommend tickers from the provided candidate list."""

# BEAR prompt (Claude Sonnet 4.6)
BEAR_SYSTEM = """You are a skeptical risk analyst reviewing a micro-cap equity portfolio.
Your job is to find REASONS NOT TO TRADE. For every potential buy, articulate the downside
risks. For every held position, evaluate whether the stop-loss should trigger. You should
be biased toward caution -- if there is a reasonable case to HOLD or SELL, make it.
However, if a position truly has strong fundamentals, acknowledge it honestly with
appropriate confidence. Only recommend tickers from the provided candidate list."""
```

### Pattern 2: OTOCO Atomic Order (Buy + Stop)

**What:** Use tastytrade's `NewComplexOrder` to place a limit buy as the trigger order, with a GTC stop as the dependent order. If the buy fills, the stop activates automatically. If the buy is cancelled, the stop never enters the market.

**When to use:** Every new buy order (BROK-04 + BROK-05 combined).

**Example:**
```python
# Source: tastytrade SDK docs (tastyworks-api.readthedocs.io/en/latest/orders.html)
from decimal import Decimal
from tastytrade.instruments import Equity
from tastytrade.order import (
    NewOrder, NewComplexOrder, OrderAction,
    OrderType, OrderTimeInForce
)

symbol = await Equity.get(session, ticker)
opening = symbol.build_leg(Decimal(str(shares)), OrderAction.BUY_TO_OPEN)
closing = symbol.build_leg(Decimal(str(shares)), OrderAction.SELL_TO_CLOSE)

stop_price = Decimal(str(round(limit_price * (1 - stop_loss_pct), 2)))

otoco = NewComplexOrder(
    trigger_order=NewOrder(
        time_in_force=OrderTimeInForce.DAY,
        order_type=OrderType.LIMIT,
        legs=[opening],
        price=-limit_price,  # negative = debit
    ),
    orders=[
        NewOrder(
            time_in_force=OrderTimeInForce.GTC,
            order_type=OrderType.STOP,
            legs=[closing],
            stop_trigger=stop_price,
        ),
    ],
)

# Dry run first
dry_response = await account.place_complex_order(session, otoco, dry_run=True)
# Then execute
response = await account.place_complex_order(session, otoco, dry_run=False)
```

### Pattern 3: Spread-Check Gate

**What:** Before placing any order, fetch a live quote via `DXLinkStreamer` and reject the trade if the bid-ask spread exceeds a threshold (recommended: 5% of mid-price for micro-caps).

**When to use:** Before every order submission.

**Example:**
```python
# Source: tastytrade SDK docs (data-streamer.html)
from tastytrade import DXLinkStreamer
from tastytrade.dxfeed import Quote

async def get_spread(session, ticker: str) -> tuple[float, float, float]:
    """Get bid, ask, spread_pct for a ticker."""
    async with DXLinkStreamer(session) as streamer:
        await streamer.subscribe(Quote, [ticker])
        async for quote in streamer.listen(Quote):
            if quote.event_symbol == ticker:
                bid = float(quote.bid_price)
                ask = float(quote.ask_price)
                mid = (bid + ask) / 2
                spread_pct = (ask - bid) / mid if mid > 0 else float('inf')
                return bid, ask, spread_pct

MAX_SPREAD_PCT = 0.05  # 5% -- reject if wider

bid, ask, spread_pct = await get_spread(session, ticker)
if spread_pct > MAX_SPREAD_PCT:
    logger.warning("Rejecting {}: spread {:.1%} exceeds {:.1%} threshold",
                   ticker, spread_pct, MAX_SPREAD_PCT)
    # Do not place order
```

### Pattern 4: Structured Output with Both SDKs

**What:** Use native Pydantic integration in both OpenAI and Anthropic SDKs to guarantee schema-compliant responses. No regex parsing, no markdown stripping.

**Example:**
```python
# OpenAI -- GPT-5.4-mini with structured output
from openai import OpenAI

openai_client = OpenAI(api_key=settings.openai_api_key)
completion = openai_client.chat.completions.parse(
    model="gpt-5.4-mini",
    messages=[
        {"role": "system", "content": BULL_SYSTEM},
        {"role": "user", "content": user_prompt},
    ],
    response_format=TradingAnalysis,
    temperature=0.3,
    max_tokens=2000,
)
bull_analysis = completion.choices[0].message.parsed

# Anthropic -- Claude Sonnet 4.6 with structured output
import anthropic

anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
response = anthropic_client.messages.parse(
    model="claude-sonnet-4-6",
    max_tokens=2000,
    system=BEAR_SYSTEM,
    messages=[{"role": "user", "content": user_prompt}],
    output_format=TradingAnalysis,
)
bear_analysis = response.parsed_output
```

### Anti-Patterns to Avoid

- **Single-Model Fallback:** If one LLM call fails, abort the entire consensus cycle. Do NOT fall back to single-model decisions. Log the failure and exit cleanly. Existing positions and stops are unaffected.
- **LLM-Determined Position Sizes:** Never let the LLM output share counts. LLM provides action + confidence. The sizing module computes shares from buying power, confidence, and price.
- **Regex JSON Parsing:** The `simple_automation.py` pattern of `re.search(r'\{.*\}', response, re.DOTALL)` is fragile. Replace entirely with native structured output. The old code can be archived.
- **Market Orders:** Never use `OrderType.NOTIONAL_MARKET` on micro-caps. Spreads of 5-20% make market orders destructive on a small account.
- **Ignoring dry_run:** Always call with `dry_run=True` first to validate buying power and fees before the real submission.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| LLM JSON parsing | Regex extraction + json.loads | `client.parse()` with Pydantic model | Both SDKs guarantee schema compliance; regex breaks on markdown fences, truncation |
| Atomic buy+stop | Sequential order placement | tastytrade `NewComplexOrder` OTOCO | Eliminates race condition between buy fill and stop placement |
| Bid/ask spread data | Cached price from yfinance | `DXLinkStreamer` live quote | yfinance data is 15-min delayed; need real-time spread for limit pricing |
| Confidence validation | Manual float range checks | Pydantic `Field(ge=0.0, le=1.0)` | Declarative, tested, impossible to forget |
| Symbol validation | String matching against hardcoded list | `Equity.get(session, ticker)` + `otc_filter` | SDK call confirms symbol exists in tastytrade's universe |

**Key insight:** Both LLM SDKs now support native Pydantic structured output. This single capability eliminates the most fragile part of the existing system (JSON parsing from free-form LLM responses). Build the Pydantic schemas first; everything else flows from them.

## Common Pitfalls

### Pitfall 1: chatgpt-4o-latest Is Dead
**What goes wrong:** Using the old model ID `chatgpt-4o-latest` or `gpt-4o` results in API errors. These were deprecated February 17, 2026.
**Why it happens:** Training data and existing code reference old model IDs.
**How to avoid:** Use `gpt-5.4-mini` for cost-effective structured analysis. Verify model ID at implementation time via `https://developers.openai.com/api/docs/models`.
**Warning signs:** HTTP 404 or "model not found" errors from OpenAI API.

### Pitfall 2: Claude 3.5 Sonnet Is Retired
**What goes wrong:** Using `claude-3-5-sonnet-20240620` or `claude-3-5-sonnet-latest` returns errors. Claude 3.5 models have been retired.
**Why it happens:** Many tutorials and older code reference Claude 3.5 Sonnet.
**How to avoid:** Use `claude-sonnet-4-6` (current production Sonnet). For budget-constrained use, `claude-haiku-4-5` is available but less capable.
**Warning signs:** API errors referencing invalid model ID.

### Pitfall 3: Price Sign Convention in tastytrade Orders
**What goes wrong:** Passing a positive price for a buy order results in unexpected behavior. The tastytrade SDK uses a sign convention where negative = debit (you pay), positive = credit (you receive).
**Why it happens:** Unintuitive for equity orders where you always think in positive dollar amounts.
**How to avoid:** For limit buy orders, always negate the price: `price=Decimal('-10')` means a $10/share debit. For sell orders (credits), use positive price.
**Warning signs:** Order rejected with "invalid price effect" or order executes at unexpected price.

### Pitfall 4: Spread Slippage on Micro-Caps
**What goes wrong:** Placing a limit order at the last traded price on an illiquid micro-cap. The last price may be stale; the current ask is much higher.
**Why it happens:** yfinance "close" price does not reflect current order book state.
**How to avoid:** Always fetch live bid/ask via `DXLinkStreamer` before pricing. Place limit buys at or slightly above mid-price, not at last price. Reject if spread > 5%.
**Warning signs:** Orders sitting unfilled for hours, or fills at unexpected prices.

### Pitfall 5: Both LLMs Agree Because Both Are Wrong
**What goes wrong:** GPT and Claude both recommend BUY on the same ticker with high confidence, but both are reasoning from the same stale training data or the same surface-level pattern.
**Why it happens:** Consensus between models trained on similar data is not independence. Agreement does not equal correctness.
**How to avoid:** Adversarial prompting (bull/bear roles) forces surface disagreement. Use minimum confidence (not average) as the threshold. Supply verified live data (prices, volumes) in the prompt so models reason from facts, not memory.
**Warning signs:** 100% agreement rate across multiple cycles. Both models citing the same catalyst or reasoning.

### Pitfall 6: Pydantic Schema Drift Between SDKs
**What goes wrong:** OpenAI's `response_format` and Anthropic's `output_format` handle Pydantic schemas slightly differently. Edge cases (optional fields, Union types, nested models) may work in one SDK but fail in the other.
**Why it happens:** Each SDK has its own JSON schema transformation layer.
**How to avoid:** Keep the `TradingAnalysis` schema simple: use only `str`, `float`, `int`, `list`, `Literal` types. Avoid `Optional`, `Union`, complex nested structures. Test the same schema against both SDKs before integrating.
**Warning signs:** One SDK returns valid output while the other throws schema validation errors.

## Code Examples

### Complete Consensus Engine Flow

```python
# Source: Research synthesis from OpenAI docs, Anthropic docs, tastytrade docs
import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from openai import OpenAI
import anthropic
from loguru import logger

from src.config import get_settings
from src.db import get_db


class TradeRecommendation(BaseModel):
    action: Literal["BUY", "SELL", "HOLD"]
    symbol: str
    confidence: float = Field(ge=0.0, le=1.0)
    stop_loss_pct: float = Field(ge=0.01, le=0.50)
    reasoning: str = Field(max_length=500)

class TradingAnalysis(BaseModel):
    market_assessment: str = Field(max_length=300)
    recommendations: list[TradeRecommendation]


def query_bull(prompt: str) -> TradingAnalysis:
    """Query GPT-5.4-mini with bull role."""
    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)
    completion = client.chat.completions.parse(
        model="gpt-5.4-mini",
        messages=[
            {"role": "system", "content": BULL_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format=TradingAnalysis,
        temperature=0.3,
        max_tokens=2000,
    )
    return completion.choices[0].message.parsed


def query_bear(prompt: str) -> TradingAnalysis:
    """Query Claude Sonnet 4.6 with bear role."""
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.parse(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=BEAR_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=TradingAnalysis,
    )
    return response.parsed_output


def evaluate_consensus(
    bull: TradingAnalysis, bear: TradingAnalysis
) -> list[TradeRecommendation]:
    """Apply veto consensus: both must agree on action+symbol."""
    approved = []
    bull_map = {r.symbol: r for r in bull.recommendations}
    bear_map = {r.symbol: r for r in bear.recommendations}

    all_symbols = set(bull_map.keys()) | set(bear_map.keys())
    for symbol in all_symbols:
        b = bull_map.get(symbol)
        r = bear_map.get(symbol)
        if b is None or r is None:
            logger.info("No consensus on {}: only one model mentioned it", symbol)
            continue
        if b.action != r.action:
            logger.info("Disagreement on {}: bull={}, bear={}", symbol, b.action, r.action)
            continue
        min_conf = min(b.confidence, r.confidence)
        if min_conf < 0.6:
            logger.info("Low confidence on {}: bull={}, bear={}", symbol, b.confidence, r.confidence)
            continue
        # Consensus reached -- use bear's stop_loss (more conservative)
        approved.append(TradeRecommendation(
            action=b.action,
            symbol=symbol,
            confidence=min_conf,
            stop_loss_pct=max(b.stop_loss_pct, r.stop_loss_pct),
            reasoning=f"BULL: {b.reasoning} | BEAR: {r.reasoning}",
        ))
    return approved
```

### Position Sizing

```python
# Source: Research recommendation based on requirements SIZE-01 through SIZE-04
from decimal import Decimal

MIN_TRADE_VALUE = Decimal("50.00")  # SIZE-04
HIGH_CONVICTION_THRESHOLD = 0.75     # SIZE-02
NORMAL_CONVICTION_THRESHOLD = 0.60   # SIZE-03
HIGH_CONVICTION_MAX_PCT = Decimal("0.40")   # SIZE-02
NORMAL_CONVICTION_MAX_PCT = Decimal("0.20") # SIZE-03


def compute_shares(
    buying_power: Decimal,
    price: Decimal,
    confidence: float,
) -> int:
    """Compute number of whole shares to buy based on confidence tier.

    Returns 0 if trade would be below minimum or confidence too low.
    """
    if confidence >= HIGH_CONVICTION_THRESHOLD:
        max_notional = buying_power * HIGH_CONVICTION_MAX_PCT
    elif confidence >= NORMAL_CONVICTION_THRESHOLD:
        max_notional = buying_power * NORMAL_CONVICTION_MAX_PCT
    else:
        return 0  # Below threshold

    if max_notional < MIN_TRADE_VALUE:
        return 0  # Account too small for this confidence level

    shares = int(max_notional / price)  # Round down to whole shares
    notional = Decimal(shares) * price

    if notional < MIN_TRADE_VALUE:
        return 0  # Even 1 share is below minimum

    return shares
```

### Order Execution with Spread Check

```python
# Source: tastytrade SDK docs
from decimal import Decimal
from tastytrade import DXLinkStreamer
from tastytrade.dxfeed import Quote
from tastytrade.instruments import Equity
from tastytrade.order import (
    NewOrder, NewComplexOrder, OrderAction,
    OrderType, OrderTimeInForce,
)

MAX_SPREAD_PCT = 0.05


async def place_buy_with_stop(
    session, account, ticker: str, shares: int,
    stop_loss_pct: float, dry_run: bool = True
) -> dict:
    """Place limit buy + GTC stop as OTOCO. Returns order details."""

    # 1. Spread check
    async with DXLinkStreamer(session) as streamer:
        await streamer.subscribe(Quote, [ticker])
        async for quote in streamer.listen(Quote):
            if quote.event_symbol == ticker:
                bid = float(quote.bid_price)
                ask = float(quote.ask_price)
                mid = (bid + ask) / 2
                spread_pct = (ask - bid) / mid if mid > 0 else float('inf')
                break

    if spread_pct > MAX_SPREAD_PCT:
        return {"status": "rejected", "reason": f"spread {spread_pct:.1%} > {MAX_SPREAD_PCT:.0%}"}

    # 2. Price at midpoint (compromise between fill probability and slippage)
    limit_price = Decimal(str(round(mid, 2)))
    stop_price = Decimal(str(round(mid * (1 - stop_loss_pct), 2)))

    # 3. Build OTOCO
    symbol = await Equity.get(session, ticker)
    opening = symbol.build_leg(Decimal(str(shares)), OrderAction.BUY_TO_OPEN)
    closing = symbol.build_leg(Decimal(str(shares)), OrderAction.SELL_TO_CLOSE)

    otoco = NewComplexOrder(
        trigger_order=NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=[opening],
            price=-limit_price,
        ),
        orders=[
            NewOrder(
                time_in_force=OrderTimeInForce.GTC,
                order_type=OrderType.STOP,
                legs=[closing],
                stop_trigger=stop_price,
            ),
        ],
    )

    response = await account.place_complex_order(session, otoco, dry_run=dry_run)
    return {
        "status": "dry_run" if dry_run else "submitted",
        "ticker": ticker,
        "shares": shares,
        "limit_price": float(limit_price),
        "stop_price": float(stop_price),
        "spread_pct": spread_pct,
    }
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `chatgpt-4o-latest` model ID | `gpt-5.4-mini` | Feb 2026 | Old ID returns 404; must update |
| `claude-3-5-sonnet-20240620` | `claude-sonnet-4-6` | Feb 2026 | Old ID retired; must update |
| Regex JSON extraction from LLM | Native `parse()` with Pydantic | OpenAI 2024, Anthropic 2025 | Eliminates parsing failures entirely |
| Sequential buy then manual stop | OTOCO complex order | tastytrade SDK 10.x+ | Atomic: stop guaranteed on fill |
| yfinance for order pricing | DXLinkStreamer live quotes | Always available | Real-time vs 15-min delayed |

**Deprecated/outdated:**
- `chatgpt-4o-latest`: Removed Feb 17, 2026. Use `gpt-5.4-mini`.
- `claude-3-5-sonnet-*`: Retired. Use `claude-sonnet-4-6`.
- `simple_automation.py` `parse_llm_response()`: Regex-based JSON extraction. Replace with native structured output.
- OpenAI `response_format={"type": "json_object"}`: Old JSON mode. Use `response_format=PydanticModel` via `.parse()` instead.

## Open Questions

1. **OTOCO on micro-caps in tastytrade sandbox**
   - What we know: OTOCO works for liquid stocks (AAPL, SPY examples in docs). The SDK supports it.
   - What's unclear: Whether tastytrade sandbox (cert) environment supports OTOCO for micro-cap symbols that may be missing from cert instrument universe.
   - Recommendation: Test OTOCO in cert with a liquid stock (SPY). For micro-cap validation, use `dry_run=True` against live (not cert) after Phase 1 testing.

2. **DXLinkStreamer connection lifecycle**
   - What we know: The streamer is a websocket that requires `async with` context manager.
   - What's unclear: Whether opening/closing the streamer for each spread check adds unacceptable latency (WebSocket handshake time).
   - Recommendation: Open the streamer once per execution cycle and pass it through. If that is architecturally awkward, the per-check approach adds ~1-2s latency which is acceptable for a daily bot.

3. **Fractional shares on tastytrade**
   - What we know: tastytrade supports `NOTIONAL_MARKET` orders where you specify dollar amount, not share count. The SDK passes `value` instead of building a leg with quantity.
   - What's unclear: Whether notional orders work with LIMIT pricing (the docs only show NOTIONAL_MARKET, not NOTIONAL_LIMIT).
   - Recommendation: Use whole shares only. Round down. This simplifies order construction and avoids edge cases. The $50 minimum trade floor ensures meaningful position sizes.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 0.23.x |
| Config file | none -- see Wave 0 |
| Quick run command | `python -m pytest tests/ -x -q` |
| Full suite command | `python -m pytest tests/ -v` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| BROK-04 | Limit order with dry_run validation | unit (mock SDK) | `python -m pytest tests/test_orders.py::test_limit_order_dry_run -x` | Wave 0 |
| BROK-05 | GTC stop companion on every buy | unit (mock SDK) | `python -m pytest tests/test_orders.py::test_otoco_stop_companion -x` | Wave 0 |
| AIDC-01 | Both LLMs queried with adversarial prompts | unit (mock APIs) | `python -m pytest tests/test_consensus.py::test_both_models_queried -x` | Wave 0 |
| AIDC-02 | Veto consensus -- both must agree | unit | `python -m pytest tests/test_consensus.py::test_veto_consensus -x` | Wave 0 |
| AIDC-03 | Confidence >= 0.6 threshold | unit | `python -m pytest tests/test_consensus.py::test_confidence_threshold -x` | Wave 0 |
| AIDC-04 | Disagreement defaults to HOLD with logging | unit | `python -m pytest tests/test_consensus.py::test_disagreement_hold -x` | Wave 0 |
| SIZE-01 | Sizing from confidence + buying power | unit | `python -m pytest tests/test_sizing.py::test_compute_shares -x` | Wave 0 |
| SIZE-02 | High conviction 40% cap | unit | `python -m pytest tests/test_sizing.py::test_high_conviction_cap -x` | Wave 0 |
| SIZE-03 | Normal conviction 20% cap | unit | `python -m pytest tests/test_sizing.py::test_normal_conviction_cap -x` | Wave 0 |
| SIZE-04 | $50 minimum floor | unit | `python -m pytest tests/test_sizing.py::test_minimum_trade_floor -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/ -x -q`
- **Per wave merge:** `python -m pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/__init__.py` -- package init
- [ ] `tests/conftest.py` -- shared fixtures (mock tastytrade session, mock LLM clients, test DB)
- [ ] `tests/test_consensus.py` -- covers AIDC-01, AIDC-02, AIDC-03, AIDC-04
- [ ] `tests/test_sizing.py` -- covers SIZE-01, SIZE-02, SIZE-03, SIZE-04
- [ ] `tests/test_orders.py` -- covers BROK-04, BROK-05
- [ ] `pytest.ini` or `[tool.pytest.ini_options]` in pyproject.toml -- config

## Sources

### Primary (HIGH confidence)
- [OpenAI Models API docs](https://developers.openai.com/api/docs/models) -- current model IDs: gpt-5.4, gpt-5.4-mini, gpt-5.4-nano
- [OpenAI Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs) -- `client.chat.completions.parse()` with Pydantic
- [Anthropic Models overview](https://platform.claude.com/docs/en/about-claude/models/overview) -- current model IDs: claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5
- [Anthropic Structured Outputs docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) -- `client.messages.parse()` with Pydantic
- [tastytrade SDK Orders docs](https://tastyworks-api.readthedocs.io/en/latest/orders.html) -- NewOrder, NewComplexOrder, OTOCO, dry_run
- [tastytrade SDK Data Streamer docs](https://tastyworks-api.readthedocs.io/en/latest/data-streamer.html) -- DXLinkStreamer, Quote subscription
- [tastytrade SDK GitHub](https://github.com/tastyware/tastytrade) -- v12.2.0, order.py source

### Secondary (MEDIUM confidence)
- [OpenAI deprecation notice](https://developers.openai.com/api/docs/deprecations) -- chatgpt-4o-latest removed Feb 17, 2026
- [OpenAI GPT-4o retirement announcement](https://openai.com/index/retiring-gpt-4o-and-older-models/) -- GPT-5.4 as replacement
- `.planning/research/PITFALLS.md` -- spread-check gate, PDT risk, symbol hallucination (project-specific research)
- `.planning/research/ARCHITECTURE.md` -- LLM Council pattern, anti-patterns (project-specific research)

### Tertiary (LOW confidence)
- None -- all findings verified against primary or secondary sources.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all packages already in pyproject.toml; model IDs verified against official docs today
- Architecture: HIGH -- tastytrade order patterns verified against SDK docs; LLM structured output patterns verified against both official docs
- Pitfalls: HIGH -- model deprecations confirmed; SDK price conventions from official docs; spread risk from PITFALLS.md research

**Research date:** 2026-03-19
**Valid until:** 2026-04-19 (30 days -- LLM model IDs may shift; verify before implementation if delayed)
