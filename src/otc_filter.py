"""OTC/penny stock ticker validation.

Rejects tickers traded on OTC, pink sheet, or grey market exchanges
before any brokerage API call. tastytrade explicitly prohibits OTC
and penny stock orders (see PITFALLS.md Pitfall 1).
"""
from __future__ import annotations

from loguru import logger

# Major US exchanges that tastytrade supports for equity orders
# All entries stored in UPPERCASE for case-insensitive comparison
VALID_EXCHANGES = frozenset({
    "NYSE", "NASDAQ", "NYSE ARCA", "NYSE AMERICAN",
    "BATS", "CBOE", "IEX",
    # Alias forms sometimes returned by data providers
    "NYQ", "NMS", "NCM", "NGM", "PCX", "BTS",
})

# Known OTC/pink sheet exchange identifiers to explicitly reject
# All entries stored in UPPERCASE for case-insensitive comparison
OTC_EXCHANGES = frozenset({
    "OTC", "OTCBB", "PINK", "OTC MARKETS", "OTHER OTC",
    "PNK", "GREY",
})


def is_exchange_listed(symbol: str, exchange: str | None) -> bool:
    """Check if a ticker is listed on a major exchange supported by tastytrade.

    Args:
        symbol: Ticker symbol
        exchange: Exchange identifier from data provider (yfinance, etc.)

    Returns:
        True if the ticker is on a valid exchange, False if OTC or unknown.
    """
    if exchange is None:
        logger.warning("No exchange data for {symbol}, rejecting as potential OTC", symbol=symbol)
        return False

    ex_upper = exchange.upper().strip()

    if ex_upper in OTC_EXCHANGES:
        logger.warning("Rejecting {symbol}: OTC exchange ({exchange})", symbol=symbol, exchange=exchange)
        return False

    if ex_upper in VALID_EXCHANGES:
        return True

    # Unknown exchange -- reject conservatively
    logger.warning(
        "Rejecting {symbol}: unknown exchange '{exchange}' (not in VALID_EXCHANGES)",
        symbol=symbol, exchange=exchange
    )
    return False


def validate_symbols(symbols_with_exchanges: list[tuple[str, str | None]]) -> tuple[list[str], list[str]]:
    """Validate a list of (symbol, exchange) pairs.

    Returns:
        Tuple of (accepted_symbols, rejected_symbols)
    """
    accepted = []
    rejected = []
    for symbol, exchange in symbols_with_exchanges:
        if is_exchange_listed(symbol, exchange):
            accepted.append(symbol)
        else:
            rejected.append(symbol)

    if rejected:
        logger.info(
            "OTC filter: accepted {a}, rejected {r}: {symbols}",
            a=len(accepted), r=len(rejected), symbols=rejected
        )
    return accepted, rejected
