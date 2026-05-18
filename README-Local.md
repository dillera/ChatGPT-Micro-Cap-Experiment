# Local Development Guide

This covers setting up and running the automated 0DTE options trading bot locally. The bot targets $100/day via vertical spreads on SPY/QQQ/IWM using a dual-LLM consensus engine routed through OpenRouter.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — fast Python package manager
- A [TastyTrade](https://tastytrade.com) brokerage account with API access
- An [OpenRouter](https://openrouter.ai) API key (routes both GPT-4o and Claude calls)

## 1. Install dependencies

```bash
uv sync
```

## 2. Configure environment

Copy the example and fill in your values:

```bash
cp .env.example .env
```

### Required `.env` values

```env
# TastyTrade OAuth2 — see Section 3 for how to get TT_REFRESH
TT_CLIENT_ID=your_client_id          # from TT developer portal
TT_SECRET=your_client_secret         # from TT developer portal
TT_REFRESH=your_refresh_token        # obtained by running the script below

# LLM routing via OpenRouter (covers GPT-4o-mini + Claude Sonnet)
OPENROUTER_API_KEY=sk-or-...

# Safety — always start with dry run
DRY_RUN=true
```

### Optional `.env` overrides

```env
# Model selection (OpenRouter model IDs)
OPENAI_MODEL=openai/gpt-4o-mini
ANTHROPIC_MODEL=anthropic/claude-sonnet-4-6

# Research screener limits
RESEARCH_MAX_MARKET_CAP=2000000000
RESEARCH_MIN_PRICE=2.0
RESEARCH_MAX_PER_RUN=30
EVAL_COOLDOWN_HOURS=24

# Logging
LOG_LEVEL=INFO
```

## 3. Get your TastyTrade refresh token

TastyTrade uses OAuth2. Run this once to get a long-lived refresh token — it opens your browser to the TT login page, you authenticate there (including 2FA), and the token is written to `.env` automatically.

```bash
uv run python scripts/get_refresh_token.py
```

**What it does:**
1. Reads `TT_CLIENT_ID` and `TT_SECRET` from `.env`
2. Opens `https://my.tastytrade.com/auth.html` in your browser
3. You log in (TT handles username, password, and 2FA — nothing goes through this script)
4. TT redirects to `http://localhost:18085/callback` — the script captures the auth code
5. Exchanges the code for a refresh token and writes `TT_REFRESH=...` to `.env`

Refresh tokens don't expire. Re-run only if you revoke access in the TT developer portal.

**Prerequisites:** Your redirect URI `http://localhost:18085/callback` must be registered in the [TT developer portal](https://developer.tastytrade.com) for your OAuth app.

## 4. Run the bot

### Dry run (safe — no real orders)

```bash
uv run python -m src
```

`DRY_RUN=true` in `.env` means all orders go through TastyTrade's dry-run validation but are never submitted.

### Live trading

```bash
DRY_RUN=false uv run python -m src
```

### Options cycle only (0DTE vertical spreads)

```bash
uv run python -m src --options-only
```

### Sync positions from TastyTrade without trading

```bash
uv run python -m src --sync-only
```

### Streamlit dashboard

```bash
uv run streamlit run dashboard.py
```

## 5. Run tests

### Mock tests (no credentials needed)

```bash
uv run pytest tests/ -v -k "not live"
```

### TastyTrade API smoke tests (hits real API, dry_run=True)

These verify OAuth is working and all broker API calls succeed. No orders are placed.

```bash
uv run pytest tests/test_broker.py -v -k live
```

The live tests are auto-skipped if `TT_SECRET` and `TT_REFRESH` aren't set.

### Full test suite

```bash
uv run pytest tests/ -v
```

## Project structure

```
src/
  broker.py          # TastytradeClient — all SDK calls live here
  cycle.py           # Main equity trading cycle
  options_cycle.py   # 0DTE vertical spread cycle ($100/day target)
  consensus.py       # Dual-LLM bull/bear consensus engine
  screener.py        # Sector screener for candidate symbols
  research.py        # Broad multi-strategy research pipeline
  cooldown.py        # Symbol eval cooldown tracker
  sizing.py          # Position sizing logic
  config.py          # All settings via pydantic-settings + .env
  db.py              # SQLite schema and helpers

scripts/
  get_refresh_token.py   # One-time OAuth2 flow to obtain TT_REFRESH

tests/
  test_broker.py         # Mock + live smoke tests for TastytradeClient
  test_cycle_e2e.py      # End-to-end cycle tests
  test_consensus.py      # LLM consensus engine tests
  test_discovery.py      # Symbol discovery tests
  conftest.py            # Shared fixtures
```

## Database

SQLite at `data/trading_bot.db`. Schema is auto-initialized on first run. Never commit `data/*.db`.

## Troubleshooting

**`TT_SECRET and TT_REFRESH must be set`** — Run `scripts/get_refresh_token.py` first.

**OAuth 403 on `my.tastytrade.com`** — Your redirect URI `http://localhost:18085/callback` isn't registered in the TT developer portal. Add it there.

**`No module named 'tastytrade'`** — Run `uv sync` to install dependencies.

**Circuit breaker tripped** — Daily loss limit hit. Check `data/trading_bot.db` circuit_breaker table or the dashboard.
