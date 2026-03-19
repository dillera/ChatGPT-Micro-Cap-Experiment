# Phase 3: Autonomous Operations - Context

**Gathered:** 2026-03-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Wire all Phase 1 (foundation) and Phase 2 (consensus + orders + sizing) components into a complete daily trading cycle orchestrator. Add circuit breakers (10% daily loss, 30% max drawdown), live stop-loss enforcement against tastytrade positions, structured JSON run logging, and cron-schedulable entry point. After this phase, a single cron trigger runs the entire system autonomously.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
User opted for Claude to make all implementation decisions. Open areas:

**Trading Cycle Orchestrator:**
- Stage sequencing: authenticate → sync positions → check circuit breakers → enforce stop-losses → fetch prices → run consensus → size positions → execute orders → log results
- Lockfile for cron overlap prevention
- Market status check (skip on weekends/holidays)
- Top-level exception handling (never crash silently)
- Exit codes: 0 = success (even if no trades), 1 = error

**Circuit Breakers:**
- State machine: ARMED → TRIPPED_DAILY → TRIPPED_DRAWDOWN
- 10% daily loss limit (from opening balance)
- 30% max drawdown from all-time high
- Manual reset only (env var OVERRIDE_CIRCUIT_BREAKER=1)
- Persist state in SQLite circuit_breaker table

**Stop-Loss Enforcement:**
- Check against live positions BEFORE LLM calls
- Compare current price vs stop_loss in positions table
- Triggered stops → submit sell order immediately
- Native GTC stops (from Phase 2 OTOCO) provide between-cycle safety

**Structured Run Logging:**
- JSON log per cycle in run_logs/ directory
- Contents: timestamp, account state, positions, LLM recommendations, consensus results, orders placed, circuit breaker status
- Replaces and extends existing chatgpt_trade_log.csv pattern

**Scheduling:**
- APScheduler or simple cron entry point
- Run at ~10:00 AM ET (after market open settles)
- --dry-run flag propagates through entire cycle

</decisions>

<canonical_refs>
## Canonical References

### Architecture
- `.planning/research/ARCHITECTURE.md` — Orchestrator as glue layer, circuit breaker state machine
- `.planning/research/FEATURES.md` — Features 7 (daily cycle), 8 (live stop-loss), 9 (circuit breakers), 15 (run logging)

### Existing Code
- `src/broker.py` — TastytradeClient (auth, snapshot, positions, orders, quotes)
- `src/consensus.py` — run_consensus_cycle()
- `src/sizing.py` — compute_shares()
- `src/orders.py` — execute_trade() with 6 safety gates
- `src/cli.py` — Current --dry-run entry point to extend
- `src/db.py` — SQLite with circuit_breaker and run_log tables already in schema
- `src/pdt.py` — PDT counter
- `src/config.py` — All settings

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- All Phase 1+2 modules are ready to wire together
- SQLite schema already has circuit_breaker and run_log tables
- cli.py has the --dry-run pattern to extend

### Integration Points
- New: `src/cycle.py` — orchestrator that sequences all stages
- New: `src/circuit_breaker.py` — state machine with SQLite persistence
- Extend: `src/cli.py` — add full cycle command
- New: `run_logs/` directory for JSON cycle logs

</code_context>

<specifics>
## Specific Ideas

No specific requirements — Claude has full discretion guided by research.

</specifics>

<deferred>
## Deferred Ideas

None.

</deferred>

---

*Phase: 03-autonomous-operations*
*Context gathered: 2026-03-19*
