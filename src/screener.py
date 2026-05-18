"""Sector-based micro-cap screening via yfinance.

Screens for small-cap stocks by sector, filters by market cap and volume,
validates against OTC filter, and caches results in SQLite to avoid
repeated API calls.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from loguru import logger

from src.config import get_settings
from src.db import get_db
from src.otc_filter import validate_symbols


# Sector-to-yfinance screen() query mapping (yfinance >= 1.0)
_SECTOR_QUERIES: dict[str, list[str]] = {
    "biotech": ["aggressive_small_caps", "small_cap_gainers"],
    "tech": ["growth_technology_stocks", "aggressive_small_caps"],
}


def _fetch_sector_quotes(sector: str) -> list[dict]:
    """Fetch raw quote dicts for a sector using yf.screen().

    Returns a list of quote dicts — each already has marketCap,
    averageDailyVolume10Day, exchange, and symbol inline.
    """
    import yfinance as yf

    queries = _SECTOR_QUERIES.get(sector, ["small_cap_gainers"])
    for query_key in queries:
        try:
            response = yf.screen(query_key)
            quotes = response.get("quotes", [])
            if quotes:
                logger.info("Screener: {} returned {} quotes for sector={}", query_key, len(quotes), sector)
                return quotes
        except Exception as e:
            logger.warning("yf.screen({}) failed for sector={}: {}", query_key, sector, e)

    logger.warning("All screener queries failed for sector={}", sector)
    return []


def screen_sector(sector: str) -> list[str]:
    """Screen a sector for micro-cap stocks meeting our criteria.

    Checks cache first. If cache is fresh (within TTL), returns cached results.
    Otherwise fetches from yfinance, filters, validates via OTC filter, and caches.

    Returns:
        List of validated ticker symbol strings.
    """
    settings = get_settings()
    conn = get_db()

    try:
        # Check cache
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=settings.screener_cache_hours)).isoformat()
        cached = conn.execute(
            "SELECT symbol FROM screener_cache WHERE sector = ? AND cached_at > ? ORDER BY symbol",
            (sector, cutoff),
        ).fetchall()

        if cached:
            return [row["symbol"] for row in cached]

        # Cache miss -- fetch from yfinance
        try:
            quotes = _fetch_sector_quotes(sector)
        except Exception as e:
            logger.warning("Screener fetch failed for sector={}: {}", sector, e)
            return []

        if not quotes:
            return []

        # Filter by market cap and volume (data is inline in each quote dict)
        candidates = []
        for q in quotes:
            sym = q.get("symbol", "")
            if not sym:
                continue
            market_cap = q.get("marketCap", 0) or 0
            avg_volume = q.get("averageDailyVolume10Day", 0) or 0
            exchange = q.get("exchange")

            if market_cap > settings.screener_max_market_cap:
                continue
            if avg_volume < settings.screener_min_volume:
                continue

            candidates.append((sym, exchange, market_cap, avg_volume))

        if not candidates:
            return []

        # Validate through OTC filter
        symbols_with_exchanges = [(sym, ex) for sym, ex, _, _ in candidates]
        accepted, rejected = validate_symbols(symbols_with_exchanges)

        if rejected:
            logger.info("Screener: {} tickers rejected by OTC filter for sector={}", len(rejected), sector)

        # Cap results
        accepted = accepted[: settings.screener_max_results_per_sector]

        # Build lookup for caching
        candidate_info = {sym: (mc, vol, ex) for sym, ex, mc, vol in candidates}

        # Update cache: delete old sector rows, insert new
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("DELETE FROM screener_cache WHERE sector = ?", (sector,))
        for sym in accepted:
            mc, vol, ex = candidate_info.get(sym, (None, None, None))
            conn.execute(
                "INSERT INTO screener_cache (sector, symbol, market_cap, avg_volume, exchange, cached_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sector, sym, mc, vol, ex, now),
            )
        conn.commit()

        return accepted

    finally:
        conn.close()


def get_screener_candidates() -> list[str]:
    """Aggregate screener results from all configured sectors.

    Returns:
        Deduplicated list of validated ticker symbols from all sectors.
    """
    settings = get_settings()
    all_symbols: list[str] = []
    seen: set[str] = set()

    for sector in settings.screener_sectors:
        sector_results = screen_sector(sector)
        for sym in sector_results:
            if sym not in seen:
                all_symbols.append(sym)
                seen.add(sym)

    return all_symbols
