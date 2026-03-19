# Technology Stack

**Project:** Micro-Cap AI Autonomous Trading Bot
**Researched:** 2026-03-19
**Research Mode:** Ecosystem — Stack Dimension

---

## Decision Summary

The existing codebase uses Python 3.11+, pandas, yfinance, and openai. This stack extends that
foundation rather than replacing it, adding tastytrade brokerage integration, Claude API,
async execution patterns, and a scheduler. The largest architectural shift is moving from
synchronous scripts to async-capable code, driven by the tastyware SDK requiring `await`.

---

## Recommended Stack

### Brokerage Integration

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| tastyware/tastytrade | 12.2.0 | tastytrade API client — orders, accounts, positions | Best choice: actively maintained (68 releases, 95%+ test coverage, v12.2.0 released March 16, 2026), typed, async, 10x less code than raw REST. Supports dry_run mode for order validation before execution. Requires Python >=3.11, matching existing constraint. |

**Not recommended:** `tastytrade-sdk` (official, v1.2.0) — lower-level raw HTTP wrapper, requires more boilerplate for order construction. `tastytrade-api` (peter-oroszvari) — lower activity, fewer releases. Use tastyware.

**Authentication note:** As of tastytrade SDK v12.0.0+, the API requires OAuth. Session is created
with `Session(client_secret, refresh_token)` — not username/password. One-time setup via the
tastytrade Open API OAuth Applications dashboard. Refresh tokens never expire; the SDK auto-renews
15-minute session tokens. Sandbox available at `api.cert.tastyworks.com` via `is_test=True`.

**Confidence:** HIGH — verified against official docs at tastyworks-api.readthedocs.io (v12.2.0).

---

### LLM Integrations

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| openai | 2.29.0 | GPT-4 (and successor models) trading analysis | Already in codebase; v2.x SDK (released March 17, 2026). Python 3.9+ required. |
| anthropic | 0.86.0 | Claude consensus signal — second LLM | Official Anthropic SDK, released March 18, 2026. Python >=3.9. Simple `client.messages.create()` interface. |

**Model note for openai:** `chatgpt-4o-latest` was deprecated and removed from the API on
February 17, 2026. Update all model references to current production models. Verify current
model IDs against `https://platform.openai.com/docs/models` before implementing consensus engine.

**Confidence:** HIGH for SDK versions (verified via PyPI). MEDIUM for specific model IDs (OpenAI
deprecates and renames models frequently — verify at implementation time).

---

### Scheduling

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| APScheduler | 3.x (latest stable) | Daily autonomous trading cycle | Provides Python-native cron expressions, survives process restarts with persistent job stores, handles timezone-aware scheduling (critical for market hours). Superior to raw `cron` + scripts because it keeps execution inside the Python process where async context, logging, and error handling live. |

**Alternative considered:** OS-level cron calling a Python script. This works but loses process
state, requires re-authentication with tastytrade OAuth on every run, and produces fragmented
logging. APScheduler with a persistent SQLite job store is the better choice for a self-contained
bot.

**Confidence:** MEDIUM — APScheduler is the established standard, verified on PyPI. No trading-
specific concerns found. Version pinning should happen at implementation time.

---

### Async Runtime

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| asyncio (stdlib) | Python 3.11+ | Async execution for tastytrade SDK | tastyware SDK is fully async: `session`, `account.place_order()`, `Equity.get()` all require `await`. No additional library needed beyond Python's stdlib asyncio. |

**Implication for existing code:** The existing `ProcessPortfolio.py` and `simple_automation.py`
are synchronous. The new trading engine must wrap the async tastytrade calls in an async event
loop (or use `asyncio.run()` at the entry point). Keep LLM calls (openai, anthropic) as blocking
sync calls unless async variants are needed — both SDKs support both modes.

**Confidence:** HIGH — verified in tastytrade SDK documentation (orders.rst shows `await account.place_order()`).

---

### Configuration and Secrets

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| pydantic-settings | 2.9.1 | Type-safe config from environment variables | Validates all config at startup (fails fast on missing API keys), reads `.env` files, provides typed access to TASTYTRADE_CLIENT_SECRET, TASTYTRADE_REFRESH_TOKEN, OPENAI_API_KEY, ANTHROPIC_API_KEY. Eliminates dict-based config lookups scattered through the codebase. |
| python-dotenv | latest | .env file loading (pydantic-settings dependency) | Pulled in automatically with pydantic-settings. Enables local development with a `.env` file without touching environment. |

**Confidence:** HIGH — pydantic-settings is the documented standard pattern for this use case,
verified on official Pydantic docs.

---

### Logging

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| loguru | latest stable | Structured application logging | Trading bots require persistent audit trails. Loguru provides one-line setup, automatic log rotation, structured output (JSON sink for trade events), and exception capture with full tracebacks. Far less boilerplate than stdlib logging for an application of this size. |

**Use structlog instead if:** the project grows to need log aggregation pipelines (Datadog,
Splunk). For a single-process autonomous bot logging to file + stdout, loguru is the right
weight.

**Confidence:** MEDIUM — ecosystem recommendation, multiple independent sources agree. No
trading-specific verification needed.

---

### Notifications

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| python-telegram-bot | latest stable | Trade execution and daily summary notifications | No additional infrastructure needed (Telegram is free), async-native, widely used in trading bot community. Delivers: trade placed, stop-loss triggered, daily P&L summary, circuit-breaker halts. |

**Alternative:** Email via `smtplib` (stdlib). Works but Telegram notifications are faster,
mobile-friendly, and don't require SMTP server configuration. For a $1K account running
autonomously, immediate mobile alerts are more valuable than email.

**Skip:** Slack (paid tier required for bots), Discord (gaming connotation, no advantage here),
SMS via Twilio (cost per message adds up).

**Confidence:** MEDIUM — Telegram bot pattern is well-established in Python trading community.
Verified python-telegram-bot exists and is maintained; specific version to pin at implementation.

---

### Data and Market Data

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| yfinance | 0.2.65 (existing) | Market data — OHLCV, fundamentals for screening | Keep existing; proven working with fallback to Stooq. Update to latest stable at implementation. |
| pandas | 2.2.2 (existing) | Portfolio state, P&L calculations, data manipulation | Keep existing. No reason to change. |
| numpy | 2.3.2 (existing) | Sharpe/Sortino/CAPM calculations | Keep existing. |

**Tastytrade market data:** The tastyware SDK includes a data streamer for real-time quotes.
However, for a daily-cycle bot that does its analysis pre-market, yfinance remains sufficient for
screening. The tastytrade data streamer is worth using for live price checks at order placement.

**Confidence:** HIGH — existing stack, already proven in production over 11 weeks.

---

### Project Packaging

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| pyproject.toml | PEP 517/518 | Project metadata and dependency declaration | The existing `requirements.txt` approach is insufficient for a production bot — no lockfile, no Python version constraint, no build metadata. Migrate to pyproject.toml. |
| uv | latest | Package manager and lockfile | 10-100x faster than pip, Rust-based, generates `uv.lock` for reproducible deploys. Critical when running on a scheduler where environment drift can break production. |

**Alternative:** Keep `requirements.txt` with pip. Acceptable but `uv.lock` prevents "worked
yesterday, broken today" dependency surprises on a live trading system.

**Confidence:** MEDIUM — uv is the current best-practice for new Python projects (2025-2026),
verified via official uv docs and multiple independent sources. Migrating an existing project has
some friction but is straightforward.

---

## Full Dependency List

### Core (new additions to existing stack)

```bash
# Brokerage
uv add tastytrade          # tastyware SDK, currently 12.2.0

# LLMs
uv add openai              # currently 2.29.0 — update from existing
uv add anthropic           # currently 0.86.0 — new addition

# Scheduling
uv add apscheduler

# Config / secrets
uv add pydantic-settings   # currently 2.9.1

# Logging
uv add loguru

# Notifications
uv add python-telegram-bot
```

### Keep from existing stack

```bash
# Already working — retain
uv add pandas              # 2.2.2 or latest
uv add numpy               # 2.3.2 or latest
uv add yfinance            # 0.2.65 or latest
uv add matplotlib          # 3.8.4 or latest (performance charts)
uv add requests            # HTTP fallback for Stooq
uv add pandas-datareader   # Stooq fallback data source
```

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Brokerage SDK | tastyware/tastytrade | tastytrade-sdk (official) | Official SDK is a thin HTTP wrapper requiring more boilerplate. tastyware has typed models, dry_run, and auto-refresh. |
| Brokerage SDK | tastyware/tastytrade | tastytrade-api (peter-oroszvari) | Lower release velocity, fewer contributors, less documentation. |
| Scheduling | APScheduler | OS cron + script | Cron requires re-auth per run, fragments logging, no persistent state. |
| Config | pydantic-settings | os.environ dict access | Raw env access has no validation — bot silently fails if TASTYTRADE_REFRESH_TOKEN is missing. |
| Logging | loguru | stdlib logging | stdlib requires 10+ lines of boilerplate. loguru is 1 line + structured sinks. |
| Logging | loguru | structlog | structlog is overkill for a single-process bot without a log aggregation pipeline. |
| Notifications | python-telegram-bot | smtplib email | Email is slower, requires SMTP config, less mobile-friendly for urgent trade alerts. |
| Package mgr | uv + pyproject.toml | pip + requirements.txt | No lockfile in requirements.txt means environment drift. uv.lock solves this. |

---

## Key Architecture Implication

The tastyware SDK is **fully async**. This is the most impactful stack constraint. The trading
engine entry point must use `asyncio.run()`, and the core trading cycle must be an async function.
LLM API calls (openai, anthropic) can remain synchronous inside the async function using normal
`await asyncio.to_thread(...)` if blocking calls are needed, or use the async client variants
both SDKs provide.

Do NOT mix sync and async carelessly — calling `asyncio.run()` from inside an already-running
event loop will raise a `RuntimeError`. APScheduler 4.x supports async jobs natively; confirm
version compatibility before pinning.

---

## Environment Variables Required

```
TASTYTRADE_CLIENT_SECRET=...
TASTYTRADE_REFRESH_TOKEN=...
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Never hardcode. Load exclusively via pydantic-settings / `.env`.

---

## Sources

- tastyware/tastytrade GitHub: https://github.com/tastyware/tastytrade (v12.2.0, March 16, 2026)
- tastytrade SDK docs: https://tastyworks-api.readthedocs.io/en/latest/orders.html
- tastytrade Sessions docs: https://tastyworks-api.readthedocs.io/en/latest/sessions.html
- tastytrade developer portal: https://developer.tastytrade.com/api-overview/
- anthropic PyPI: https://pypi.org/project/anthropic/ (v0.86.0, March 18, 2026) — HIGH confidence
- openai PyPI: https://pypi.org/project/openai/ (v2.29.0, March 17, 2026) — HIGH confidence
- tastytrade-sdk PyPI: https://pypi.org/project/tastytrade-sdk/ (v1.2.0, official) — HIGH confidence
- pydantic-settings docs: https://docs.pydantic.dev/latest/concepts/pydantic_settings/ — HIGH confidence
- APScheduler docs: https://apscheduler.readthedocs.io/en/latest/ — HIGH confidence
- uv docs: https://docs.astral.sh/uv/concepts/projects/config/ — HIGH confidence
- loguru GitHub: https://github.com/Delgan/loguru — MEDIUM confidence (ecosystem consensus)

---

*Research date: 2026-03-19 | Confidence: HIGH (brokerage, LLM SDKs) / MEDIUM (scheduling, notifications)*
