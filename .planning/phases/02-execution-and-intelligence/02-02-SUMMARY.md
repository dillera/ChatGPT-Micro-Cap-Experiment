---
phase: 02-execution-and-intelligence
plan: 02
subsystem: trading
tags: [position-sizing, risk-management, decimal, tdd]

# Dependency graph
requires:
  - phase: 01-foundation
    provides: models.py domain models, config.py settings
provides:
  - compute_shares() pure function for confidence-tiered position sizing
  - MIN_TRADE_VALUE, HIGH_CONVICTION_THRESHOLD, NORMAL_CONVICTION_THRESHOLD constants
  - test infrastructure (tests/ package with pytest)
affects: [02-execution-and-intelligence, 03-orchestration]

# Tech tracking
tech-stack:
  added: [pytest]
  patterns: [TDD red-green-refactor, pure function with Decimal arithmetic]

key-files:
  created: [src/sizing.py, tests/__init__.py, tests/test_sizing.py]
  modified: []

key-decisions:
  - "Loguru logger.info for below-threshold rejections (structured logging, not silent discard)"
  - "int() truncation for share rounding (floor division, never round up)"

patterns-established:
  - "TDD: write failing tests first, then implement, then verify"
  - "Pure function sizing: no broker/API dependencies, only primitives in and int out"

requirements-completed: [SIZE-01, SIZE-02, SIZE-03, SIZE-04]

# Metrics
duration: 2min
completed: 2026-03-19
---

# Phase 02 Plan 02: Position Sizing Summary

**Confidence-tiered position sizing with $50 minimum floor, 40%/20% buying power caps, and full TDD coverage**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-19T14:38:59Z
- **Completed:** 2026-03-19T14:40:42Z
- **Tasks:** 1 (TDD: RED + GREEN phases)
- **Files modified:** 3

## Accomplishments
- compute_shares() pure function handles high conviction (>=0.75, 40%), normal (>=0.6, 20%), and reject (<0.6) tiers
- $50 minimum trade floor prevents commission-dominated positions
- Division-by-zero guard and whole-share rounding (floor)
- 11 unit tests covering all SIZE requirements pass

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Failing tests for position sizing** - `b985645` (test)
2. **Task 1 GREEN: Implement sizing module** - `2eef0ac` (feat)

_TDD task: RED commit has failing tests, GREEN commit makes them pass._

## Files Created/Modified
- `src/sizing.py` - Confidence-tiered position sizing with compute_shares(), constants for thresholds and caps
- `tests/__init__.py` - Test package init
- `tests/test_sizing.py` - 11 unit tests for SIZE-01 through SIZE-04

## Decisions Made
- Used loguru for rejection logging (consistent with project logging pattern)
- int() truncation for share count (never round up to avoid exceeding allocation)
- No refactor phase needed -- implementation clean and minimal on first pass

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Position sizing module ready for integration with consensus engine (Plan 03)
- compute_shares() accepts primitives (Decimal, float) -- no coupling to broker or LLM modules
- Test infrastructure (tests/ directory, pytest) established for subsequent plans

---
*Phase: 02-execution-and-intelligence*
*Completed: 2026-03-19*
