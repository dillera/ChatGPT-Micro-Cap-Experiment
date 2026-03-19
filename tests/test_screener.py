"""Unit tests for sector-based micro-cap screener."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.db import SCHEMA_SQL


@pytest.fixture()
def screener_db(monkeypatch):
    """In-memory SQLite DB with full schema, patched into src.screener and src.db."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO circuit_breaker (id, status) VALUES (1, 'ACTIVE')"
    )
    conn.commit()

    class _Wrapper:
        def __init__(self, c):
            self._conn = c

        def close(self):
            pass

        def __getattr__(self, name):
            return getattr(self._conn, name)

    wrapper = _Wrapper(conn)

    def _get_db(*args, **kwargs):
        return wrapper

    monkeypatch.setattr("src.db.get_db", _get_db)
    monkeypatch.setattr("src.screener.get_db", _get_db)

    yield wrapper
    conn.close()


def _make_mock_ticker(symbol: str, market_cap: float, avg_volume: float, exchange: str):
    """Create a mock yfinance Ticker with .info dict."""
    t = MagicMock()
    t.info = {
        "symbol": symbol,
        "marketCap": market_cap,
        "averageDailyVolume10Day": avg_volume,
        "exchange": exchange,
    }
    return t


class TestScreenSector:
    def test_filters_out_high_market_cap(self, screener_db):
        """Tickers with market_cap > $300M are excluded."""
        from src.screener import screen_sector

        mock_tickers = {
            "SMALL": _make_mock_ticker("SMALL", 100_000_000, 50_000, "NMS"),
            "BIG": _make_mock_ticker("BIG", 500_000_000, 50_000, "NMS"),
        }

        with patch("src.screener._fetch_sector_tickers") as mock_fetch:
            mock_fetch.return_value = mock_tickers
            result = screen_sector("biotech")

        assert "SMALL" in result
        assert "BIG" not in result

    def test_filters_out_low_volume(self, screener_db):
        """Tickers with average volume < 10K are excluded."""
        from src.screener import screen_sector

        mock_tickers = {
            "ACTIVE": _make_mock_ticker("ACTIVE", 100_000_000, 50_000, "NMS"),
            "THIN": _make_mock_ticker("THIN", 100_000_000, 5_000, "NMS"),
        }

        with patch("src.screener._fetch_sector_tickers") as mock_fetch:
            mock_fetch.return_value = mock_tickers
            result = screen_sector("biotech")

        assert "ACTIVE" in result
        assert "THIN" not in result

    def test_filters_out_otc_tickers(self, screener_db):
        """Tickers on OTC exchanges are excluded via otc_filter."""
        from src.screener import screen_sector

        mock_tickers = {
            "GOOD": _make_mock_ticker("GOOD", 100_000_000, 50_000, "NMS"),
            "OTC_STOCK": _make_mock_ticker("OTC_STOCK", 100_000_000, 50_000, "PNK"),
        }

        with patch("src.screener._fetch_sector_tickers") as mock_fetch:
            mock_fetch.return_value = mock_tickers
            result = screen_sector("biotech")

        assert "GOOD" in result
        assert "OTC_STOCK" not in result

    def test_returns_cached_results(self, screener_db):
        """If cache is fresh, returns cached symbols without calling yfinance."""
        from src.screener import screen_sector

        # Manually insert a cache entry
        now = datetime.now(timezone.utc).isoformat()
        screener_db.execute(
            "INSERT INTO screener_cache (sector, symbol, market_cap, avg_volume, exchange, cached_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("biotech", "CACHED", 100_000_000, 50_000, "NMS", now),
        )
        screener_db.commit()

        with patch("src.screener._fetch_sector_tickers") as mock_fetch:
            result = screen_sector("biotech")
            # Should NOT call _fetch because cache is fresh
            mock_fetch.assert_not_called()

        assert result == ["CACHED"]

    def test_stale_cache_triggers_refresh(self, screener_db):
        """Stale cache (beyond TTL) triggers a fresh fetch."""
        from src.screener import screen_sector

        # Insert stale cache entry (48 hours ago)
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        screener_db.execute(
            "INSERT INTO screener_cache (sector, symbol, market_cap, avg_volume, exchange, cached_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("biotech", "STALE", 100_000_000, 50_000, "NMS", stale_time),
        )
        screener_db.commit()

        mock_tickers = {
            "FRESH": _make_mock_ticker("FRESH", 100_000_000, 50_000, "NMS"),
        }

        with patch("src.screener._fetch_sector_tickers") as mock_fetch:
            mock_fetch.return_value = mock_tickers
            result = screen_sector("biotech")
            mock_fetch.assert_called_once()

        assert "FRESH" in result
        assert "STALE" not in result

    def test_empty_on_fetch_failure(self, screener_db):
        """If yfinance fetch fails, return empty list (screener is additive)."""
        from src.screener import screen_sector

        with patch("src.screener._fetch_sector_tickers") as mock_fetch:
            mock_fetch.side_effect = Exception("yfinance API error")
            result = screen_sector("biotech")

        assert result == []


class TestGetScreenerCandidates:
    def test_aggregates_sectors(self, screener_db, monkeypatch):
        """get_screener_candidates combines results from all configured sectors."""
        from src.screener import get_screener_candidates
        from src.config import Settings

        s = Settings(
            db_path=":memory:",
            screener_sectors=["biotech", "tech"],
        )
        monkeypatch.setattr("src.config._settings", s)
        monkeypatch.setattr("src.screener.get_settings", lambda: s)

        # Insert cache for both sectors
        now = datetime.now(timezone.utc).isoformat()
        for sector, sym in [("biotech", "BIO1"), ("tech", "TECH1")]:
            screener_db.execute(
                "INSERT INTO screener_cache (sector, symbol, market_cap, avg_volume, exchange, cached_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sector, sym, 100_000_000, 50_000, "NMS", now),
            )
        screener_db.commit()

        result = get_screener_candidates()
        assert "BIO1" in result
        assert "TECH1" in result

    def test_deduplicates_across_sectors(self, screener_db, monkeypatch):
        """Duplicate tickers across sectors appear only once."""
        from src.screener import get_screener_candidates
        from src.config import Settings

        s = Settings(
            db_path=":memory:",
            screener_sectors=["biotech", "tech"],
        )
        monkeypatch.setattr("src.config._settings", s)
        monkeypatch.setattr("src.screener.get_settings", lambda: s)

        now = datetime.now(timezone.utc).isoformat()
        for sector in ["biotech", "tech"]:
            screener_db.execute(
                "INSERT INTO screener_cache (sector, symbol, market_cap, avg_volume, exchange, cached_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sector, "DUPE", 100_000_000, 50_000, "NMS", now),
            )
        screener_db.commit()

        result = get_screener_candidates()
        assert result.count("DUPE") == 1
