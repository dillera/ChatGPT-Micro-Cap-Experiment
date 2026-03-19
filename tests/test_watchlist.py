"""Unit tests for watchlist CRUD operations."""
from __future__ import annotations

import sqlite3

import pytest

from src.db import SCHEMA_SQL


@pytest.fixture()
def wl_db(monkeypatch):
    """In-memory SQLite DB with full schema, patched into src.watchlist.get_db."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO circuit_breaker (id, status) VALUES (1, 'ACTIVE')"
    )
    conn.commit()

    # Non-closing wrapper so production close() doesn't kill our test conn
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
    monkeypatch.setattr("src.watchlist.get_db", _get_db)

    yield wrapper
    conn.close()


class TestAddTicker:
    def test_add_new_ticker_returns_true(self, wl_db):
        from src.watchlist import add_ticker

        assert add_ticker("ABEO") is True

    def test_add_duplicate_active_returns_false(self, wl_db):
        from src.watchlist import add_ticker

        add_ticker("ABEO")
        assert add_ticker("ABEO") is False

    def test_reactivate_removed_ticker_returns_true(self, wl_db):
        from src.watchlist import add_ticker, remove_ticker

        add_ticker("ABEO")
        remove_ticker("ABEO")
        assert add_ticker("ABEO") is True


class TestRemoveTicker:
    def test_remove_active_ticker_returns_true(self, wl_db):
        from src.watchlist import add_ticker, remove_ticker

        add_ticker("ABEO")
        assert remove_ticker("ABEO") is True

    def test_remove_missing_ticker_returns_false(self, wl_db):
        from src.watchlist import remove_ticker

        assert remove_ticker("MISSING") is False

    def test_remove_already_inactive_returns_false(self, wl_db):
        from src.watchlist import add_ticker, remove_ticker

        add_ticker("ABEO")
        remove_ticker("ABEO")
        assert remove_ticker("ABEO") is False


class TestListTickers:
    def test_list_empty(self, wl_db):
        from src.watchlist import list_tickers

        assert list_tickers() == []

    def test_list_returns_active_only(self, wl_db):
        from src.watchlist import add_ticker, list_tickers, remove_ticker

        add_ticker("ABEO")
        add_ticker("RXRX")
        add_ticker("ATOS")
        remove_ticker("RXRX")
        result = list_tickers()
        assert result == ["ABEO", "ATOS"]  # sorted, RXRX excluded

    def test_list_sorted_alphabetically(self, wl_db):
        from src.watchlist import add_ticker, list_tickers

        add_ticker("ZZZZ")
        add_ticker("AAAA")
        add_ticker("MMMM")
        assert list_tickers() == ["AAAA", "MMMM", "ZZZZ"]


class TestGetActiveSymbols:
    def test_get_active_symbols_same_as_list(self, wl_db):
        from src.watchlist import add_ticker, get_active_symbols, list_tickers

        add_ticker("ABEO")
        add_ticker("RXRX")
        assert get_active_symbols() == list_tickers()
