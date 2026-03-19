---
phase: 02-execution-and-intelligence
plan: 01
subsystem: ai-consensus
tags: [openai, anthropic, pydantic, structured-output, consensus, adversarial-prompting]

# Dependency graph
requires:
  - phase: 01-foundation
    provides: "SQLite schema with llm_audit and consensus_decisions tables, Settings/config pattern, broker AccountSnapshot"
provides:
  - "TradeRecommendation, TradingAnalysis, ConsensusResult Pydantic models"
  - "BULL_SYSTEM and BEAR_SYSTEM adversarial prompt templates"
  - "build_user_prompt function for portfolio-aware prompting"
  - "query_bull (OpenAI GPT-5.4-mini) and query_bear (Anthropic Claude Sonnet 4.6) LLM clients"
  - "evaluate_consensus veto logic function"
  - "run_consensus_cycle full orchestration with SQLite audit logging"
  - "20 passing unit tests covering all AIDC requirements"
affects: [02-02-order-execution, 02-03-position-sizing, 03-orchestration]

# Tech tracking
tech-stack:
  added: []
  patterns: ["Native Pydantic structured output via parse() on both OpenAI and Anthropic SDKs", "Adversarial bull/bear role assignment (GPT=bull, Claude=bear)", "Veto consensus with min-confidence gating"]

key-files:
  created: [src/consensus.py, src/prompts.py, tests/__init__.py, tests/conftest.py, tests/test_consensus.py]
  modified: [src/models.py, src/config.py, pyproject.toml]

key-decisions:
  - "GPT-5.4-mini as bull (aggressive), Claude Sonnet 4.6 as bear (skeptical) -- fixed roles, not rotating"
  - "Min confidence (not average) used for threshold gating -- more conservative"
  - "Max stop_loss_pct from both models used for approved trades -- more conservative"
  - "Connection not closed in run_consensus_cycle -- caller manages lifecycle for testability"

patterns-established:
  - "TDD workflow: write failing tests first, implement to pass, commit"
  - "Mock LLM clients via unittest.mock.patch for deterministic testing"
  - "In-memory SQLite fixtures for database-dependent tests"

requirements-completed: [AIDC-01, AIDC-02, AIDC-03, AIDC-04]

# Metrics
duration: 4min
completed: 2026-03-19
---

# Phase 02 Plan 01: Multi-LLM Consensus Engine Summary

**Adversarial bull/bear consensus engine using GPT-5.4-mini and Claude Sonnet 4.6 with native Pydantic structured output and veto logic**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-19T14:39:00Z
- **Completed:** 2026-03-19T14:43:38Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments

- TradeRecommendation, TradingAnalysis, and ConsensusResult Pydantic models with field validation (confidence 0.0-1.0, stop_loss_pct 0.01-0.50)
- Bull/bear adversarial prompt system with build_user_prompt generating portfolio-aware context
- Full consensus engine: query_bull (OpenAI parse), query_bear (Anthropic parse), evaluate_consensus (veto logic), run_consensus_cycle (orchestration with SQLite audit)
- 20 unit tests covering all AIDC-01 through AIDC-04 requirements -- all passing

## Task Commits

Each task was committed atomically:

1. **Task 1: Define Pydantic schemas, prompt templates, and test scaffold** - `067b10f` (feat)
2. **Task 2: Implement consensus engine with LLM clients and veto logic** - `5d887ce` (feat)

_Note: TDD tasks -- tests written in Task 1, implementation in Task 2._

## Files Created/Modified

- `src/models.py` - Added TradeRecommendation, TradingAnalysis, ConsensusResult Pydantic models
- `src/prompts.py` - BULL_SYSTEM, BEAR_SYSTEM prompt constants and build_user_prompt function
- `src/consensus.py` - Full consensus engine: query_bull, query_bear, evaluate_consensus, run_consensus_cycle with audit logging
- `src/config.py` - Added openai_model, anthropic_model, consensus_temperature, consensus_max_tokens, min_confidence settings
- `pyproject.toml` - Added [tool.pytest.ini_options] with testpaths and asyncio_mode
- `tests/__init__.py` - Test package init
- `tests/conftest.py` - Shared fixtures: mock_settings, test_db, sample_positions, sample_bull/bear_analysis
- `tests/test_consensus.py` - 20 unit tests covering schema validation, prompt building, consensus logic, LLM mocking, DB audit

## Decisions Made

- GPT-5.4-mini assigned bull role, Claude Sonnet 4.6 assigned bear role (fixed, not rotating) -- amplifies natural model tendencies
- Min confidence used as threshold (not average) -- more conservative, ensures both models are confident
- Max stop_loss_pct from both models used for approved trades -- takes more conservative stop
- DB connection not closed in run_consensus_cycle -- enables testability with in-memory SQLite fixtures

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed conn.close() closing test DB**
- **Found during:** Task 2 (test execution)
- **Issue:** run_consensus_cycle closed the DB connection in a finally block, which closed the test_db fixture before assertions could verify audit rows
- **Fix:** Removed conn.close() from run_consensus_cycle -- connection lifecycle managed by caller
- **Files modified:** src/consensus.py
- **Verification:** All 20 tests pass
- **Committed in:** 5d887ce (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix)
**Impact on plan:** Minor -- connection lifecycle change improves testability without affecting production behavior.

## Issues Encountered

None beyond the auto-fixed deviation above.

## User Setup Required

None - no external service configuration required. API keys already configured in .env from Phase 1.

## Next Phase Readiness

- Consensus engine ready for integration with order execution (02-02) and position sizing (02-03)
- ConsensusResult.approved_trades provides the input for the sizing module
- All LLM calls logged to llm_audit; all decisions logged to consensus_decisions
- Test infrastructure (conftest.py fixtures) available for subsequent test files

## Self-Check: PASSED

- All 9 files verified present on disk
- Both commits (067b10f, 5d887ce) verified in git log
- All grep content checks pass (classes, functions, table references)
- 20/20 tests passing

---
*Phase: 02-execution-and-intelligence*
*Completed: 2026-03-19*
