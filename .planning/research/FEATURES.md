# Feature Landscape

**Domain:** Autonomous micro-cap equity trading bot (brownfield upgrade)
**Researched:** 2026-03-19
**Overall confidence:** HIGH (tastytrade API well-documented; multi-LLM patterns well-established; risk patterns universal)

---

## Context: Brownfield Starting Point

The existing system already provides these capabilities — they are NOT features to build, they are foundations to preserve:

| Existing Capability | Status | Notes |
|---------------------|--------|-------|
| Market data fetching (Yahoo → Stooq fallback) | Done | `trading_script.py` |
| CSV-based portfolio state with daily P&L | Done | `chatgpt_portfolio_update.csv` |
| Stop-loss enforcement + sell trigger logic | Done | Simulation only, no live execution |
| GPT-4 single-LLM recommendation + JSON parsing | Done | `simple_automation.py` |
| S&P 500 / benchmark comparison | Done | `trading_script.py` |
| Trade logging and LLM audit trail | Done | `chatgpt_trade_log.csv` |
| Weekend/holiday date normalization | Done | `last_trading_date()` |

**Upgrade gap:** Everything existing runs in simulation. Nothing touches a live brokerage. The upgrade is: connect the existing decision layer to a live tastytrade account with real execution, a second LLM, circuit breakers, and notifications.

---

## Table Stakes

Features where absence makes the system broken or unusable. These must work before anything else.

### 1. tastytrade Authentication and Session Management
**Why Expected:** All live trading requires a valid, refreshed session. Without this, zero API calls succeed.
**Complexity:** Low-Medium
**Implementation:** OAuth session via `tastyware/tastytrade` Python SDK (v12.2.0, supports Python >= 3.11). Session creation uses username/password; the SDK handles token refresh. A sandbox account is available for safe testing before live deployment.
**Notes:** Credentials must come from environment variables (`TASTYTRADE_USERNAME`, `TASTYTRADE_PASSWORD`). The SDK provides both sync and async contexts; use `SyncSession` to avoid async complexity given the daily-cycle pattern.

### 2. Live Account Balance and Buying Power Retrieval
**Why Expected:** Position sizing decisions require knowing actual available cash. CSV-tracked cash drifts from reality.
**Complexity:** Low
**Implementation:** `Account.get_balances(session)` → `buying_power` field. Must be fetched at start of every daily cycle and override any locally-tracked value.
**Notes:** For a sub-$1K account, buying power is the binding constraint on every decision. Get it fresh every run, never use a cached/stale value.

### 3. Live Position Sync from tastytrade
**Why Expected:** LLM recommendations must reflect actual held positions, not CSV state. Drift between CSV and live account causes double-buys and phantom sells.
**Complexity:** Low
**Implementation:** `Account.get_positions(session)` returns current holdings with quantity and cost basis. Reconcile against CSV at start of each cycle; CSV becomes a secondary record, not the source of truth.
**Notes:** This is the single most critical architecture change. The live account IS the portfolio state.

### 4. Programmatic Order Execution (Market and Limit Orders)
**Why Expected:** Without execution, the entire upgrade is pointless.
**Complexity:** Medium
**Implementation:** tastytrade SDK supports `LIMIT`, `NOTIONAL_MARKET`, `STOP`, and `OCO/OTOCO` order types with `DAY` and `GTC` time-in-force. Use `dry_run=True` preview before submission to validate buying power impact. Equity orders use `NewOrder` with `Leg` objects.
**Notes:** Always use `dry_run=True` first to validate order before submission — the SDK calculates fees and buying power impact. For a sub-$1K account, use `LIMIT` orders near market to avoid slippage on illiquid micro-caps. `NOTIONAL_MARKET` is available but dangerous on micro-cap spreads.

### 5. Claude API Integration (Second LLM)
**Why Expected:** The project constraint mandates dual-LLM consensus. Single-model decisions are explicitly out of scope.
**Complexity:** Low
**Implementation:** `anthropic` Python SDK. Parallel API calls to both GPT-4 and Claude with identical prompts; compare structured JSON outputs. Use `claude-3-5-sonnet` for cost-effectiveness at this scale.
**Notes:** Both LLM calls should receive the exact same prompt (same portfolio state, same context). Prompt templates should be version-controlled so LLM reasoning can be audited.

### 6. Multi-LLM Consensus Engine (GPT-4 + Claude Agreement Required)
**Why Expected:** This is the core architectural constraint. Without consensus logic, you have two LLMs but no mechanism for resolving disagreement.
**Complexity:** Medium
**Implementation:** Simple two-LLM agreement check: both must recommend the same action (buy/sell/hold) on the same ticker for a trade to execute. Disagreement = no trade. This is a conservative veto system — both must say YES.
**Disagreement handling:** When models disagree, default to HOLD. Log disagreement details (what each model said, confidence scores) for every trade considered. This audit trail is critical for tuning the system.
**Notes:** Do NOT implement debate rounds, weighted voting, or complex ensemble scoring in v1. Research (TradingAgents framework, multi-LLM Medium case studies) shows these add complexity without proportional benefit at this scale. Simple veto is the right starting point. The consensus threshold can be loosened later if HOLD frequency is too high.

### 7. Daily Autonomous Trading Cycle (Cron-Schedulable)
**Why Expected:** The system must run without human trigger. A daily script that requires manual invocation is semi-automation, not autonomy.
**Complexity:** Low
**Implementation:** Single entry-point Python script that executes a complete cycle: authenticate → sync positions → fetch prices → call both LLMs → evaluate consensus → execute approved trades → enforce stop-losses → send notifications → log everything. Schedulable via `cron` or `launchd`.
**Notes:** Exit codes matter for cron monitoring. Exit 0 = success (even if no trades). Exit 1 = error requiring attention. Include a `--dry-run` flag that runs the full cycle but skips order submission.

### 8. Stop-Loss Enforcement Against Live Positions
**Why Expected:** The existing system enforces stop-losses in simulation. Against live positions, stop-losses must trigger real sell orders.
**Complexity:** Medium
**Implementation:** At start of each cycle, compare every live position's current price against its stop-loss price (stored in trade log). If breached, submit a limit sell order at or near market before any other action. Stop-loss check runs before LLM calls — do not waste API calls on positions that should be exited.
**Notes:** Stop-loss prices should live in the trade log CSV (already exists) plus a lightweight local state file. tastytrade also supports native stop orders (`STOP` order type) that trigger server-side — consider native stops as a safety net against cycle failures.

### 9. Circuit Breakers (Daily Loss Limit + Max Drawdown Halt)
**Why Expected:** Without circuit breakers, a malfunctioning consensus engine or bad market day can drain the account before any human notices.
**Complexity:** Low-Medium
**Implementation:** Two checks before any trade execution:
- **Daily loss limit:** If account value has dropped X% from the opening balance of this cycle, halt all trading and send alert. Recommended: 10% for a sub-$1K account (aggressive but accounts for normal micro-cap volatility).
- **Max drawdown halt:** If account value has dropped X% from the all-time high (tracked across cycles), halt trading and require manual reset. Recommended: 30%.
**Notes:** Circuit breakers must persist state across runs. Store the "session opening balance" and "all-time high" in a state file. When a circuit breaker trips, the system must not auto-reset — require a manual flag or environment variable override to resume.

---

## Differentiators

Features that increase alpha or system robustness, but where absence doesn't break core function.

### 10. Biotech Catalyst Context Injection
**Why Valuable:** The existing experiment's ~32% return was driven by biotech catalyst timing (FDA decisions, Phase 3 readouts, PDUFA dates). Injecting upcoming catalyst dates into LLM prompts dramatically improves recommendation quality.
**Complexity:** Medium
**Implementation:** Integrate BioPharmCatalyst or CatalystAlert API/scrape into the daily prompt. For each held position and each candidate ticker, include upcoming catalyst dates and event type in the LLM context window.
**Dependencies:** Requires Feature 6 (consensus engine) — catalyst context goes into both LLM prompts simultaneously.
**Notes:** This is what elevates this from a generic trading bot to a biotech-specialist bot. It's the key reason the experiment outperformed. Worth building in Phase 2. MEDIUM confidence on API availability — verify BioPharmCatalyst terms of service before automating.

### 11. Structured LLM Prompt Schema with Confidence Scoring
**Why Valuable:** The existing prompt returns a free-form JSON. A stricter schema with required confidence scores allows the consensus engine to gate on certainty, not just direction.
**Complexity:** Low
**Implementation:** Require both LLMs to return: `action`, `ticker`, `shares`, `price`, `stop_loss`, `confidence` (0.0-1.0), `reasoning` (2-3 sentences). Add a consensus threshold: require both models' confidence >= 0.6 for trade execution.
**Notes:** This is a prompt engineering change, not a code architecture change. Low effort, high leverage. Confidence gating prevents marginal trades.

### 12. Position Sizing Formula for Small Account
**Why Valuable:** Naive sizing on a sub-$1K account makes many trades commission-prohibitive or too small to matter.
**Complexity:** Low
**Implementation:** Kelly-inspired sizing with a hard cap. Two tiers:
- High conviction (both LLMs confidence >= 0.75): up to 40% of buying power
- Normal conviction (both >= 0.6): up to 20% of buying power
- Hard rule: no single trade < $50 (commission eats returns on smaller positions)
**Notes:** The project specification allows up to 50% single position. That is acceptable for a verified high-conviction catalyst play. Never let the LLM recommend a position size — compute it programmatically from confidence scores and available buying power.

### 13. Native tastytrade Stop Orders as Safety Net
**Why Valuable:** The daily cycle only enforces stop-losses once per day. If a micro-cap gap-downs overnight or at open, a native server-side stop provides protection between cycles.
**Complexity:** Low-Medium
**Implementation:** On every new buy, immediately submit a companion GTC stop order at the stop-loss price using tastytrade's `STOP` order type. If the daily cycle fires the stop-loss first, cancel the native stop. If the native stop fires between cycles, the position sync at next cycle startup detects the exit and logs it.
**Notes:** This eliminates the overnight/gap risk inherent in a daily-cycle-only stop-loss model. Critical for micro-cap biotech where binary events happen pre-market.

### 14. Notification System (Telegram or Email)
**Why Valuable:** The system operates autonomously. The operator needs to know what happened without having to SSH into a server.
**Complexity:** Low
**Implementation:** Telegram bot via `python-telegram-bot` is the recommended choice — free, instant, no email deliverability issues, supports rich formatting. Three notification types:
1. **Trade executed:** Ticker, action, shares, price, rationale summary, account balance after
2. **Stop-loss triggered:** Which position, at what price, P&L on the position
3. **Daily summary:** Positions held, today's P&L, account value vs all-time high, circuit breaker status
**Dependencies:** Requires Feature 7 (cycle) — notifications are emitted at the end of each cycle phase.
**Notes:** Email (SMTP/SendGrid) is an acceptable alternative but has more failure modes. Discord webhooks are also viable. Telegram is the pragmatic choice for a single-operator system.

### 15. Comprehensive Cycle Run Log
**Why Valuable:** Post-hoc analysis of why trades were or were not taken requires a complete record of every cycle's state.
**Complexity:** Low
**Implementation:** At the end of every cycle, write a structured JSON log entry containing: timestamp, account balance at start/end, positions at start/end, all LLM recommendations (both models), consensus result, trades executed, stop-losses checked, circuit breaker states. Store in a `run_logs/` directory.
**Notes:** This extends the existing `chatgpt_trade_log.csv` audit trail. The new log should be JSON (not CSV) because it needs to capture nested structures (per-LLM responses). Keep CSV for human-readable summary; JSON for machine-readable detail.

---

## Anti-Features

Things to explicitly NOT build. Building these wastes time or makes the system harder to trust.

### Anti-Feature 1: Web UI or Dashboard
**Why Avoid:** Adds frontend complexity (authentication, serving, session management) with zero trading value. Telegram notifications deliver the same information to where the operator is.
**What to Do Instead:** CLI with `--status` flag for on-demand portfolio display. Notifications for autonomous alerts.

### Anti-Feature 2: Options or Multi-Leg Strategies
**Why Avoid:** Options complexity (greeks, expiry, assignment risk) is categorically higher than equity complexity. The existing experiment has no options history to train LLM context on. A failure mode with options on a sub-$1K account can exceed account value.
**What to Do Instead:** Equities only in v1. If options are added later, they must be a separate, explicitly scoped project with defined risk caps.

### Anti-Feature 3: Intraday or Real-Time Monitoring Loop
**Why Avoid:** Micro-cap biotech catalysts play out over days to weeks, not minutes. An intraday polling loop adds API rate limit risk, cost, and complexity for zero return benefit in this domain. Native stop orders (Feature 13) cover the gap-down risk.
**What to Do Instead:** Daily cycle at market open (9:30-10:00 AM ET). Native GTC stop orders for between-cycle protection.

### Anti-Feature 4: Backtesting Framework
**Why Avoid:** The existing 11-week experiment IS the backtest — it proved the approach works with real money. Building a backtesting framework before live execution is waste. The project specification explicitly excludes this.
**What to Do Instead:** Forward-test with live money. The cycle run log (Feature 15) provides enough data to analyze performance after the fact.

### Anti-Feature 5: Multi-Account Support
**Why Avoid:** Single tastytrade account is the constraint. Generalizing to multi-account adds session management complexity with no benefit for this use case.
**What to Do Instead:** Single account, single session, hard-coded account selection at startup.

### Anti-Feature 6: LLM Debate / Multi-Round Reasoning
**Why Avoid:** Complex consensus mechanisms (Bull/Bear debate agents, weighted ensemble scoring, iterative reasoning rounds) are interesting research but not validated for this specific domain and account size. They increase API cost per cycle and introduce new failure modes.
**What to Do Instead:** Simple two-model veto: both say BUY = BUY, otherwise HOLD. If veto rate is too high, adjust prompt quality before adding consensus complexity.

### Anti-Feature 7: Self-Modifying Prompt Templates
**Why Avoid:** Research shows autonomous agents that rewrite their own rules "cascaded before a human can intervene" and consumed massive tokens. In a financial system, self-modification is a direct path to uncontrolled behavior.
**What to Do Instead:** Prompt templates are version-controlled static files. Changes require human commit. The LLMs analyze market data, not their own instructions.

### Anti-Feature 8: Automatic Circuit Breaker Reset
**Why Avoid:** If a circuit breaker trips, something went wrong. An automatic reset removes the human checkpoint that exists for good reason.
**What to Do Instead:** Tripped circuit breakers require manual override via environment variable (`OVERRIDE_CIRCUIT_BREAKER=1`). The notification system (Feature 14) delivers the alert so the operator can make an informed decision.

---

## Feature Dependencies

```
Feature 1 (tastytrade auth)
    └── Feature 2 (account balance)
    └── Feature 3 (position sync)
    └── Feature 4 (order execution)
            └── Feature 8 (live stop-loss enforcement)
            └── Feature 13 (native stop orders)

Feature 5 (Claude API)
    └── Feature 6 (consensus engine)
            └── Feature 11 (confidence scoring)   [enhances consensus]
            └── Feature 12 (position sizing)        [uses consensus output]
            └── Feature 10 (catalyst injection)     [feeds both LLMs]

Feature 7 (daily cycle)
    └── ALL features above (cycle orchestrates them)
    └── Feature 9 (circuit breakers)               [gating in cycle]
    └── Feature 14 (notifications)                  [emitted by cycle]
    └── Feature 15 (run log)                         [written by cycle]
```

**Critical path:** 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9

Everything else (10-15) is buildable in parallel once the critical path is established.

---

## MVP Recommendation

The minimal viable autonomous system requires exactly the table stakes features (1-9) in order:

**Phase 1 (Foundation):**
1. tastytrade auth + session management (Feature 1)
2. Account balance + position sync (Features 2, 3)
3. Order execution with dry_run validation (Feature 4)

**Phase 2 (Intelligence):**
4. Claude API integration (Feature 5)
5. Consensus engine with simple veto (Feature 6)
6. Structured prompt schema with confidence (Feature 11 — low effort, build with Feature 6)

**Phase 3 (Autonomous Cycle):**
7. Complete daily cycle orchestration (Feature 7)
8. Live stop-loss enforcement (Feature 8)
9. Circuit breakers with persistent state (Feature 9)
10. Notifications via Telegram (Feature 14 — low effort, build with cycle)
11. Native tastytrade stop orders (Feature 13)

**Defer to Phase 4:**
- Position sizing formula (Feature 12) — basic sizing works in Phase 3; tune after first live runs
- Biotech catalyst injection (Feature 10) — high value but requires external data integration
- Comprehensive run logging (Feature 15) — implement once cycle is stable

---

## Sources

- tastytrade API documentation: https://developer.tastytrade.com/api-overview/
- tastytrade Python SDK (tastyware): https://tastyworks-api.readthedocs.io/
- tastytrade Python SDK PyPI: https://pypi.org/project/tastytrade/
- tastytrade Order Management: https://developer.tastytrade.com/order-management/
- TradingAgents multi-LLM framework: https://tradingagents-ai.github.io/
- Multi-LLM trading case study (Medium): https://medium.com/@frankmorales_91352/the-evolution-of-algorithmic-trading-a-case-study-of-a-multi-llm-enhanced-cryptocurrency-trading-2941f6844068
- AI Trading Bot Risk Management Guide: https://3commas.io/blog/ai-trading-bot-risk-management-guide-2025
- Trading bot anti-patterns: https://dev.to/ai-agent-economy/our-trading-bot-rewrites-its-own-rules-heres-how-and-what-went-wrong-5dg9
- BioPharmCatalyst (biotech catalyst data): https://www.biopharmcatalyst.com/
- CatalystAlert (AI biotech predictions): https://catalystalert.io
