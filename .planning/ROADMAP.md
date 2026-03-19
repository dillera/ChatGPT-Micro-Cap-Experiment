# Roadmap: Micro-Cap AI Trading Bot

## Overview

A brownfield upgrade of a proven 11-week simulation into a fully autonomous live trading system. The journey moves through five phases: first establishing the persistent state store and proving the tastytrade OAuth2 connection; then building the order execution and multi-LLM consensus engine as the core upgrade; then assembling the autonomous daily cycle with circuit breakers; then hardening the system with dry-run testing and logging; and finally tuning thresholds against observed live data. At the end, a cron job fires each trading day and the system runs without human intervention — every trade placed, every stop-loss enforced, every decision logged.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Foundation** - SQLite state store, proven OAuth2 auth, read-only brokerage sync, and dev environment (completed 2026-03-19)
- [x] **Phase 2: Execution and Intelligence** - Live order execution, multi-LLM consensus engine, and position sizing (completed 2026-03-19)
- [x] **Phase 3: Autonomous Operations** - Daily trading cycle, circuit breakers, stop-loss enforcement, and structured logging (completed 2026-03-19)
- [x] **Phase 4: Hardening** - End-to-end dry-run test suite, operational validation, and system confidence (completed 2026-03-19)
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
- [x] 01-01: Set up project structure, pyproject.toml, uv lockfile, pydantic-settings config, and loguru logging
- [x] 01-02: Implement SQLite state store (schema: positions, run_log, circuit_breaker, day_trade_counter) and migrate existing CSV data
- [x] 01-03: Implement tastytrade OAuth2 client (auth, session lifecycle, auto-refresh) and read-only account/position sync

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
**Plans**: 3 plans

Plans:
- [x] 02-01-PLAN.md — Multi-LLM consensus engine (Pydantic schemas, bull/bear prompts, OpenAI + Anthropic structured output, veto consensus logic, SQLite audit logging)
- [x] 02-02-PLAN.md — Position sizing module (confidence-tiered formula, $50 minimum guard, whole-share rounding, buying power integration)
- [x] 02-03-PLAN.md — Order execution layer (spread check gate, OTC filter, PDT guard, OTOCO limit+stop orders, dry_run validation, trade recording)

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
**Plans**: 3 plans

Plans:
- [ ] 03-01-PLAN.md — Trading cycle orchestrator with stop-loss enforcement (stage sequencing, lockfile, market check, stop-loss before LLM calls)
- [ ] 03-02-PLAN.md — Circuit breaker state machine (10% daily loss limit, 30% max drawdown halt, manual reset, SQLite persistence)
- [ ] 03-03-PLAN.md — Structured JSON run logging, circuit breaker wiring, and CLI update for autonomous execution

### Phase 4: Hardening
**Goal**: The system can be fully exercised in dry-run mode without placing real orders — every code path is tested and the operator has confidence in the system before leaving it unattended
**Depends on**: Phase 3
**Requirements**: (none — all v1 requirements covered in phases 1-3; this phase delivers operational confidence)
**Success Criteria** (what must be TRUE):
  1. `python trading_cycle.py --dry-run` completes a full cycle — auth, sync, LLM calls, sizing, order validation — without submitting any order to tastytrade
  2. A simulated JSON parse failure from either LLM aborts the cycle cleanly with an error log entry rather than defaulting to single-model execution
  3. A simulated circuit breaker trip in dry-run produces the correct halt behavior and requires a manual flag to resume
  4. The dry-run output log contains enough detail to verify every decision the system would have made
**Plans**: 2 plans

Plans:
- [x] 04-01-PLAN.md — End-to-end dry-run test harness (12 tests covering all 11 pipeline stages with failure injection: happy path, LLM failure, CB halt, weekend skip, lockfile, run log verification, stop-loss, post-trade CB trip)
- [x] 04-02-PLAN.md — Pitfall mitigation validation (50 tests proving OTC filter, OAuth2 path, PDT counter, spread check, consensus veto, parse failure abort, circuit breaker states, position sizing guards)

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
| 1. Foundation | 3/3 | Complete   | 2026-03-19 |
| 2. Execution and Intelligence | 3/3 | Complete   | 2026-03-19 |
| 3. Autonomous Operations | 3/3 | Complete   | 2026-03-19 |
| 4. Hardening | 2/2 | Complete   | 2026-03-19 |
| 5. Tuning | 0/2 | Not started | - |
| 6. Watchlist and Screening | 2/2 | Complete | 2026-03-19 |
| 7. Streamlit Dashboard | 2/2 | Complete   | 2026-03-19 |

### Phase 6: Watchlist and Screening
**Goal**: Manual watchlist management, sector-based micro-cap screening, and LLM-proposed ticker discovery — feeding candidate tickers into the consensus engine
**Depends on**: Phase 4 (can run before Phase 5 since it doesn't need live data)
**Requirements**: WATCH-01, WATCH-02, WATCH-03, WATCH-04, WATCH-05
**Success Criteria** (what must be TRUE):
  1. User can add/remove tickers to a persistent watchlist stored in SQLite
  2. Sector screener returns micro-cap stocks matching criteria (market cap, volume, exchange)
  3. LLMs propose new ticker candidates based on portfolio state and market conditions
  4. All candidates pass OTC filter and exchange validation before consensus
  5. Daily cycle pulls candidates from all three sources and runs consensus on each
**Plans**: 2 plans

Plans:
- [x] 06-01-PLAN.md — Watchlist CRUD (SQLite table, add/remove/list functions, CLI subcommands) and sector-based micro-cap screener (yfinance, OTC filter, cache)
- [x] 06-02-PLAN.md — LLM discovery prompt for ticker proposals and cycle integration wiring all three candidate sources into daily trading cycle

### Phase 7: Streamlit Dashboard
**Goal**: A local Streamlit web app for managing the watchlist, viewing positions/P&L, reviewing run logs, and monitoring circuit breaker status — the operator's window into the autonomous system
**Depends on**: Phase 6
**Requirements**: DASH-01, DASH-02, DASH-03, DASH-04, DASH-05
**Success Criteria** (what must be TRUE):
  1. Streamlit app runs at localhost:8501 and displays current portfolio positions with P&L
  2. User can add/remove watchlist tickers via the UI with immediate SQLite persistence
  3. Run log history is browsable with expandable detail per cycle
  4. Circuit breaker status is visible with manual reset button
  5. Sector screener results are displayed and can be added to watchlist with one click
**Plans**: 2 plans

Plans:
- [ ] 07-01-PLAN.md — Core dashboard: portfolio positions/P&L display, watchlist management UI, circuit breaker status with manual reset
- [ ] 07-02-PLAN.md — Run log browser with expandable JSON detail, sector screener results with one-click add-to-watchlist
