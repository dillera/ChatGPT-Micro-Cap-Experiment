# Roadmap: Micro-Cap AI Trading Bot

## Overview

A brownfield upgrade of a proven 11-week simulation into a fully autonomous live trading system. The journey moves through five phases: first establishing the persistent state store and proving the tastytrade OAuth2 connection; then building the order execution and multi-LLM consensus engine as the core upgrade; then assembling the autonomous daily cycle with circuit breakers; then hardening the system with dry-run testing and logging; and finally tuning thresholds against observed live data. At the end, a cron job fires each trading day and the system runs without human intervention — every trade placed, every stop-loss enforced, every decision logged.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Foundation** - SQLite state store, proven OAuth2 auth, read-only brokerage sync, and dev environment
- [ ] **Phase 2: Execution and Intelligence** - Live order execution, multi-LLM consensus engine, and position sizing
- [ ] **Phase 3: Autonomous Operations** - Daily trading cycle, circuit breakers, stop-loss enforcement, and structured logging
- [ ] **Phase 4: Hardening** - End-to-end dry-run test suite, operational validation, and system confidence
- [ ] **Phase 5: Tuning** - Evidence-based threshold and prompt refinement from live run data

## Phase Details

### Phase 1: Foundation
**Goal**: The system has a proven connection to tastytrade and a persistent state store — every downstream component can be built and tested against a stable foundation
**Depends on**: Nothing (first phase)
**Requirements**: INFR-01, INFR-02, INFR-03, INFR-04, BROK-01, BROK-02, BROK-03
**Success Criteria** (what must be TRUE):
  1. Running `--dry-run` prints live account balance and buying power fetched from tastytrade (not CSV)
  2. Live positions from tastytrade are written to the SQLite database and match the brokerage account
  3. OAuth2 authentication succeeds and session tokens auto-refresh without manual intervention
  4. OTC tickers are rejected at the screening stage before any API call is attempted
  5. PDT day-trade counter reads from SQLite and enforces the 3-trade-per-5-days limit at startup
**Plans**: 3 plans

Plans:
- [ ] 01-01: Set up project structure, pyproject.toml, uv lockfile, pydantic-settings config, and loguru logging
- [ ] 01-02: Implement SQLite state store (schema: positions, run_log, circuit_breaker, day_trade_counter) and migrate existing CSV data
- [ ] 01-03: Implement tastytrade OAuth2 client (auth, session lifecycle, auto-refresh) and read-only account/position sync

### Phase 2: Execution and Intelligence
**Goal**: The system can query both LLMs for a consensus decision, size the position correctly, and place a validated limit order — the core capability that removes the human from the loop
**Depends on**: Phase 1
**Requirements**: BROK-04, BROK-05, AIDC-01, AIDC-02, AIDC-03, AIDC-04, SIZE-01, SIZE-02, SIZE-03, SIZE-04
**Success Criteria** (what must be TRUE):
  1. GPT-4 and Claude are both queried with adversarial (bull/bear) prompts and their raw responses are logged before parsing
  2. A trade executes only when both models agree on the action AND both report confidence >= 0.6
  3. A disagreement between models produces a HOLD with both models' full reasoning written to the run log
  4. A confidence below 0.6 from either model produces a HOLD — not a trade
  5. Every new buy triggers a companion GTC stop order filed on tastytrade before the cycle ends
  6. Position size is computed from confidence score and buying power — high conviction (>= 0.75) uses up to 40%, normal (>= 0.6) uses up to 20%, no trade below $50
**Plans**: TBD

Plans:
- [ ] 02-01: Implement multi-LLM consensus engine (PromptBuilder, OpenAI client, Anthropic client, Pydantic response validation, ConsensusMatcher)
- [ ] 02-02: Implement order execution layer (limit orders only, dry_run pre-flight, spread check gate, symbol hallucination validation, GTC stop companion order)
- [ ] 02-03: Implement position sizing module (confidence-tiered formula, minimum trade guard, buying power integration)

### Phase 3: Autonomous Operations
**Goal**: A single cron trigger fires the complete trading cycle each market day — circuit breakers halt trading when risk limits are breached, stop-losses enforce against live positions, and every cycle produces a structured log
**Depends on**: Phase 2
**Requirements**: OPER-01, OPER-02, OPER-03, OPER-04, OPER-05, LOGS-01
**Success Criteria** (what must be TRUE):
  1. A cron job fires `python trading_cycle.py` once per trading day and the cycle completes without human interaction
  2. Stop-losses are checked against live tastytrade positions before LLM calls — any triggered stop places a sell order
  3. If daily loss exceeds 10% of opening balance, the circuit breaker trips and no further orders are placed that day
  4. If drawdown exceeds 30% from all-time high, the circuit breaker trips and remains tripped until manually overridden
  5. A structured JSON run log is written after every cycle containing the full state snapshot (positions, decisions, orders, circuit breaker status)
**Plans**: TBD

Plans:
- [ ] 03-01: Implement trading cycle orchestrator (stage sequencing, lockfile for cron overlap prevention, market status check, top-level exception handling)
- [ ] 03-02: Implement risk management module (circuit breaker state machine, daily loss limit, max drawdown halt, manual reset requirement, SQLite persistence)
- [ ] 03-03: Implement structured JSON run logging and wire APScheduler for daily autonomous scheduling

### Phase 4: Hardening
**Goal**: The system can be fully exercised in dry-run mode without placing real orders — every code path is tested and the operator has confidence in the system before leaving it unattended
**Depends on**: Phase 3
**Requirements**: (none — all v1 requirements covered in phases 1-3; this phase delivers operational confidence)
**Success Criteria** (what must be TRUE):
  1. `python trading_cycle.py --dry-run` completes a full cycle — auth, sync, LLM calls, sizing, order validation — without submitting any order to tastytrade
  2. A simulated JSON parse failure from either LLM aborts the cycle cleanly with an error log entry rather than defaulting to single-model execution
  3. A simulated circuit breaker trip in dry-run produces the correct halt behavior and requires a manual flag to resume
  4. The dry-run output log contains enough detail to verify every decision the system would have made
**Plans**: TBD

Plans:
- [ ] 04-01: Implement end-to-end dry-run test harness covering all seven pipeline stages with injected failure scenarios
- [ ] 04-02: Validate all critical pitfall mitigations (OAuth2 path, OTC filter, PDT counter, spread check, parse failure abort, circuit breaker manual reset)

### Phase 5: Tuning
**Goal**: After real trading runs accumulate data, the system's consensus thresholds, position sizing tiers, and prompt quality are refined based on observed behavior — not speculation
**Depends on**: Phase 4
**Requirements**: (none — evidence-based tuning of system already built; requires live run data to have accumulated)
**Success Criteria** (what must be TRUE):
  1. HOLD rate from run logs is measured and consensus threshold is adjusted if HOLD frequency exceeds 80% or falls below 30%
  2. Position sizing tiers are reviewed against actual fill prices and adjusted if realized slippage consistently exceeds the 3% spread gate
  3. LLM prompt quality is assessed from logged reasoning and at least one prompt revision is made based on observed hallucination patterns
**Plans**: TBD

Plans:
- [ ] 05-01: Analyze accumulated run logs for HOLD rate, fill quality, and LLM reasoning patterns — produce tuning recommendations
- [ ] 05-02: Implement approved threshold and prompt adjustments based on tuning analysis

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 0/3 | Not started | - |
| 2. Execution and Intelligence | 0/3 | Not started | - |
| 3. Autonomous Operations | 0/3 | Not started | - |
| 4. Hardening | 0/2 | Not started | - |
| 5. Tuning | 0/2 | Not started | - |
