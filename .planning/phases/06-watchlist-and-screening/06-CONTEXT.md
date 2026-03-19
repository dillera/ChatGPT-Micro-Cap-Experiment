# Phase 6: Watchlist and Screening - Context

**Gathered:** 2026-03-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Build three ticker candidate sources (manual watchlist, sector screener, LLM proposals) and wire them into the daily trading cycle so the consensus engine has new buy candidates — not just existing positions. All candidates must pass OTC/exchange validation before consensus.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation decisions are Claude's discretion. Key areas:

**Manual Watchlist:**
- SQLite `watchlist` table (ticker, added_at, notes, active)
- CLI commands: `python -m src watchlist add ABEO`, `python -m src watchlist remove ABEO`, `python -m src watchlist list`
- Also exposed as functions for the Streamlit UI in Phase 7

**Sector Screener:**
- Use yfinance or a free screener API to find micro-caps by sector
- Filters: market cap < $300M, average volume > 10K, listed exchange only
- Sector categories: biotech, tech, other configurable sectors
- Cache results in SQLite to avoid re-screening every cycle
- Refresh frequency: daily or configurable

**LLM-Proposed Tickers:**
- Add a "discovery" prompt to the consensus engine asking LLMs to suggest new tickers
- Include current portfolio and watchlist in context so LLMs don't duplicate
- Validate all proposed tickers against OTC filter before adding to candidates
- This is separate from the bull/bear analysis — it's a screening step

**Cycle Integration:**
- New stage in `run_cycle()` between position sync and consensus
- Gather candidates from: watchlist + screener + LLM proposals
- Deduplicate against existing positions
- Run consensus on each candidate (in addition to existing position analysis)

</decisions>

<canonical_refs>
## Canonical References

### Existing Code
- `src/cycle.py` — Daily cycle to extend with candidate gathering stage
- `src/consensus.py` — Consensus engine to run on candidates
- `src/otc_filter.py` — Ticker validation
- `src/db.py` — SQLite schema to extend with watchlist table
- `src/cli.py` — CLI to extend with watchlist commands
- `src/config.py` — Settings for screener parameters

### Data Sources
- yfinance — Already installed, can screen by sector/market cap
- LLM APIs — Already wired in consensus.py

</canonical_refs>

<code_context>
## Existing Code Insights

### Integration Points
- New: `src/watchlist.py` — CRUD for watchlist table
- New: `src/screener.py` — Sector-based micro-cap screening
- Extend: `src/cycle.py` — Add candidate gathering stage
- Extend: `src/cli.py` — Add watchlist subcommands
- Extend: `src/db.py` — Add watchlist table to schema
- Extend: `src/consensus.py` — Add discovery prompt for LLM proposals

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

*Phase: 06-watchlist-and-screening*
*Context gathered: 2026-03-19*
