# Phase 4: Hardening - Context

**Gathered:** 2026-03-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Validate all Phases 1-3 code paths via dry-run end-to-end testing. Inject failure scenarios (LLM parse failure, circuit breaker trip, API timeout) and confirm the system handles each gracefully. No new features — this phase delivers operator confidence before the system runs unattended.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation decisions are Claude's discretion. Key areas:

- End-to-end dry-run test harness covering all pipeline stages
- Injected failure scenarios (JSON parse failure aborts cleanly, circuit breaker manual reset)
- Validation of all pitfall mitigations from research (OAuth2, OTC filter, PDT counter, spread check)
- How much to mock vs test against live (dry-run mode exists)
- Whether to create integration tests or extend existing unit tests

</decisions>

<canonical_refs>
## Canonical References

### Success Criteria (from ROADMAP.md)
1. `python trading_cycle.py --dry-run` completes full cycle without submitting orders
2. Simulated JSON parse failure aborts cleanly — no single-model fallback
3. Simulated circuit breaker trip produces correct halt + manual reset behavior
4. Dry-run output log contains enough detail to verify every decision

### Existing Test Suite
- `tests/test_consensus.py` — 20 tests (Phase 2)
- `tests/test_sizing.py` — 11 tests (Phase 2)
- `tests/test_orders.py` — 13 tests (Phase 2)
- `tests/conftest.py` — Shared fixtures

### All Source Modules
- `src/cycle.py`, `src/stoploss.py`, `src/circuit_breaker.py` (Phase 3)
- `src/consensus.py`, `src/sizing.py`, `src/orders.py` (Phase 2)
- `src/broker.py`, `src/db.py`, `src/config.py`, `src/cli.py` (Phase 1)
- `src/otc_filter.py`, `src/pdt.py`, `src/run_logger.py`, `src/prompts.py`

</canonical_refs>

<code_context>
## Existing Code Insights

### Test Infrastructure
- pytest 8.x + pytest-asyncio already installed
- `[tool.pytest.ini_options]` may need adding to pyproject.toml
- conftest.py has mock fixtures for tastytrade session and LLM clients

### Integration Points
- `src/cycle.py` `run_cycle()` is the main entry point to test end-to-end
- `--dry-run` flag propagates through all modules
- `run_logs/` directory captures JSON output for verification

</code_context>

<specifics>
## Specific Ideas

No specific requirements — Claude has full discretion.

</specifics>

<deferred>
## Deferred Ideas

None.

</deferred>

---

*Phase: 04-hardening*
*Context gathered: 2026-03-19*
