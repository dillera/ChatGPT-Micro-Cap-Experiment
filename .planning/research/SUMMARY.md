# Project Research Summary

**Project:** Micro-Cap AI Autonomous Trading Bot
**Domain:** Autonomous algorithmic trading — brownfield upgrade from simulation to live brokerage execution
**Researched:** 2026-03-19
**Confidence:** HIGH

## Executive Summary

This is a brownfield upgrade of a proven simulation system — not a greenfield build. Eleven weeks of live paper trading have already validated the core thesis: a micro-cap biotech strategy guided by GPT-4 LLM analysis produced approximately 32% returns. The upgrade task is precisely scoped: connect the existing decision layer to a live tastytrade brokerage account, add Claude as a second LLM consensus requirement, and add the operational infrastructure (scheduling, circuit breakers, notifications, persistent state) needed to run autonomously. The recommended architecture is a scheduled pipeline executor — deterministic, single-process, daily cycle — not a real-time event-driven system. This matches the domain: micro-cap biotech catalysts play out over days, not minutes.

The most critical technical constraint is the tastytrade SDK's async requirement combined with a critical authentication change: session-token (username/password) authentication was discontinued December 1, 2025. All new code must use OAuth2 via the tastyware/tastytrade SDK v12.x. The async boundary should be isolated to a thin Broker API Layer — everything else stays synchronous to limit complexity. The recommended stack extends the existing Python 3.11 / pandas / yfinance / openai foundation with tastytrade SDK, anthropic SDK, APScheduler, SQLite state store, pydantic-settings, loguru, and Telegram notifications. SQLite replaces the existing CSV state files as the authoritative source of truth.

The primary risks fall into three categories: regulatory (Pattern Day Trader rule will lock the account at over 3 day trades in 5 business days), execution quality (market orders on illiquid micro-caps cause catastrophic slippage — limit orders exclusively), and LLM reliability (both models can hallucinate tickers and catalysts; JSON parse failures must abort the cycle, never silently degrade to single-model decisions). The dual-LLM consensus veto — both must say BUY for a trade to execute — is the correct starting architecture. Do not add debate rounds, weighted voting, or self-modifying prompts. Simple veto first; tune only if HOLD frequency is unacceptably high after live runs.

---

## Key Findings

### Recommended Stack

The existing Python 3.11 / pandas / numpy / yfinance / openai stack requires extension, not replacement. The largest architectural shift is moving from synchronous scripts to an async boundary at the brokerage integration layer. The tastyware/tastytrade SDK (v12.2.0, released March 16, 2026) is the clear choice over the official tastytrade-sdk — it provides typed models, dry_run order validation, auto-refresh OAuth tokens, and 10x less boilerplate. Package management should migrate from requirements.txt to pyproject.toml + uv for lockfile-based dependency reproducibility.

**Core technologies:**
- `tastyware/tastytrade` 12.2.0: live brokerage integration — actively maintained, typed, async, full order lifecycle
- `anthropic` 0.86.0: Claude consensus signal — second LLM, simple messages API
- `openai` 2.29.0: GPT-4 trading analysis — existing but update; note `chatgpt-4o-latest` was deprecated February 17, 2026
- `asyncio` (stdlib): async runtime at broker boundary only — SDK requires `await` for all calls
- `APScheduler` 3.x: daily autonomous cycle scheduling — Python-native, DST-aware, survives restarts
- `pydantic-settings` 2.9.1: typed config from environment — validates all API keys at startup, fails fast
- `loguru`: structured logging with rotation — one-line setup, JSON sink for trade audit events
- `python-telegram-bot`: mobile trade notifications — free, async-native, better than email for urgent alerts
- `SQLite` (stdlib): persistent state store — replaces CSV files; single-process, no operational burden
- `uv` + `pyproject.toml`: lockfile package management — prevents environment drift on live system

### Expected Features

The existing system provides market data fetching, CSV portfolio state, stop-loss simulation, GPT-4 recommendations, and trade logging. These are preserved foundations, not features to rebuild. The upgrade adds live execution and operational infrastructure.

**Must have (table stakes — Features 1-9):**
- tastytrade OAuth2 authentication and session management — zero API calls succeed without this
- Live account balance and buying power retrieval — CSV cash tracking drifts from reality
- Live position sync from tastytrade — live account is the source of truth, not CSV
- Programmatic order execution with dry_run pre-flight — always validate before submitting
- Claude API integration — project constraint mandates dual-LLM consensus
- Multi-LLM consensus engine (simple veto: both say BUY = execute) — core architectural requirement
- Daily autonomous trading cycle orchestrator — cron-schedulable, complete single entry point
- Live stop-loss enforcement against actual positions — stop-loss check runs before LLM calls
- Circuit breakers (daily loss limit 10% + max drawdown halt 25%) — prevent account blowup

**Should have (differentiators — Features 10-15, build after critical path):**
- Structured LLM prompt schema with confidence scoring (0.0-1.0) — low effort, high leverage, build with Phase 2
- Position sizing formula (Kelly-inspired, confidence-tiered) — compute programmatically, never let LLM size positions
- Native tastytrade GTC stop orders as safety net — covers overnight gap-down risk between daily cycles
- Telegram notifications (trade executed, stop-loss triggered, daily summary) — build with Phase 3 cycle
- Comprehensive JSON cycle run log — machine-readable, nested, extends existing CSV audit trail
- Biotech catalyst context injection (BioPharmCatalyst) — highest-value differentiator, Phase 4

**Defer (v2+):**
- Web UI or dashboard — Telegram + CLI flag provides equivalent operator value
- Options or multi-leg strategies — categorically different risk profile; equities only in v1
- Intraday polling loop — native stop orders cover the gap-down risk this would address
- Backtesting framework — 11-week live experiment is the backtest; forward-test with live money
- LLM debate rounds or weighted ensemble — adds cost and failure modes without validated benefit

### Architecture Approach

The system follows a scheduled pipeline executor pattern. A single cron trigger fires once per trading day, passes data through seven deterministic stages (circuit breaker check → broker sync → market data fetch → stop-loss evaluation → LLM consensus → risk validation → order execution → daily snapshot → notifications), and terminates. Each stage has a clear input/output contract. No stage calls another stage directly — all cross-stage communication goes through the Orchestrator or SQLite State Store. This keeps the system testable, debuggable, and resistant to scope creep toward real-time operation.

**Major components:**
1. `trading_cycle.py` (Orchestrator) — sequences stages, owns circuit breaker gate, handles top-level exceptions
2. `broker/tastytrade_client.py` (Broker API Layer) — sync facade over async SDK, session lifecycle, order placement
3. `data/market_data.py` (Market Data Layer) — stateless fetch layer, preserves Yahoo → Stooq fallback chain
4. `llm/consensus_engine.py` (LLM Consensus Engine) — parallel GPT-4 + Claude calls, consensus matching, LLM audit trail
5. `risk/risk_manager.py` (Risk Management Module) — validates all proposed trades, owns circuit breaker state machine
6. `state/db.py` (SQLite State Store) — single source of truth, replaces all CSV files, persists circuit breaker state
7. `notifications/notifier.py` (Notification Layer) — pluggable transport, Telegram/stdout, event-driven from orchestrator

### Critical Pitfalls

1. **Deprecated username/password auth (Pitfall 2)** — Session-token auth was discontinued December 1, 2025. Use OAuth2 exclusively with tastytrade SDK 12.x. Older tutorials and LLM training data suggest the wrong approach. Address in Phase 1 before any other API work.

2. **Pattern Day Trader rule (Pitfall 3)** — Over 3 day trades (open and close same security same day) in any 5-business-day window locks the account. Track day trade count in state; enforce hard 2-trade maximum with 1-trade buffer; use GTC stop orders (next day) not same-day market stops. Address in Phase 2 execution layer and Phase 3 risk management.

3. **Market order slippage on micro-caps (Pitfall 5)** — Micro-cap biotech spreads can be 5-20% of price. A market order on a 50% position with 5% slippage is a $12.50 immediate loss on a $500 account. Use limit orders exclusively. Implement a maximum spread check gate (reject if `(ask-bid)/mid > 3%`). Address in Phase 2 order execution layer.

4. **LLM hallucination of tickers and catalysts (Pitfall 4)** — Both models fabricate symbols, misstate FDA dates, and invent clinical results. Never pass LLM-provided tickers directly to orders. Validate every symbol against Yahoo Finance / tastytrade instruments API post-LLM. Require stated catalyst with verifiable date. Address in Phase 2 LLM integration.

5. **JSON parse failure corrupting execution (Pitfall 10)** — LLMs do not guarantee schema compliance. Validate every response with Pydantic. A Pydantic validation error = abort cycle, alert, do not trade. Never fall back to single-model decision when parsing fails. Log every raw LLM response before parsing for post-incident debugging. Address in Phase 2 LLM integration.

6. **OTC / penny stock rejection (Pitfall 1)** — tastytrade prohibits OTC-listed securities. Many micro-caps under $300M market cap trade OTC. Filter candidates at screening time to `exchange` in `['NYSE', 'NASDAQ', 'NYSE American', 'BATS']`. Add pre-order instrument validation. Address in Phase 1.

---

## Implications for Roadmap

Research across all four dimensions converges on a clear five-phase structure. The ordering is driven by hard dependency chains: every component depends on the State Store existing first, brokerage integration must exist before execution, LLM layer requires knowing what portfolio state looks like, and the Orchestrator is glue code that cannot be written until all stages it sequences exist.

### Phase 1: Foundation — State Store, Auth, and Dev Environment

**Rationale:** The SQLite State Store is the dependency of everything else. Authentication must be proved working before any brokerage code is written. The sandbox symbol gap issue (Pitfall 11) and deprecated auth issue (Pitfall 2) must be resolved before any other API work begins or developer time is wasted in the wrong direction.

**Delivers:** SQLite schema, CSV migration script, proven OAuth2 tastytrade session, dev environment with cert sandbox, OTC ticker filter at screening level, API rate-limit client wrapper.

**Addresses:** Features 1 (auth), 2 (account balance), 3 (position sync) — read-only brokerage operations only.

**Avoids:** Pitfall 2 (deprecated auth), Pitfall 11 (sandbox symbol gaps), Pitfall 1 (OTC ticker rejection), Pitfall 15 (rate limiting).

**Research flag:** Standard OAuth2 pattern, well-documented in SDK. No additional phase research needed.

---

### Phase 2: Execution and Intelligence Layer

**Rationale:** Order execution and LLM integration are the core upgrade deliverables and the highest-risk components. Both must be built and tested against sandbox (with appropriate symbol awareness) before the autonomous cycle can be assembled. LLM prompt schema and confidence scoring (Feature 11) costs nothing to build alongside the consensus engine and should not be deferred.

**Delivers:** Live order execution with dry_run pre-flight, multi-LLM consensus engine (GPT-4 + Claude, simple veto), structured prompt schema with confidence scoring, Pydantic LLM response validation, LLM audit trail, symbol hallucination detection, position sizing formula.

**Uses:** tastytrade SDK order types (LIMIT, STOP, GTC), openai 2.29.0, anthropic 0.86.0, Pydantic validation.

**Implements:** Broker API Layer, LLM Consensus Engine (PromptBuilder, OpenAIClient, AnthropicClient, ResponseParser, ConsensusMatcher).

**Avoids:** Pitfall 3 (PDT — track day trade count from this phase), Pitfall 4 (LLM hallucination), Pitfall 5 (market order slippage), Pitfall 10 (JSON parse failure), Pitfall 9 (false consensus — adversarial prompting strategy).

**Research flag:** LLM adversarial prompting strategy for false consensus prevention (Pitfall 9) may need a focused spike. Everything else follows documented patterns.

---

### Phase 3: Autonomous Cycle, Risk Management, and Operations

**Rationale:** The Orchestrator is last because it sequences all prior components. Circuit breakers and stop-loss enforcement require live execution to exist. Scheduling, DST handling, and duplicate-order prevention are operational concerns that only become real once the cycle is assembled.

**Delivers:** Complete autonomous daily trading cycle (cron-schedulable), live stop-loss enforcement with native tastytrade GTC stop orders as safety net, circuit breakers (daily loss 10%, max drawdown 25%, manual reset required), Telegram notifications, market status check at cycle start, lockfile for cron overlap prevention.

**Uses:** APScheduler (DST-aware), python-telegram-bot, tastytrade STOP order type, SQLite circuit_breaker table.

**Implements:** Orchestrator, Risk Management Module (full circuit breaker state machine), Notification Layer.

**Avoids:** Pitfall 3 (PDT — GTC stops not same-day), Pitfall 6 (duplicate orders on restart), Pitfall 7 (trading halt — order timeout alert), Pitfall 8 (timezone/DST — market status API check), Pitfall 13 (streamer disconnection — REST reconciliation on reconnect).

**Research flag:** Standard patterns. No additional research needed. APScheduler DST-awareness is documented.

---

### Phase 4: Hardening and Biotech Specialization

**Rationale:** After the first live trading runs, real operational data will reveal where the system needs tuning. Biotech catalyst injection is the highest-leverage differentiator (the primary reason the simulation outperformed) but requires external data integration that should be validated after the core system is stable.

**Delivers:** End-to-end dry-run test suite (dry_run=True on all broker calls), comprehensive JSON cycle run log, biotech catalyst context injection into LLM prompts (BioPharmCatalyst or CatalystAlert), wash sale 31-day re-entry block, FINRA TAF fee accounting in P&L.

**Avoids:** Pitfall 14 (wash sale rule), Pitfall 12 (TAF fee divergence in P&L).

**Research flag:** Biotech catalyst data APIs need verification — BioPharmCatalyst terms of service must be confirmed before automating scraping. This phase likely needs a focused research spike on available APIs.

---

### Phase 5: Tuning and Optimization

**Rationale:** Once live data accumulates, evidence-based tuning of consensus thresholds, position sizing tiers, circuit breaker percentages, and prompt quality becomes possible. This is explicitly not speculative — only tune based on observed behavior.

**Delivers:** Consensus threshold adjustments based on HOLD rate data, position sizing tier refinement, prompt quality improvements, performance reporting.

**Research flag:** No research needed. This phase is data-driven from live run logs.

---

### Phase Ordering Rationale

- State Store first because every component reads/writes it — building it first enables isolation testing of all other components.
- Auth and brokerage read-only operations before order execution — validates the integration without financial risk.
- Execution and intelligence together in Phase 2 because they are the core upgrade risk; they belong in the same phase to be tested as a unit.
- Orchestrator and scheduling last because they are assembly code — cannot be written until all stages they sequence exist.
- Biotech catalyst injection in Phase 4 (not Phase 2) because it requires an external API integration that should not block the critical path.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 4:** BioPharmCatalyst and CatalystAlert API availability, terms of service, and data format need verification before implementation. MEDIUM confidence currently.
- **Phase 2:** The specific adversarial prompting strategy for false consensus prevention (Pitfall 9) is worth a focused spike — the "one plays bear, one plays bull" approach has MEDIUM confidence.

Phases with standard patterns (skip research-phase):
- **Phase 1:** OAuth2 + tastytrade SDK 12.x is fully documented. SQLite schema design is standard.
- **Phase 3:** APScheduler, circuit breaker state machines, Telegram bot integration — all well-documented.
- **Phase 5:** Data-driven tuning, no external research needed.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Core additions (tastytrade SDK, anthropic) verified via official docs and PyPI at current versions. APScheduler and Telegram bot are MEDIUM (community consensus, no trading-specific concerns found). OpenAI model IDs are MEDIUM — deprecation happened February 2026, verify current model names at implementation. |
| Features | HIGH | tastytrade API capabilities verified against official docs. Multi-LLM consensus patterns documented via TradingAgents research and case studies. Risk management patterns are domain-universal. |
| Architecture | HIGH | Scheduled pipeline executor pattern is well-matched to daily-cycle strategy. SQLite choice is justified. tastytrade SDK data contracts verified against official source. LLM consensus data contracts follow established patterns. |
| Pitfalls | HIGH | Critical pitfalls (OAuth deprecation, OTC restriction, PDT rule) verified against official tastytrade support docs and SDK changelogs. LLM pitfalls verified against multiple independent sources. |

**Overall confidence:** HIGH

### Gaps to Address

- **OpenAI model IDs:** `chatgpt-4o-latest` was deprecated February 17, 2026. The correct current model identifier must be verified at `platform.openai.com/docs/models` before implementation. Do not assume any specific model string.
- **BioPharmCatalyst API terms:** Automating access requires ToS verification. MEDIUM confidence that a usable API or scraping approach exists. Validate in Phase 4 before building.
- **Adversarial prompting for false consensus:** The bear/bull prompting split for Pitfall 9 prevention is theoretically sound but has not been validated against this specific domain and account size. Build simple veto first; add adversarial prompting as an enhancement based on observed HOLD rate.
- **tastytrade cash account PDT exception:** Cash accounts may avoid PDT restrictions entirely (different settlement rules). Verify against official tastytrade support before Phase 3 if PDT tracking adds complexity — switching account type may be simpler.
- **APScheduler version compatibility with async:** APScheduler 4.x supports async jobs natively; 3.x requires different patterns. Confirm version at implementation time to avoid asyncio.run() conflict with in-process event loop.

---

## Sources

### Primary (HIGH confidence)
- tastyware/tastytrade GitHub v12.2.0 (March 16, 2026): https://github.com/tastyware/tastytrade
- tastytrade SDK official docs: https://tastyworks-api.readthedocs.io/en/latest/
- tastytrade Developer Portal: https://developer.tastytrade.com/api-overview/
- anthropic PyPI v0.86.0 (March 18, 2026): https://pypi.org/project/anthropic/
- openai PyPI v2.29.0 (March 17, 2026): https://pypi.org/project/openai/
- pydantic-settings docs: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
- tastytrade OTC restriction: https://support.tastytrade.com/support/s/solutions/articles/43000478158
- tastytrade PDT rule: https://support.tastytrade.com/support/s/solutions/articles/43000435180
- tastytrade OAuth deprecation (GitHub Issue #269): https://github.com/tastyware/tastytrade/issues/269
- tastytrade Sandbox docs: https://developer.tastytrade.com/sandbox/

### Secondary (MEDIUM confidence)
- TradingAgents multi-LLM framework: https://arxiv.org/abs/2412.20138
- LLM Council consensus pattern: https://virtuslab.com/blog/ai/llm-council
- APScheduler docs: https://apscheduler.readthedocs.io/
- SQLite vs PostgreSQL for trading systems: https://medium.com/prooftrading/selecting-a-database-for-an-algorithmic-trading-system-2d25f9648d02
- 3commas AI trading bot risk management guide (2025): https://3commas.io/blog/ai-trading-bot-risk-management-guide-2025
- AI trading bot self-modification anti-pattern: https://dev.to/ai-agent-economy/our-trading-bot-rewrites-its-own-rules-heres-how-and-what-went-wrong-5dg9
- LLM hallucinations in finance: https://biztechmagazine.com/article/2025/08/llm-hallucinations-what-are-implications-financial-institutions
- Claude bias in finance: https://blogs.cfainstitute.org/investor/2025/05/14/ai-bias-by-design-what-the-claude-prompt-leak-reveals-for-investment-professionals/
- BioPharmCatalyst: https://www.biopharmcatalyst.com/
- FINRA Trading Halts: https://www.finra.org/investors/investing/investment-products/stocks/trading-halts-delays-suspensions
- SEC Microcap stock risk bulletin: https://sec.gov/oiea/investor-alerts-bulletins/ib_microcap_3.html

### Tertiary (LOW confidence — informational only)
- CFTC AI trading advisory: https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/AITradingBots.html

---

*Research completed: 2026-03-19*
*Ready for roadmap: yes*
