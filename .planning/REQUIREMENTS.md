# Requirements: Micro-Cap AI Trading Bot

**Defined:** 2026-03-19
**Core Value:** Autonomously generate profitable trades on a small tastytrade account with aggressive but controlled risk management

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Brokerage

- [x] **BROK-01**: System authenticates with tastytrade via OAuth2 and manages session tokens
- [x] **BROK-02**: System fetches live account balance and buying power at cycle start
- [x] **BROK-03**: System syncs live positions from tastytrade as source of truth
- [ ] **BROK-04**: System places limit orders with dry_run validation before submission
- [ ] **BROK-05**: System files companion GTC stop orders on every new buy for overnight protection

### AI Decision

- [ ] **AIDC-01**: System queries both GPT-4 and Claude with adversarial prompts (bull/bear)
- [ ] **AIDC-02**: Both models must agree on action for trade to execute (veto consensus)
- [ ] **AIDC-03**: Both models must report confidence >= 0.6 for trade to proceed
- [ ] **AIDC-04**: Disagreements default to HOLD with full logging of each model's reasoning

### Operations

- [ ] **OPER-01**: System runs complete daily trading cycle without human trigger
- [ ] **OPER-02**: System checks stop-losses against live positions before LLM calls
- [ ] **OPER-03**: Circuit breaker halts trading if daily loss exceeds 10% of opening balance
- [ ] **OPER-04**: Circuit breaker halts trading if drawdown exceeds 30% from all-time high
- [ ] **OPER-05**: Tripped circuit breakers require manual override to resume

### Sizing & Logging

- [x] **SIZE-01**: Position sizing computed programmatically from confidence scores and buying power
- [x] **SIZE-02**: High conviction (>= 0.75) allows up to 40% of buying power per trade
- [x] **SIZE-03**: Normal conviction (>= 0.6) allows up to 20% of buying power per trade
- [x] **SIZE-04**: No trade smaller than $50 (commission protection)
- [ ] **LOGS-01**: Structured JSON run log written after every cycle with full state snapshot

### Infrastructure

- [x] **INFR-01**: SQLite database replaces CSV files as primary state store
- [x] **INFR-02**: OTC/penny stock ticker validation before any order attempt
- [x] **INFR-03**: PDT day-trade counter prevents account lockout (max 3 day trades per 5 days)
- [x] **INFR-04**: System supports --dry-run flag for full cycle without order submission

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Notifications

- **NOTF-01**: Trade execution alerts via Telegram with ticker, action, price, rationale
- **NOTF-02**: Stop-loss trigger alerts with position P&L
- **NOTF-03**: Daily summary notification with positions, P&L, circuit breaker status

### Biotech Specialization

- **BIOT-01**: Inject upcoming FDA/trial catalyst dates into LLM prompts
- **BIOT-02**: BioPharmCatalyst or CatalystAlert API integration for event data

## Out of Scope

| Feature | Reason |
|---------|--------|
| Options trading | Complexity too high for v1; risk can exceed account value |
| Web UI / dashboard | Zero trading value; Telegram/CLI sufficient for single operator |
| Intraday monitoring | Micro-cap catalysts play out over days, not minutes; native stops cover gaps |
| Backtesting framework | 11-week live experiment IS the backtest; forward-test with real money |
| Multi-account support | Single tastytrade account constraint |
| LLM debate / multi-round reasoning | Adds cost and failure modes without validated benefit at this scale |
| Self-modifying prompts | Direct path to uncontrolled behavior in financial system |
| Automatic circuit breaker reset | Removes human checkpoint that exists for safety |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| BROK-01 | Phase 1 | Complete |
| BROK-02 | Phase 1 | Complete |
| BROK-03 | Phase 1 | Complete |
| BROK-04 | Phase 2 | Pending |
| BROK-05 | Phase 2 | Pending |
| AIDC-01 | Phase 2 | Pending |
| AIDC-02 | Phase 2 | Pending |
| AIDC-03 | Phase 2 | Pending |
| AIDC-04 | Phase 2 | Pending |
| OPER-01 | Phase 3 | Pending |
| OPER-02 | Phase 3 | Pending |
| OPER-03 | Phase 3 | Pending |
| OPER-04 | Phase 3 | Pending |
| OPER-05 | Phase 3 | Pending |
| SIZE-01 | Phase 2 | Complete |
| SIZE-02 | Phase 2 | Complete |
| SIZE-03 | Phase 2 | Complete |
| SIZE-04 | Phase 2 | Complete |
| LOGS-01 | Phase 3 | Pending |
| INFR-01 | Phase 1 | Complete |
| INFR-02 | Phase 1 | Complete |
| INFR-03 | Phase 1 | Complete |
| INFR-04 | Phase 1 | Complete |

**Coverage:**
- v1 requirements: 23 total
- Mapped to phases: 23
- Unmapped: 0

---
*Requirements defined: 2026-03-19*
*Last updated: 2026-03-19 after roadmap creation*
