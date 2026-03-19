# Architecture Patterns: Autonomous Micro-Cap Trading Bot

**Domain:** Autonomous algorithmic trading with LLM consensus decision engine
**Researched:** 2026-03-19
**Overall Confidence:** HIGH (tastytrade SDK well-documented; LLM consensus patterns well-established; state management recommendations verified)

---

## Recommended Architecture

The system is a **scheduled pipeline executor** — not event-driven, not real-time. A cron trigger fires once per trading day, moves data through a deterministic pipeline of stages, and terminates. Each stage has a clear input and output contract. This maps well to the daily micro-cap strategy and keeps complexity low.

```
┌─────────────────────────────────────────────────────────────────┐
│                        CRON / SCHEDULER                         │
│                    (APScheduler or system cron)                  │
└─────────────────────────────┬───────────────────────────────────┘
                              │ trigger at market open (9:31 ET)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         ORCHESTRATOR                             │
│                    (trading_cycle.py)                            │
│  Coordinates stage execution, owns circuit breaker state,        │
│  handles top-level exceptions, sends notifications               │
└───┬─────────────┬──────────────┬──────────────┬─────────────────┘
    │             │              │              │
    ▼             ▼              ▼              ▼
┌───────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐
│Broker │  │  Market  │  │   LLM    │  │   Risk Mgmt  │
│  API  │  │   Data   │  │Consensus │  │    Module    │
│Layer  │  │  Layer   │  │ Engine   │  │              │
└───────┘  └──────────┘  └──────────┘  └──────────────┘
    │             │              │              │
    └──────┬──────┘              │              │
           ▼                     │              │
    ┌──────────────┐             │              │
    │ State Store  │◄────────────┘◄─────────────┘
    │  (SQLite)    │
    └──────────────┘
           │
           ▼
    ┌──────────────┐
    │Notifications │
    │  (stdout +   │
    │  email/SMS)  │
    └──────────────┘
```

---

## Component Boundaries

### 1. Orchestrator (`trading_cycle.py`)

**Responsibility:** Own the daily trading cycle. Call each stage in order. Gate on circuit breaker status before allowing LLM or execution stages to run. Never contain business logic — it only sequences and handles failures.

**Communicates with:** All other components (as coordinator, not peer).

**Inputs:** Cron trigger, environment variables (API keys, config)
**Outputs:** Completion status, trigger for notifications

**Key design constraint:** The orchestrator reads circuit breaker state from the State Store at startup. If the breaker is tripped, it logs, notifies, and exits without calling any trading stages. This prevents a runaway loss spiral if the cron fires again while a halt is active.

---

### 2. Brokerage API Layer (`broker/tastytrade_client.py`)

**Responsibility:** Wrap the `tastytrade` SDK (tastyware unofficial SDK, version 12.x). Expose a synchronous facade over the async SDK methods so the rest of the system does not need to manage an event loop. Handle session lifecycle, token refresh, and all network errors.

**Communicates with:** Orchestrator (called by), State Store (writes positions/balances after sync)

**Primary operations:**
- `authenticate()` — create `Session` from `TASTYTRADE_CLIENT_SECRET` + `TASTYTRADE_REFRESH_TOKEN` env vars
- `get_account_state()` → `AccountSnapshot` — calls `account.get_balances()` + `account.get_positions()`, returns normalized dataclass
- `place_order(symbol, shares, action, order_type, price)` → `OrderResult` — wraps `account.place_order(session, NewOrder(...), dry_run=False)`
- `dry_run_order(...)` → `BuyingPowerEffect` — same as above with `dry_run=True` for pre-flight validation

**Session management pattern:** The `Session` object supports `serialize()` / `deserialize()`, enabling token reuse across daily runs without re-authentication. Store serialized session in State Store; deserialize at startup; call `session.refresh()` to extend. Fall back to full re-auth if refresh fails.

**SDK reference:**
```python
from tastytrade import Session, Account
from tastytrade.instruments import Equity
from tastytrade.order import NewOrder, OrderAction, OrderType, OrderTimeInForce

# Auth
session = Session(provider_secret=os.environ['TT_CLIENT_SECRET'],
                  refresh_token=os.environ['TT_REFRESH_TOKEN'])

# Get account
account = await Account.get(session)

# Place equity buy
symbol = await Equity.get(session, 'RXRX')
leg = symbol.build_leg(50, OrderAction.BUY_TO_OPEN)
order = NewOrder(
    time_in_force=OrderTimeInForce.DAY,
    order_type=OrderType.LIMIT,
    legs=[leg],
    price=Decimal('12.50')
)
response = await account.place_order(session, order, dry_run=False)
```

**Confidence:** HIGH — sourced from official tastytrade SDK docs at tastyworks-api.readthedocs.io

---

### 3. Market Data Layer (`data/market_data.py`)

**Responsibility:** Fetch current prices for screening candidates and existing positions. Preserve the existing multi-source fallback chain (Yahoo → Stooq) since it is proven and working. Extend to also pull news/catalyst summaries from a financial news API for the LLM prompt context.

**Communicates with:** Orchestrator (called by), LLM Consensus Engine (provides enriched data to prompt builder)

**Key design decision:** Keep this layer stateless. It fetches and returns; it does not write to the State Store. The Orchestrator decides what to persist.

**Data contract out:**
```python
@dataclass
class TickerSnapshot:
    symbol: str
    price: float
    price_date: date
    price_source: str          # "yahoo", "stooq-pdr", "stooq-csv"
    week_change_pct: float
    news_headlines: list[str]  # up to 5 recent headlines for LLM context
```

---

### 4. LLM Consensus Engine (`llm/consensus_engine.py`)

**Responsibility:** Query GPT-4 and Claude independently with identical prompts. Parse both responses. Apply consensus rules. Return a single `ConsensusDecision` or raise `NoConsensusError`.

**Communicates with:** Orchestrator (called by), State Store (reads current portfolio context, writes LLM audit trail)

**Internal structure:**

```
ConsensusEngine
├── PromptBuilder          — builds structured prompt from portfolio + market data
├── OpenAIClient           — wraps openai SDK, temp=0.3, JSON mode
├── AnthropicClient        — wraps anthropic SDK, temp=0.3, structured output
├── ResponseParser         — validates and normalizes both responses into TradeRecommendation list
└── ConsensusMatcher       — applies agreement rules, returns ConsensusDecision
```

**Consensus rules (opinionated — evidence from LLM Council / TradingAgents research):**

1. Both LLMs must independently recommend the same action (BUY/SELL/HOLD) for the same ticker for a consensus to exist.
2. If GPT-4 says BUY RXRX and Claude says HOLD RXRX — no consensus, skip that ticker.
3. If both say BUY on a ticker but at different confidence levels, use the lower confidence as the consensus confidence.
4. A `HOLD` consensus (both say hold everything) is a valid consensus — no trades execute.
5. If LLM parsing fails for either model, abort the entire trading cycle (do not fall back to single-model decisions). Log and notify.

**Rationale for strict consensus:** The project spec requires both models to agree. Single-model decisions are explicitly out of scope. Failure to parse is more informative than a silent degradation.

**Data contracts:**

```python
@dataclass
class TradeRecommendation:
    action: Literal['BUY', 'SELL', 'HOLD']
    symbol: str
    shares: int
    price: float          # suggested entry/exit price
    stop_loss: float
    confidence: float     # 0.0 - 1.0
    reason: str
    model: str            # "gpt-4" or "claude-3-5-sonnet"

@dataclass
class ConsensusDecision:
    agreed_trades: list[TradeRecommendation]  # de-duped, single entry per ticker
    gpt4_raw_response: str                    # for audit trail
    claude_raw_response: str                  # for audit trail
    consensus_timestamp: datetime
    no_consensus_tickers: list[str]           # tickers where models disagreed
```

**LLM audit trail:** Every call writes to `llm_audit` table in SQLite. Never discard LLM responses even on failure — these are the primary debugging tool.

---

### 5. Risk Management Module (`risk/risk_manager.py`)

**Responsibility:** Gate every proposed trade against configured risk rules before it reaches the broker. The orchestrator calls `risk_manager.validate(proposed_trades, account_state)` and receives either approved trades or rejection reasons. Also owns circuit breaker evaluation.

**Communicates with:** Orchestrator (called by), State Store (reads daily P&L for circuit breaker check)

**Rules evaluated (in order, short-circuit on first failure):**

| Rule | Parameter | Default | Notes |
|------|-----------|---------|-------|
| Circuit breaker: daily loss | `MAX_DAILY_LOSS_PCT` | 10% | Halt if today's realized+unrealized loss exceeds X% of start-of-day equity |
| Circuit breaker: max drawdown | `MAX_DRAWDOWN_PCT` | 25% | Halt if peak-to-current drawdown exceeds X% |
| Single position size | `MAX_POSITION_PCT` | 50% | No single position can exceed X% of portfolio |
| Minimum cash reserve | `MIN_CASH_RESERVE` | $50 | Always keep at least this much cash uninvested |
| Order minimum | `MIN_ORDER_VALUE` | $10 | tastytrade has no equity commission but very small orders waste spread |
| Price reasonableness | — | — | Proposed price must be within 5% of last known price |

**Circuit breaker state machine:**

```
ACTIVE ──[daily loss > MAX]──► HALTED_DAILY (auto-resets next trading day)
ACTIVE ──[drawdown > MAX]────► HALTED_DRAWDOWN (requires manual reset)
HALTED_DAILY ──[new day]────► ACTIVE
HALTED_DRAWDOWN ──[manual]──► ACTIVE
```

Circuit breaker state persists in SQLite so a system restart does not reset it.

**Data contract out:**

```python
@dataclass
class RiskValidationResult:
    approved_trades: list[TradeRecommendation]
    rejected_trades: list[tuple[TradeRecommendation, str]]  # (trade, reason)
    circuit_breaker_status: Literal['ACTIVE', 'HALTED_DAILY', 'HALTED_DRAWDOWN']
    halt_reason: str | None
```

---

### 6. State Store (`state/db.py`)

**Responsibility:** Single source of truth for all persistent state. Replaces the CSV files. Uses SQLite (not PostgreSQL) — rationale: single-process, single-account, no concurrent writers, file-based for portability and easy inspection.

**Communicates with:** All components (read/write)

**Schema:**

```sql
-- Current positions (replaces portfolio CSV)
CREATE TABLE positions (
    id          INTEGER PRIMARY KEY,
    symbol      TEXT NOT NULL,
    shares      REAL NOT NULL,
    buy_price   REAL NOT NULL,
    cost_basis  REAL NOT NULL,
    stop_loss   REAL,
    opened_at   TEXT NOT NULL,   -- ISO datetime
    updated_at  TEXT NOT NULL
);

-- Daily snapshots (replaces TOTAL rows in portfolio CSV)
CREATE TABLE daily_snapshots (
    id              INTEGER PRIMARY KEY,
    snapshot_date   TEXT NOT NULL UNIQUE,  -- YYYY-MM-DD
    total_equity    REAL NOT NULL,
    cash_balance    REAL NOT NULL,
    positions_value REAL NOT NULL,
    daily_pnl       REAL NOT NULL,
    daily_pnl_pct   REAL NOT NULL,
    peak_equity     REAL NOT NULL,         -- for drawdown calculation
    drawdown_pct    REAL NOT NULL
);

-- Trade log (replaces trade log CSV)
CREATE TABLE trades (
    id              INTEGER PRIMARY KEY,
    executed_at     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    action          TEXT NOT NULL,         -- BUY, SELL
    shares          REAL NOT NULL,
    price           REAL NOT NULL,
    total_value     REAL NOT NULL,
    commission      REAL DEFAULT 0,
    stop_loss       REAL,
    reason          TEXT,
    order_id        TEXT,                  -- tastytrade order ID
    source          TEXT NOT NULL          -- "llm_consensus", "stop_loss", "manual"
);

-- LLM audit trail (replaces llm_responses.jsonl)
CREATE TABLE llm_audit (
    id              INTEGER PRIMARY KEY,
    called_at       TEXT NOT NULL,
    model           TEXT NOT NULL,         -- "gpt-4", "claude-3-5-sonnet"
    prompt_hash     TEXT NOT NULL,         -- SHA256 of prompt for dedup detection
    raw_response    TEXT NOT NULL,
    parsed_ok       INTEGER NOT NULL,      -- boolean
    parse_error     TEXT,
    consensus_id    INTEGER                -- FK to consensus_decisions
);

-- Consensus decisions
CREATE TABLE consensus_decisions (
    id              INTEGER PRIMARY KEY,
    decided_at      TEXT NOT NULL,
    gpt4_audit_id   INTEGER NOT NULL,
    claude_audit_id INTEGER NOT NULL,
    agreed_tickers  TEXT,                  -- JSON array
    disagreed_tickers TEXT,               -- JSON array
    trades_executed INTEGER DEFAULT 0
);

-- Circuit breaker state
CREATE TABLE circuit_breaker (
    id          INTEGER PRIMARY KEY DEFAULT 1,  -- single row
    status      TEXT NOT NULL DEFAULT 'ACTIVE',
    tripped_at  TEXT,
    reason      TEXT,
    reset_at    TEXT
);

-- Session token cache
CREATE TABLE session_cache (
    id              INTEGER PRIMARY KEY DEFAULT 1,  -- single row
    serialized_session TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);
```

**Why SQLite over PostgreSQL:** This is a single-process, daily-batch system on one machine. SQLite handles the write volume trivially. Adding PostgreSQL adds operational burden (separate process, backups, connection pooling) with no benefit at this scale. If the system ever scales to multiple concurrent processes, migrate then.

**Migration from CSV:** The first run of the rebuilt system should include a migration script (`scripts/migrate_csv_to_sqlite.py`) that reads the existing CSV files and populates the SQLite schema. This is a one-time operation.

---

### 7. Notification Layer (`notifications/notifier.py`)

**Responsibility:** Send end-of-cycle summaries, trade confirmations, and alert conditions. Pluggable transport — start with stdout + email (smtplib), add SMS/Slack later without touching core logic.

**Communicates with:** Orchestrator (called by at end of each stage with status events)

**Event types:**
- `CYCLE_STARTED` — log only
- `TRADE_EXECUTED` — immediate notification (email/stdout)
- `STOP_LOSS_HIT` — immediate notification
- `CIRCUIT_BREAKER_TRIPPED` — immediate notification + halt
- `DAILY_SUMMARY` — end-of-cycle email with P&L, positions, LLM decision rationale
- `LLM_FAILURE` — immediate notification, cycle aborted

---

## Data Flow: Daily Trading Cycle

```
9:31 AM ET — Cron fires
│
├─ 1. CIRCUIT BREAKER CHECK
│     State Store → read circuit_breaker table
│     If HALTED: notify + exit
│
├─ 2. BROKER SYNC
│     Broker API Layer → get_account_state()
│     → AccountSnapshot {cash, positions, buying_power}
│     State Store ← write/reconcile positions table
│
├─ 3. MARKET DATA FETCH
│     For each held position + screening watchlist:
│     Market Data Layer → TickerSnapshot[]
│     (Yahoo → Stooq fallback, headlines fetch)
│
├─ 4. STOP-LOSS EVALUATION
│     Risk Manager → check each position against stop_loss
│     For triggered stops:
│       Broker API Layer → place_order(SELL)
│       State Store ← log trade, update position
│       Notifier ← STOP_LOSS_HIT event
│
├─ 5. LLM CONSENSUS
│     ConsensusEngine.prompt_builder(portfolio_state, market_data)
│     → parallel calls to GPT-4 and Claude
│     → parse both responses
│     → apply consensus rules → ConsensusDecision
│     State Store ← write llm_audit rows
│
├─ 6. RISK VALIDATION
│     Risk Manager → validate(consensus_trades, account_state)
│     → RiskValidationResult {approved, rejected, cb_status}
│
├─ 7. ORDER EXECUTION
│     For each approved trade:
│       Broker API Layer → dry_run_order() → verify buying power
│       Broker API Layer → place_order()
│       State Store ← log trade, update positions
│       Notifier ← TRADE_EXECUTED event
│
├─ 8. DAILY SNAPSHOT
│     Broker API Layer → get_account_state()  (post-trade)
│     State Store ← write daily_snapshots row with P&L
│
└─ 9. NOTIFICATIONS
      Notifier ← DAILY_SUMMARY event
      (sends email: trades executed, P&L, LLM rationale, next stop-loss levels)
```

**Flow direction rules:**
- Data flows DOWN through stages (each stage receives output of previous)
- The State Store is the ONLY shared mutable state across stages
- No stage calls another stage directly — all cross-stage communication goes through the Orchestrator or State Store
- The Broker API Layer is never called directly by LLM or Risk components

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Single-Model Fallback
**What:** If Claude fails, fall back to GPT-4 decision alone
**Why bad:** Defeats the entire purpose of consensus. A single-model system during live trading hours is undetected degradation.
**Instead:** Abort the LLM stage, notify, and exit without executing any new trades. Existing positions and stop-losses still run (stages 2-4 are LLM-independent).

### Anti-Pattern 2: Stateless Stop-Loss Evaluation
**What:** Recalculate stop-losses from scratch each cycle by re-reading CSV
**Why bad:** If CSV is out of sync with actual broker positions, you miss stop-loss triggers. Existing system has this fragility.
**Instead:** Broker Sync (stage 2) is authoritative. Always reconcile State Store against live broker positions before evaluating stop-losses. Never trust local state alone.

### Anti-Pattern 3: Inline Risk Rules in Orchestrator
**What:** `if proposed_shares * price > 0.5 * total_equity: skip` in the main cycle function
**Why bad:** Risk rules proliferate and become untestable. Hard to tune parameters without touching orchestration logic.
**Instead:** All risk rules live in `risk_manager.py`. The orchestrator only calls `validate()` and acts on the result.

### Anti-Pattern 4: Async Everywhere
**What:** Make every module async because the tastytrade SDK is async
**Why bad:** Adds complexity to prompt building, SQLite writes, CSV migrations — none of which benefit from async.
**Instead:** Isolate async to the Broker API Layer. Use `asyncio.run()` at the boundary. Everything else is synchronous Python. One event loop, at the brokerage integration point only.

### Anti-Pattern 5: LLM Decides Position Size
**What:** Let the LLM output the number of shares to buy and use that directly
**Why bad:** LLMs don't know the current account balance or current price precisely. They hallucinate position sizes that exceed buying power or violate risk rules.
**Instead:** LLM recommends a symbol and action (BUY/SELL). The Risk Manager calculates actual share count based on current buying power and MAX_POSITION_PCT. LLM provides conviction (reason, confidence), system provides sizing.

---

## Suggested Build Order (Dependencies)

The components have hard dependencies that dictate build sequence:

```
Phase 1 (Foundation — no external dependencies)
└── State Store (SQLite schema + db.py)
    └── CSV migration script

Phase 2 (External integrations — depend on State Store)
├── Broker API Layer (tastytrade SDK wrapper)
│   └── Session management, account sync, order placement
└── Market Data Layer (extend existing fetchers)

Phase 3 (Decision layer — depends on State Store + Market Data)
├── LLM Consensus Engine
│   ├── Prompt Builder
│   ├── OpenAI + Anthropic clients
│   └── Consensus Matcher
└── Risk Management Module
    └── Circuit breaker state machine

Phase 4 (Orchestration — depends on all above)
├── Orchestrator (trading_cycle.py)
├── Scheduler (APScheduler or cron wrapper)
└── Notification Layer

Phase 5 (Hardening)
├── End-to-end dry-run test (dry_run=True on all broker calls)
└── Stop-loss validation against live positions
```

**Build order rationale:**
- State Store first because every other component reads/writes it. Building it first lets you test persistence in isolation.
- Broker API Layer second because it is purely I/O with clear inputs/outputs and can be tested with the tastytrade sandbox before LLM logic exists.
- LLM Engine third because prompts require knowing what portfolio state looks like from State Store, and what market data looks like from the data layer.
- Risk Manager alongside LLM Engine because it only depends on State Store (no LLM dependency — intentional, risk runs after consensus).
- Orchestrator last because it is glue code — it cannot be written until all stages it sequences exist.

---

## Scalability Considerations

This system is explicitly NOT designed to scale beyond a single account, daily cycle, equities-only strategy. The architecture should resist scope creep toward real-time or multi-account operation.

| Concern | At Current Scale | If Scaled Later |
|---------|-----------------|-----------------|
| State Store | SQLite, file-based | Migrate to PostgreSQL, add TimescaleDB for OHLCV |
| LLM Calls | Serial (GPT-4 then Claude) | Parallel (asyncio.gather) — already isolated |
| Scheduling | System cron or APScheduler in-process | Cloud scheduler (e.g., AWS EventBridge) |
| Notifications | stdout + smtplib | Replace notifier.py transport, same interface |
| Broker API | Single Session object | Add connection pool, multi-account session map |

---

## Sources

- tastytrade SDK documentation (HIGH confidence): https://tastyworks-api.readthedocs.io/en/latest/
- tastyware/tastytrade GitHub (HIGH confidence): https://github.com/tastyware/tastytrade
- tastytrade order placement docs (HIGH confidence): https://github.com/tastyware/tastytrade/blob/master/docs/orders.rst
- TradingAgents multi-LLM framework paper (MEDIUM confidence): https://arxiv.org/abs/2412.20138
- LLM Council consensus pattern (MEDIUM confidence): https://virtuslab.com/blog/ai/llm-council
- Trading bot risk management circuit breakers (MEDIUM confidence): https://3commas.io/blog/ai-trading-bot-risk-management-guide-2025
- APScheduler async scheduling (HIGH confidence): https://apscheduler.readthedocs.io/
- SQLite vs PostgreSQL for trading systems (MEDIUM confidence): https://medium.com/prooftrading/selecting-a-database-for-an-algorithmic-trading-system-2d25f9648d02
- Existing codebase architecture (HIGH confidence): .planning/codebase/ARCHITECTURE.md
