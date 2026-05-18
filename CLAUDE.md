# Claude Code Handoff — 0DTE Options Trading Bot

## Project in one sentence
Automated 0DTE options trading bot targeting $100/day via vertical spreads on SPY/QQQ/IWM, using a dual-LLM consensus engine (GPT-4o-mini + Claude Sonnet) routed through OpenRouter, with a TastyTrade brokerage backend.

## Current branch
`use-strategies` — branched from `main` after merging `feature/0dte-options-100-target`.

## How to run
```bash
uv run python -m src options --window morning   # dry run (DRY_RUN=true in .env)
DRY_RUN=false uv run python -m src options --window midday
uv run streamlit run dashboard.py
uv run pytest tests/ -v -k "not live"
```

## Strategy stack (in priority order)

Every cycle window (morning/midday/close) evaluates strategies in this cascade and takes the first signal that passes all gates:

### 1. Debit Spread — ORB / Mean Reversion / Pre-Close
**File:** `src/options_strategy.py` — `evaluate_signal_for_window()`  
**When:** Directional momentum is detected.
- **ORB** (9:45–10:15 ET): Price breaks ORB high/low by ≥0.15% → buy ATM call/put spread.
- **Mean Reversion** (11:00–14:00 ET): Price ≥0.5% from VWAP → fade the move.
- **Pre-Close** (15:00–15:45 ET): Strong trend intact (BULLISH/BEARISH bias) → ride it.

**Risk:** Max loss = debit paid. Theta works against you → close aggressively early.  
**Profit targets (dynamic):** 50% before 11am → 40% → 30% after 1pm.

### 2. Iron Condor
**File:** `src/options_strategy.py` — `evaluate_iron_condor_signal()`  
**When:** `market_regime == "LOW_VOL"` (VIX < 15) AND `trend_bias == "NEUTRAL"` AND RSI 35–65 AND |VWAP slope| ≤ 0.001.  
**What:** Sells OTM put spread + OTM call spread simultaneously (1.5% OTM, $5-wide each). Collects premium from both sides.  
**Risk:** Max loss = worst side's width − total credit. Wins if SPY stays in range.  
**Executor:** `execute_iron_condor_trade()` in `src/orders.py`. Stores 2 DB rows (PUT_CREDIT + CALL_CREDIT) with `daily_session='iron_condor'`, counts as 1 trade.  
**Profit targets (dynamic):** 25% before noon → 40% → 60% after 2pm (let theta cook).

### 3. Credit Spread — Bull Put / Bear Call
**File:** `src/options_strategy.py` — `evaluate_credit_spread_signal()`  
**When:** No debit or condor signal. Fires in LOW_VOL or NORMAL regime.  
- **PUT_CREDIT** (bull put spread): BULLISH or NEUTRAL bias → sell OTM put 1% below price.
- **CALL_CREDIT** (bear call spread): BEARISH bias → sell OTM call 1% above price.

**Blocked if:** RSI < 30 (crash) or VWAP slope < −0.1% for puts; RSI > 70 or slope > +0.1% for calls.  
**Risk:** Max loss = (width − credit) × 100 × contracts.  
**Profit targets (dynamic):** 25% before noon → 40% → 60% after 2pm.

### Cross-cutting: Momentum Entry Filter
**File:** `src/options_strategy.py` — `passes_momentum_filter(ctx, direction, is_credit)`  
Applied to every signal before it's returned.
- **Debit CALL:** RSI ≥ 45 AND VWAP slope ≥ 0
- **Debit PUT:** RSI ≤ 55 AND VWAP slope ≤ 0
- **Credit PUT:** RSI ≥ 30 AND slope ≥ −0.001
- **Credit CALL:** RSI ≤ 70 AND slope ≤ +0.001
- **Iron condor (NEUTRAL):** RSI 35–65 AND |slope| ≤ 0.001
- Fail-open if both RSI and slope are None (pre-market / data gap).

### Cross-cutting: VIX-Based Position Sizing
**File:** `src/options_strategy.py` — `get_vix_size_multiplier(market_regime)`  
Applied in all three executors after base contract count is computed.
| Regime | VIX | Multiplier |
|--------|-----|-----------|
| LOW_VOL | < 15 | 1.5× |
| NORMAL | 15–25 | 1.0× |
| HIGH_VOL | 25–35 | 0.5× |
| EXTREME | > 35 | blocked upstream |

### Cross-cutting: Dynamic Profit Targets
**File:** `src/daily_target.py` — `get_dynamic_profit_target(spread_type, now_et)`  
Used in `check_and_close_open_spreads()` every cycle.
| Time | Debit (theta hurts) | Credit/Condor (theta helps) |
|------|--------------------|-----------------------------|
| < 11:00 | 50% | 25% |
| 11:00–13:00 | 40% | 40% |
| > 13:00 | 30% | — |
| > 14:00 | 30% | 60% |

## Key source files

| File | Purpose |
|------|---------|
| `src/options_cycle.py` | 15-stage orchestrator: auth → VIX → signal cascade → consensus → execute |
| `src/options_strategy.py` | All signal evaluators + momentum filter + VIX sizing + strike selection |
| `src/orders.py` | Executors: `execute_spread_trade`, `execute_credit_spread_trade`, `execute_iron_condor_trade` |
| `src/broker.py` | TastytradeClient: `place_vertical_spread`, `place_credit_spread`, `place_iron_condor` |
| `src/daily_target.py` | $100/day P&L tracking, dynamic profit targets, circuit breaker integration |
| `src/market_context.py` | Fetches VIX, ORB, VWAP, RSI-14, VWAP slope via yfinance |
| `src/consensus.py` | Dual-LLM go/no-go on every signal before execution |
| `src/config.py` | All settings via pydantic-settings + .env |
| `src/db.py` | SQLite schema: spread_positions, daily_options_target, session_cache |
| `dashboard.py` | Streamlit dashboard: run log browser + sector screener |

## Database tables (SQLite at data/trading_bot.db)
- `spread_positions` — open/closed spreads with P&L, OCC symbols, spread_type
- `daily_options_target` — daily P&L state: realized_pnl, trades_today, target_hit, stop_loss_hit
- `session_cache` — cached TastyTrade OAuth session
- `circuit_breaker` — daily loss limit state
- `trades` — legacy equity trade log

## Safety rails
- `DRY_RUN=true` in `.env` — all orders validated but never submitted
- VIX EXTREME (> 35) → cycle halts immediately (stage 3)
- Circuit breaker (stage 5) → halts if daily loss > 10% or drawdown > 30%
- Daily target gate (stage 7) — stops new trades if $100 hit or $150 loss hit
- Lockfile at `data/options_cycle.lock` — prevents concurrent cycle runs

## Next work (branch: use-strategies)
1. **Orchestrator** — scheduler/cron to auto-run the cycle at each window (morning/midday/close/monitor). Replace manual `--window` invocation.
2. **Web UI** — real-time dashboard showing:
   - Live strategy signal status (what fired, what was blocked and why)
   - Open spread positions with current P&L
   - Daily P&L progress toward $100 target
   - Market context (VIX, RSI, VWAP slope, regime)
   - Trade history with close reasons

## Environment setup
```bash
cp .env.example .env   # fill in TT_CLIENT_ID, TT_SECRET, TT_REFRESH, OPENROUTER_API_KEY
uv run python scripts/get_refresh_token.py   # one-time OAuth flow
uv sync
```
See `README-Local.md` for full setup guide.
