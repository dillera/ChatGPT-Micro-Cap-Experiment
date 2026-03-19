# Phase 1: Foundation - Context

**Gathered:** 2026-03-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Establish the persistent state store (SQLite), prove tastytrade OAuth2 authentication, sync live account positions and balances read-only, implement OTC ticker validation and PDT day-trade counter. No orders are placed in this phase — this is the stable foundation all downstream components build on.

</domain>

<decisions>
## Implementation Decisions

### CSV Migration
- Migrate all 11 weeks of historical data (portfolio snapshots AND individual trade records) into SQLite
- Parse trade log CSV into individual trade records for per-trade P&L analysis
- SQLite database lives at `data/trading_bot.db`
- Original CSV files stay in repo as-is (no deletion, no move) — they are no longer read by the new system
- SQLite is the single source of truth going forward; CSV is historical archive only

### Auth & Credentials
- Store tastytrade OAuth2 credentials (refresh_token, client_secret) in `.env` file, gitignored
- Load via pydantic-settings (handles .env and env vars transparently)
- User already has OAuth credentials provisioned — no developer portal setup needed
- Use async API from the start (tastytrade SDK is async-first; avoid sync-to-async migration later)
- Connect to live tastytrade account from day one (not sandbox) — Phase 1 is read-only, so no risk
- All other API keys (OpenAI, Anthropic) also go in `.env` for consistency

### Project Structure
- Claude's Discretion: whether to restructure as a Python package or keep flat scripts
- Claude's Discretion: pyproject.toml vs setup.py vs bare scripts
- Claude's Discretion: logging framework choice (loguru vs stdlib logging)

### Claude's Discretion
- SQLite schema design (tables, columns, indexes)
- OTC ticker validation approach (exchange lookup vs blocklist)
- PDT counter implementation details
- Whether to use alembic or manual migrations
- Test structure (if any tests are added in this phase)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### tastytrade SDK
- `.planning/research/STACK.md` — SDK version (tastyware/tastytrade v12.2.0), OAuth2 auth pattern, async requirement, session serialization
- `.planning/research/PITFALLS.md` — OTC prohibition, session-token auth deprecated, PDT rule details

### Architecture
- `.planning/research/ARCHITECTURE.md` — SQLite schema design, state store as foundation, build order rationale
- `.planning/codebase/ARCHITECTURE.md` — Existing data flow, CSV I/O patterns, FetchResult dataclass

### Existing Data
- `.planning/codebase/STACK.md` — Current dependencies, configuration patterns, environment variables
- `.planning/codebase/STRUCTURE.md` — File layout, CSV locations (`Scripts and CSV Files/`), naming conventions

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `trading_script.py` `download_price_data()` — Market data fetching with multi-source fallback; can be wrapped in async
- `trading_script.py` `FetchResult` dataclass — Pattern for typed return values; extend for tastytrade responses
- `trading_script.py` `last_trading_date()` — Weekend/holiday handling; reuse for market calendar checks
- `trading_script.py` `load_benchmarks()` — JSON config loading pattern; extend for .env config

### Established Patterns
- Snake_case functions, UPPER_CASE constants, PascalCase dataclasses
- CSV I/O via pandas DataFrame — migration script reads these directly
- Environment variable config (`ASOF_DATE`, `OPENAI_API_KEY`) — extend to tastytrade credentials
- Graceful fallback pattern (Yahoo → Stooq) — apply to tastytrade API error handling

### Integration Points
- `Scripts and CSV Files/chatgpt_portfolio_update.csv` — Source for historical portfolio migration
- `Scripts and CSV Files/chatgpt_trade_log.csv` — Source for historical trade migration
- `requirements.txt` — Will need tastytrade, pydantic-settings, aiosqlite additions

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches for infrastructure.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 01-foundation*
*Context gathered: 2026-03-19*
