# Phase 2: Execution and Intelligence - Context

**Gathered:** 2026-03-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Implement live order execution via tastytrade (limit orders with dry_run validation), multi-LLM consensus engine (GPT-4 + Claude with adversarial bull/bear prompts), and confidence-tiered position sizing. This is the core upgrade that removes the human from the trading loop. No scheduling or cycle orchestration — that's Phase 3.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
User opted for Claude to make all implementation decisions. The following areas are open for research and planning to determine the best approach:

**Adversarial Prompting Strategy:**
- How to structure bull/bear role assignment (which model gets which role, rotation, or random)
- Prompt template design (what portfolio state, market data, and instructions each model receives)
- How to handle the existing `simple_automation.py` prompt pattern — extend or replace
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
- Both models say SELL same ticker → execute sell
- Both say BUY different tickers → treat as disagreement (HOLD) or execute both?
- One BUY one HOLD → HOLD (veto system)
- Both say HOLD → no action, log reasoning
- Confidence averaging vs minimum for threshold check
- What happens when one LLM API call fails (abort cycle, not single-model fallback per research)

**Position Sizing:**
- Kelly-inspired formula: high conviction (>=0.75) → 40% buying power, normal (>=0.6) → 20%
- $50 minimum trade size (commission protection)
- How to handle fractional shares if tastytrade supports them
- Whether to round down to whole shares or use notional orders

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### tastytrade Order Execution
- `.planning/research/STACK.md` — SDK v12.2.0 order types (LIMIT, STOP, GTC), dry_run preview, NewOrder/Leg API
- `.planning/research/FEATURES.md` — Feature 4 (order execution), Feature 13 (native GTC stops), Feature 12 (position sizing)
- `.planning/research/PITFALLS.md` — Limit-only orders, spread-check gate, PDT risk, symbol hallucination

### Multi-LLM Consensus
- `.planning/research/FEATURES.md` — Feature 5 (Claude API), Feature 6 (consensus engine), Feature 11 (confidence scoring)
- `.planning/research/ARCHITECTURE.md` — LLM Council pattern, Risk Manager owns sizing, strict consensus (no single-model fallback)
- `.planning/research/PITFALLS.md` — LLM training bias overlap, adversarial prompting requirement, Pydantic validation of LLM output

### Existing Code
- `src/broker.py` — TastytradeClient with persistent event loop, AccountSnapshot, sync_positions_to_db
- `src/config.py` — Settings with tt_client_id, tt_secret, tt_refresh, openai_api_key, anthropic_api_key
- `src/otc_filter.py` — OTC validation (VALID_EXCHANGES frozenset)
- `src/pdt.py` — PDT counter (safe limit = 2)
- `src/db.py` — SQLite connection manager, get_db()
- `src/models.py` — Position, Trade, DailySnapshot dataclasses
- `simple_automation.py` — Existing GPT-4 prompt pattern (generate_trading_prompt, parse_llm_response)

### Phase 1 Decisions (carry forward)
- `.planning/phases/01-foundation/01-CONTEXT.md` — Async pattern, .env credentials, SQLite as source of truth

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/broker.py` TastytradeClient — Add `place_order()` and `place_stop_order()` methods using same `_run()` pattern
- `src/broker.py` AccountSnapshot — Already has positions, balances, buying_power for sizing calculations
- `src/otc_filter.py` — Validate LLM-proposed tickers before any order attempt
- `src/pdt.py` — Check PDT counter before executing any buy that could become a same-day sell
- `src/config.py` Settings — Already has `openai_api_key` and `anthropic_api_key` fields
- `simple_automation.py` `generate_trading_prompt()` — Base prompt structure to adapt for adversarial roles
- `simple_automation.py` `parse_llm_response()` — JSON extraction pattern (try json.load, fallback regex)

### Established Patterns
- Sync facade over async SDK via `_run()` / `run_until_complete()` on persistent event loop
- Pydantic Settings for all config from .env
- Loguru for structured logging
- SQLite for state persistence (trades table ready for recording executed orders)

### Integration Points
- `src/broker.py` — Extend with order placement methods
- `src/db.py` — Write trade records after execution
- `src/cli.py` — Wire consensus + execution into the `--dry-run` flow
- New modules needed: `src/consensus.py`, `src/sizing.py`, `src/orders.py`

</code_context>

<specifics>
## Specific Ideas

No specific requirements — Claude has full discretion on implementation approach guided by research findings.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 02-execution-and-intelligence*
*Context gathered: 2026-03-19*
