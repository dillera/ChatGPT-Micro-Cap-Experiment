---
phase: 03-autonomous-operations
plan: 01
subsystem: trading-pipeline
tags: [orchestrator, stop-loss, lockfile, circuit-breaker, fcntl, cron]

# Dependency graph
requires:
  - phase: 02-execution-and-intelligence
    provides: consensus engine, position sizing, order execution layer
provides:
  - run_cycle() orchestrator sequencing all 8 pipeline stages
  - check_and_enforce_stops() stop-loss enforcement against live positions
affects: [03-02 circuit breaker wiring, 03-03 CLI and logging, 04-01 dry-run testing]

# Tech tracking
tech-stack:
  added: [fcntl]
  patterns: [lockfile via fcntl.flock, stage-sequenced pipeline, structured return dict]

key-files:
  created: [src/cycle.py, src/stoploss.py]
  modified: []

key-decisions:
  - "Lockfile at data/cycle.lock via fcntl.flock LOCK_EX|LOCK_NB for cron overlap prevention"
  - "Stop-loss sell defers to future simple sell method (place_otoco_order is for opening, not closing)"
  - "Consensus failure is non-fatal -- cycle continues to post-trade snapshot"
  - "Weekend-only market check for v1 (proper calendar deferred to Phase 5)"

patterns-established:
  - "Stage-sequenced pipeline: each stage returns early on failure with status dict"
  - "Lockfile pattern: acquire in try, release in finally, always"
  - "Structured result dict: every cycle returns dict with status key for downstream consumers"

requirements-completed: [OPER-01, OPER-02]

# Metrics
duration: 2min
completed: 2026-03-19
---

# Phase 3 Plan 1: Trading Cycle Orchestrator Summary

**8-stage trading cycle orchestrator with lockfile guard, circuit breaker gate, and stop-loss enforcement before LLM calls**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-19T15:15:30Z
- **Completed:** 2026-03-19T15:17:31Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Stop-loss enforcement module reads stored levels from SQLite, checks live quotes, and records triggered stops to trades table
- Trading cycle orchestrator sequences all 8 stages with exclusive lockfile, circuit breaker gate, and structured return dict
- Stop-loss enforcement runs BEFORE LLM consensus calls, preventing wasted API spend on positions that should be exited

## Task Commits

Each task was committed atomically:

1. **Task 1: Create stop-loss enforcement module** - `0b1918b` (feat)
2. **Task 2: Create trading cycle orchestrator** - `d69accd` (feat)

## Files Created/Modified
- `src/stoploss.py` - Stop-loss enforcement: reads positions DB, checks live quotes, records triggered stops
- `src/cycle.py` - Trading cycle orchestrator: 8-stage pipeline with lockfile, circuit breaker, and structured results

## Decisions Made
- Lockfile at `data/cycle.lock` using `fcntl.flock(LOCK_EX | LOCK_NB)` for non-blocking cron overlap prevention
- Stop-loss sell in live mode returns `needs_sell_method` status -- `place_otoco_order` is for opening positions, not closing. Simple sell method deferred to Phase 4
- Consensus failure is non-fatal: cycle continues to post-trade snapshot so account state is always captured
- Weekend-only market check (weekday >= 5) for v1; proper market calendar (holidays) deferred to Phase 5
- Candidates for consensus are current position symbols only; new BUY screening deferred to Phase 5

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `run_cycle()` is ready for circuit breaker wiring (Plan 03-02)
- Return dict provides all data needed for structured JSON logging (Plan 03-03)
- CLI integration (Plan 03-03) will translate status dict to exit codes

---
*Phase: 03-autonomous-operations*
*Completed: 2026-03-19*
