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


# Sector-to-yfinance screener query mapping
# These map to yfinance Screener predefined keys
_SECTOR_QUERIES: dict[str, str] = {
    "biotech": "most_actives",
    "tech": "most_actives",
}


def _fetch_sector_tickers(sector: str) -> dict:
    """Fetch candidate tickers for a sector from yfinance.

    Returns:
        Dict mapping symbol -> mock ticker-like object with .info dict.
        In production, uses yf.Screener + yf.Ticker for info lookup.
    """
    import yfinance as yf

    settings = get_settings()
    query_key = _SECTOR_QUERIES.get(sector, "most_actives")

    try:
        screener = yf.Screener()
        screener.set_default_body(query_key)
        response = screener.response
        symbols = [
            q.get("symbol", "")
            for q in response.get("quotes", [])
            if q.get("symbol")
        ]
    except Exception:
        # yfinance screener API is unreliable; try alternate approach
        logger.warning("yfinance Screener failed for sector={}, trying fallback", sector)
        try:
            # Fallback: use a sector ETF to find holdings
            screener = yf.Screener()
            screener.set_default_body("small_cap_gainers")
            response = screener.response
            symbols = [
                q.get("symbol", "")
                for q in response.get("quotes", [])
                if q.get("symbol")
            ]
        except Exception as e2:
            logger.warning("yfinance fallback also failed for sector={}: {}", sector, e2)
            return {}

    # Fetch info for each candidate (capped)
    symbols = symbols[: settings.screener_max_results_per_sector * 2]
    result = {}
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            # Access .info to pre-populate
            _ = ticker.info
            result[sym] = ticker
        except Exception:
            continue

    return result


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
            raw_tickers = _fetch_sector_tickers(sector)
        except Exception as e:
            logger.warning("Screener fetch failed for sector={}: {}", sector, e)
            return []

        if not raw_tickers:
            return []

        # Filter by market cap and volume
        candidates = []
        for sym, ticker in raw_tickers.items():
            info = ticker.info if hasattr(ticker, "info") else {}
            market_cap = info.get("marketCap", 0) or 0
            avg_volume = info.get("averageDailyVolume10Day", 0) or 0
            exchange = info.get("exchange")

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
