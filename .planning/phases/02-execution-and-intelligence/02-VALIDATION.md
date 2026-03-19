---
phase: 2
slug: execution-and-intelligence
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-03-19
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 0.23.x |
| **Config file** | none — Wave 0 installs |
| **Quick run command** | `python -m pytest tests/ -x -q` |
| **Full suite command** | `python -m pytest tests/ -v` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/ -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | AIDC-01 | unit (mock APIs) | `python -m pytest tests/test_consensus.py::test_both_models_queried -x` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | AIDC-02 | unit | `python -m pytest tests/test_consensus.py::test_veto_consensus -x` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | AIDC-03 | unit | `python -m pytest tests/test_consensus.py::test_confidence_threshold -x` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | AIDC-04 | unit | `python -m pytest tests/test_consensus.py::test_disagreement_hold -x` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 1 | SIZE-01 | unit | `python -m pytest tests/test_sizing.py::test_compute_shares -x` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 1 | SIZE-02 | unit | `python -m pytest tests/test_sizing.py::test_high_conviction_cap -x` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 1 | SIZE-03 | unit | `python -m pytest tests/test_sizing.py::test_normal_conviction_cap -x` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 1 | SIZE-04 | unit | `python -m pytest tests/test_sizing.py::test_minimum_trade_floor -x` | ❌ W0 | ⬜ pending |
| 02-03-01 | 03 | 2 | BROK-04 | unit (mock SDK) | `python -m pytest tests/test_orders.py::test_limit_order_dry_run -x` | ❌ W0 | ⬜ pending |
| 02-03-01 | 03 | 2 | BROK-05 | unit (mock SDK) | `python -m pytest tests/test_orders.py::test_otoco_stop_companion -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/__init__.py` — package init
- [ ] `tests/conftest.py` — shared fixtures (mock tastytrade session, mock LLM clients, test DB)
- [ ] `tests/test_consensus.py` — covers AIDC-01, AIDC-02, AIDC-03, AIDC-04
- [ ] `tests/test_sizing.py` — covers SIZE-01, SIZE-02, SIZE-03, SIZE-04
- [ ] `tests/test_orders.py` — covers BROK-04, BROK-05
- [ ] `[tool.pytest.ini_options]` in pyproject.toml — config

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live tastytrade OTOCO order fill | BROK-04, BROK-05 | Requires real brokerage credentials and market hours | Run without --dry-run during market hours, verify order on tastytrade dashboard |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 5s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
