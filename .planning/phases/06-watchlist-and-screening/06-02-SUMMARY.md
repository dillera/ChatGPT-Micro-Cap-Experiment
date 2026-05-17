---
phase: 06-watchlist-and-screening
plan: 02
status: complete
completed_at: 2026-03-19
tests_added: 9
tests_total: 133
---

# Plan 06-02 Summary: LLM Discovery + Cycle Integration

## What Was Built

### src/prompts.py
- Added `DISCOVERY_SYSTEM` prompt — micro-cap equity scout role, instructs both LLMs to propose 3-5 new tickers on NYSE/NASDAQ only
- Added `build_discovery_prompt()` — builds context prompt with current holdings table, watchlist, and buying power; instructs LLMs to avoid duplicates

### src/consensus.py
- Added `_query_bull_discovery()` and `_query_bear_discovery()` — same pattern as bull/bear but use DISCOVERY_SYSTEM
- Added `run_discovery_cycle()` — queries both models, unions proposed symbols, validates each via yfinance exchange + OTC filter, returns accepted tickers. Non-fatal: any failure returns []
- Imports: `yfinance`, `validate_symbols` from `src.otc_filter`, `DISCOVERY_SYSTEM`/`build_discovery_prompt` from `src.prompts`

### src/cycle.py
- Added `gather_candidates(snapshot, dry_run) -> (list[str], dict)` — aggregates from watchlist + screener + discovery, deduplicates against held positions, logs counts
- Added Stage 5.5 between stop-loss (5) and consensus (6): calls `gather_candidates()`
- Stage 6 (Consensus) now combines `position_symbols + new_candidates` so both existing positions (SELL/HOLD analysis) and new candidates (BUY candidates) go through consensus
- `cycle_result["candidates"]` dict added: `{watchlist, screener, discovered, total_new}`
- New imports: `run_discovery_cycle`, `get_screener_candidates`, `get_active_symbols`

### tests/test_discovery.py (new — 9 tests)
- NYSE ticker accepted
- OTC ticker rejected
- Unknown exchange rejected
- Ticker in positions still returned (dedup is caller's job)
- Both models fail → empty list
- Single model failure → other model's proposals used
- yfinance lookup failure → rejected (no exchange = unknown = reject)
- NASDAQ alias (NMS) accepted
- Mixed NYSE + OTC → only NYSE accepted

### tests/test_cycle_e2e.py (updated)
- `_base_patches()` now includes mocks for `get_active_symbols`, `get_screener_candidates`, `run_discovery_cycle`
- `TestHappyPath` verifies `cycle_result["candidates"]` with expected counts (watchlist=2, screener=2, discovered=1)

## Requirements Satisfied
- **WATCH-03**: LLMs propose new ticker candidates via `run_discovery_cycle()`
- **WATCH-05**: Daily cycle gathers from watchlist + screener + LLM proposals via `gather_candidates()`

## Test Results
- 133 tests passing (9 new discovery tests + 12 E2E tests updated)
