"""Multi-strategy stock research pipeline.

Scans 10+ yfinance screener presets across momentum, value, sector, and
activity strategies. Filters by market cap ($10M–$2B), price, and volume.
Validates against OTC filter. Bulk-adds new small/mid-cap candidates to
the watchlist, skipping symbols already present or held.
"""
from __future__ import annotations

from loguru import logger

from src.config import get_settings
from src.db import get_db
from src.otc_filter import validate_symbols
from src.watchlist import add_ticker, get_active_symbols


# yfinance screen() preset keys to query — cast a wide net across styles/sectors
_SCREEN_QUERIES = [
    # Momentum
    "day_gainers",
    "small_cap_gainers",
    "aggressive_small_caps",
    "most_actives",
    # Value / growth
    "undervalued_growth_stocks",
    "growth_technology_stocks",
    # Sectors
    "ms_healthcare",
    "ms_technology",
    "ms_consumer_cyclical",
    "ms_energy",
    "ms_basic_materials",
    "ms_industrials",
]


def _run_screens() -> list[dict]:
    """Run all screen queries and return deduplicated raw quote dicts."""
    import yfinance as yf

    seen: set[str] = set()
    results: list[dict] = []

    for query in _SCREEN_QUERIES:
        try:
            response = yf.screen(query)
            quotes = response.get("quotes", [])
            for q in quotes:
                sym = q.get("symbol", "").upper().strip()
                if sym and sym not in seen:
                    seen.add(sym)
                    results.append(q)
            logger.info("Research screen '{}': {} quotes", query, len(quotes))
        except Exception as e:
            logger.warning("Research screen '{}' failed: {}", query, e)

    logger.info("Research: {} unique raw candidates from {} screens", len(results), len(_SCREEN_QUERIES))
    return results


def _apply_filters(quotes: list[dict]) -> list[dict]:
    """Apply market cap, price, and volume filters."""
    settings = get_settings()
    passed = []
    for q in quotes:
        mcap = q.get("marketCap") or 0
        price = q.get("regularMarketPrice") or 0
        volume = q.get("averageDailyVolume10Day") or q.get("averageDailyVolume3Month") or 0

        if mcap < settings.research_min_market_cap:
            continue
        if mcap > settings.research_max_market_cap:
            continue
        if price < settings.research_min_price:
            continue
        if volume < settings.research_min_volume:
            continue
        passed.append(q)

    logger.info(
        "Research: {}/{} candidates passed market cap/price/volume filters",
        len(passed), len(quotes),
    )
    return passed


def run_research(held_symbols: list[str] | None = None) -> int:
    """Run the full research pipeline and add new candidates to the watchlist.

    Args:
        held_symbols: Symbols already in positions — excluded from output.

    Returns:
        Number of new symbols added to the watchlist.
    """
    settings = get_settings()
    held = {s.upper() for s in (held_symbols or [])}
    existing_watchlist = {s.upper() for s in get_active_symbols()}

    # Step 1: Scrape screens
    raw = _run_screens()

    # Step 2: Apply fundamental filters
    filtered = _apply_filters(raw)

    # Step 3: Remove already-held or watchlisted symbols
    novel = [q for q in filtered if q.get("symbol", "").upper() not in held | existing_watchlist]
    logger.info("Research: {} novel candidates after dedup vs held+watchlist", len(novel))

    if not novel:
        return 0

    # Step 4: OTC filter — validate exchange
    symbols_with_exchanges = [
        (q["symbol"].upper(), (q.get("exchange") or q.get("fullExchangeName") or "").upper() or None)
        for q in novel
    ]
    accepted_syms, rejected_syms = validate_symbols(symbols_with_exchanges)

    if rejected_syms:
        logger.info("Research: OTC filter rejected {} symbols", len(rejected_syms))

    # Step 5: Cap to research_max_per_run
    to_add = accepted_syms[: settings.research_max_per_run]

    # Build a quick lookup for market cap notes
    mcap_map = {q["symbol"].upper(): q.get("marketCap", 0) for q in novel}

    # Step 6: Add to watchlist
    added = 0
    for sym in to_add:
        mcap = mcap_map.get(sym, 0)
        mcap_str = f"${mcap / 1e6:.0f}M" if mcap >= 1_000_000 else "unknown"
        note = f"research pipeline — mcap {mcap_str}"
        if add_ticker(sym, notes=note):
            added += 1

    logger.info(
        "Research complete: {} new symbols added to watchlist ({} screened, {} filtered, {} passed OTC)",
        added, len(raw), len(filtered), len(accepted_syms),
    )
    return added
