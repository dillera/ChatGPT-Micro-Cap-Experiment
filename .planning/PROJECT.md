# Micro-Cap AI Trading Bot

## What This Is

A fully autonomous micro-cap trading system that connects to a tastytrade brokerage account, uses multiple LLMs (GPT-4 and Claude) for consensus-driven trading decisions, and executes trades daily without human intervention. Built on top of an existing ChatGPT micro-cap experiment that has demonstrated ~32% returns over 9 weeks.

## Core Value

The system must autonomously generate profitable trades on a small tastytrade account with aggressive but controlled risk management — every trade placed, every stop-loss enforced, every decision logged.

## Requirements

### Validated

<!-- Inferred from existing codebase -->

- ✓ Market data fetching with multi-source fallback (Yahoo → Stooq) — existing
- ✓ Portfolio state tracking via CSV with daily P&L calculations — existing
- ✓ Stop-loss enforcement with automatic sell triggers — existing
- ✓ GPT-4 integration for trading recommendations with JSON parsing — existing
- ✓ Performance visualization and S&P 500 benchmarking — existing
- ✓ Trade logging and LLM response audit trail — existing
- ✓ Weekend/holiday date handling for trading calendars — existing

<!-- Validated in Phase 1: Foundation -->

- ✓ SQLite state store replacing CSV as single source of truth — Phase 1
- ✓ tastytrade OAuth2 authentication with session caching — Phase 1
- ✓ Live positions sync from tastytrade account — Phase 1
- ✓ Live account balance and buying power retrieval — Phase 1
- ✓ OTC/penny stock ticker validation — Phase 1
- ✓ PDT day-trade counter (max 3 per 5 days) — Phase 1
- ✓ --dry-run mode for safe testing — Phase 1

### Active

- [ ] Programmatic order execution (market and limit orders) via tastytrade
- [ ] Programmatic order execution (market and limit orders) via tastytrade
- [ ] Claude API integration as second LLM for consensus decisions
- [ ] Multi-LLM consensus engine (GPT-4 + Claude agreement required)
- [ ] Daily autonomous trading cycle (scheduled, no human trigger)
- [ ] Aggressive position sizing (up to 50% single position, small account)
- [ ] Enhanced screening/filtering for micro-cap opportunities
- [ ] Real-time P&L tracking from live brokerage data
- [ ] Notification system (trades executed, stop-losses hit, daily summary)
- [ ] Circuit breakers (daily loss limit, max drawdown halt)

### Out of Scope

- Options trading — complexity too high for v1, equities only
- Intraday/real-time monitoring — daily cycle is sufficient for micro-caps
- Web UI or dashboard — CLI/notification-based for v1
- Multiple account support — single tastytrade account
- Backtesting framework — forward-testing with live money is the experiment

## Context

This project evolves an existing 11-week experiment where ChatGPT-4 has been making micro-cap biotech trading decisions with manual execution. The experiment proved the AI can generate alpha (~32% vs S&P 500's ~4.5%). Now the goal is to remove the human from the loop entirely by connecting directly to tastytrade for order execution.

The account is small (under $1K), so position sizing must be aggressive to be meaningful — concentrated bets with wider stops. The tastytrade API provides RESTful endpoints for account management, order placement, and position tracking.

Current portfolio focuses on biotech catalysts (FDA decisions, Phase 3 trials) with high-conviction concentrated positions.

## Constraints

- **Brokerage**: tastytrade API only — no other brokers
- **Account Size**: Under $1K — commissions and minimum order sizes matter
- **AI Models**: Must use both GPT-4 and Claude for consensus (no single-model decisions)
- **Risk**: Aggressive but with circuit breakers — daily loss limit must halt trading
- **Execution**: Must be schedulable via cron for daily autonomous runs
- **Data**: tastytrade API keys provided via environment variables (never hardcoded)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Full rebuild over incremental upgrade | Current CSV-based workflow doesn't support live trading; need clean architecture | ✓ Good |
| Multi-LLM consensus (GPT-4 + Claude) | Reduces single-model bias; higher confidence when both agree | — Pending |
| Aggressive position sizing (up to 50%) | Small account needs concentrated bets to generate meaningful returns | — Pending |
| Daily cycle (not intraday) | Micro-cap catalysts play out over days/weeks, not minutes | — Pending |
| tastytrade over other brokers | User's existing account; strong API; low commissions | — Pending |

---
*Last updated: 2026-03-19 after Phase 1 completion*
