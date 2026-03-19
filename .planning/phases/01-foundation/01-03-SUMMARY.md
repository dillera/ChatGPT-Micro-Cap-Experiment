---
phase: 01-foundation
plan: 03
subsystem: brokerage
tags: [tastytrade, oauth2, async, otc-filter, cli]

# Dependency graph
requires:
  - phase: 01-foundation plan 01
    provides: pydantic-settings config with tastytrade credentials
  - phase: 01-foundation plan 02
    provides: SQLite schema (positions, session_cache tables), models, PDT counter
provides:
  - TastytradeClient sync facade over async SDK with OAuth2 auth
  - AccountSnapshot dataclass for balance/positions
  - Session caching in SQLite with 14-min TTL
  - OTC ticker filter (is_exchange_listed, validate_symbols)
  - CLI entry point with --dry-run flag
affects: [phase-02-execution, phase-03-operations]

# Tech tracking
tech-stack:
  added: [tastytrade SDK 12.2.0 (async)]
  patterns: [sync-facade-over-async, session-caching-in-sqlite, conservative-exchange-filter]

key-files:
  created:
    - src/broker.py
    - src/otc_filter.py
    - src/cli.py
    - src/__main__.py
  modified: []

key-decisions:
  - "Sync Account.get() is actually async coroutine in SDK 12.2.0 -- used asyncio.run() boundary pattern"
  - "CurrentPosition has no market_value field -- computed from mark_price * quantity"
  - "Conservative OTC filter: unknown exchanges rejected (safe default)"

patterns-established:
  - "Async isolation: all tastytrade SDK calls wrapped in asyncio.run() at broker.py boundary"
  - "Session caching: serialize/deserialize to session_cache table with 14-min TTL"
  - "OTC guard: frozenset lookup for O(1) exchange validation"

requirements-completed: [BROK-01, BROK-02, BROK-03, INFR-02, INFR-04]

# Metrics
duration: 2min
completed: 2026-03-19
---

# Phase 1 Plan 3: Brokerage Client Summary

**Tastytrade OAuth2 client with sync facade, session caching, OTC exchange filter, and --dry-run CLI entry point**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-19T13:48:43Z
- **Completed:** 2026-03-19T13:50:31Z
- **Tasks:** 2
- **Files created:** 4

## Accomplishments
- TastytradeClient wraps async SDK behind synchronous methods using asyncio.run()
- OAuth2 authentication with provider_secret + refresh_token (no deprecated username/password)
- Session tokens cached in SQLite session_cache table with 14-minute TTL
- OTC filter rejects OTC/PINK/GREY exchanges before any brokerage API call
- CLI entry point with --dry-run prints account balance, buying power, positions
- Module runnable via `python -m src --dry-run`

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement tastytrade OAuth2 client** - `d391f98` (feat)
2. **Task 2: Implement OTC filter and CLI** - `f94de78` (feat)

## Files Created/Modified
- `src/broker.py` - TastytradeClient sync facade, AccountSnapshot dataclass, session caching, position sync
- `src/otc_filter.py` - VALID_EXCHANGES/OTC_EXCHANGES frozensets, is_exchange_listed(), validate_symbols()
- `src/cli.py` - CLI with --dry-run and --sync-only flags, connects broker and prints summary
- `src/__main__.py` - Module entry point for `python -m src`

## Decisions Made
- SDK 12.2.0 Account.get(), get_balances(), get_positions() are all async coroutines (not sync). Used asyncio.run() at facade boundary per ARCHITECTURE.md Anti-Pattern 4.
- CurrentPosition lacks a market_value field. Computed as mark_price * quantity (falls back to average_open_price if mark_price is None).
- OTC filter uses conservative approach: unknown exchanges are rejected by default. Only explicitly listed VALID_EXCHANGES are accepted.
- Rate limiting: added asyncio.sleep(0.5) between sequential API calls per community-documented 2 req/sec limit.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Corrected async method names in SDK**
- **Found during:** Task 1 (broker.py implementation)
- **Issue:** Plan referenced `Account.aget()` but SDK 12.2.0 uses `Account.get()` as async coroutine (not `aget` prefix)
- **Fix:** Used `Account.get()`, `account.get_balances()`, `account.get_positions()` directly as async calls
- **Files modified:** src/broker.py
- **Verification:** Module imports successfully, method inspection confirms coroutine signatures
- **Committed in:** d391f98

**2. [Rule 1 - Bug] Fixed missing market_value field on CurrentPosition**
- **Found during:** Task 1 (broker.py implementation)
- **Issue:** Plan referenced `p.market_value` but CurrentPosition has no such field
- **Fix:** Computed market_value as `mark_price * quantity` with fallback to `average_open_price`
- **Files modified:** src/broker.py
- **Verification:** Field access confirmed against SDK model inspection
- **Committed in:** d391f98

---

**Total deviations:** 2 auto-fixed (2 bugs from plan/SDK mismatch)
**Impact on plan:** Both fixes necessary for correctness. No scope creep.

## Issues Encountered
None beyond the SDK field mismatches documented above.

## User Setup Required
None - no external service configuration required. Tastytrade credentials configured in previous plan (01-01).

## Next Phase Readiness
- Broker client ready for order execution in Phase 2 (place_order, dry_run_order methods to be added)
- OTC filter ready for screening pipeline integration
- CLI provides foundation for full trading cycle entry point
- All Phase 1 success criteria are now achievable

## Self-Check: PASSED

All 4 created files verified on disk. Both task commits (d391f98, f94de78) verified in git log.

---
*Phase: 01-foundation*
*Completed: 2026-03-19*
