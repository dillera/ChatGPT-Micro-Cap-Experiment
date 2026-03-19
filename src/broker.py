"""Tastytrade brokerage client — synchronous facade over async SDK.

Provides OAuth2 authentication, session caching, account snapshot,
and position sync to SQLite. All async SDK calls are isolated here
behind synchronous methods (Anti-Pattern 4 from ARCHITECTURE.md).
"""
from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from loguru import logger

from src.config import get_settings
from src.db import get_db


@dataclass
class AccountSnapshot:
    account_number: str
    cash_balance: float
    buying_power: float
    net_liquidating_value: float
    positions: list[dict]      # list of {symbol, shares, price, market_value}
    fetched_at: str            # ISO datetime


class TastytradeClient:
    """Synchronous facade over the async tastytrade SDK."""

    def __init__(self) -> None:
        self._session = None
        self._account = None

    def authenticate(self) -> None:
        """Authenticate via OAuth2. Try cached session first, fall back to fresh auth."""
        asyncio.run(self._async_authenticate())

    async def _async_authenticate(self) -> None:
        from tastytrade import Session, Account

        settings = get_settings()

        # Try to restore cached session from SQLite
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT serialized_session, expires_at FROM session_cache WHERE id=1"
            ).fetchone()
            if row and row["expires_at"] > datetime.now().isoformat():
                try:
                    self._session = Session.deserialize(row["serialized_session"])
                    logger.info("Restored cached tastytrade session")
                except Exception as e:
                    logger.warning("Cached session invalid, re-authenticating: {}", e)
                    self._session = None
        finally:
            conn.close()

        # Fresh authentication if no cached session
        if self._session is None:
            if not settings.tastytrade_client_secret or not settings.tastytrade_refresh_token:
                raise ValueError(
                    "TASTYTRADE_CLIENT_SECRET and TASTYTRADE_REFRESH_TOKEN must be set in .env"
                )
            self._session = Session(
                provider_secret=settings.tastytrade_client_secret,
                refresh_token=settings.tastytrade_refresh_token,
            )
            logger.info("Authenticated with tastytrade via OAuth2")

            # Cache the new session
            self._cache_session()

        # Get the account (async)
        accounts = await Account.get(self._session)
        if not accounts:
            raise RuntimeError("No accounts found on tastytrade")
        self._account = accounts[0]  # Use first account
        logger.info("Connected to account: {}", self._account.account_number)

    def _cache_session(self) -> None:
        """Cache serialized session token in SQLite."""
        if self._session is None:
            return
        conn = get_db()
        try:
            serialized = self._session.serialize()
            expires = (datetime.now() + timedelta(minutes=14)).isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO session_cache (id, serialized_session, expires_at) VALUES (1, ?, ?)",
                (serialized, expires),
            )
            conn.commit()
        finally:
            conn.close()

    def get_account_snapshot(self) -> AccountSnapshot:
        """Fetch live account balances and positions."""
        return asyncio.run(self._async_get_snapshot())

    async def _async_get_snapshot(self) -> AccountSnapshot:
        from tastytrade import Account

        # Brief pause between sequential API calls (~2 req/sec limit)
        balances = await self._account.get_balances(self._session)
        await asyncio.sleep(0.5)
        positions = await self._account.get_positions(self._session)

        pos_list = []
        for p in positions:
            shares = float(p.quantity)
            avg_price = float(p.average_open_price) if p.average_open_price else 0.0
            # market_value: use mark_price * quantity if available, else estimate from avg price
            mark = float(p.mark_price) if p.mark_price else avg_price
            market_value = mark * abs(shares)
            pos_list.append({
                "symbol": p.symbol,
                "shares": shares,
                "price": avg_price,
                "market_value": market_value,
            })

        return AccountSnapshot(
            account_number=self._account.account_number,
            cash_balance=float(balances.cash_balance) if balances.cash_balance else 0.0,
            buying_power=float(balances.equity_buying_power) if balances.equity_buying_power else 0.0,
            net_liquidating_value=float(balances.net_liquidating_value) if balances.net_liquidating_value else 0.0,
            positions=pos_list,
            fetched_at=datetime.now().isoformat(),
        )

    def sync_positions_to_db(self, snapshot: AccountSnapshot) -> int:
        """Write live positions to SQLite, replacing existing rows. Returns count synced."""
        conn = get_db()
        try:
            now = datetime.now().isoformat()
            conn.execute("DELETE FROM positions")  # Clear stale local state
            count = 0
            for p in snapshot.positions:
                conn.execute(
                    """INSERT INTO positions (symbol, shares, buy_price, cost_basis, stop_loss, opened_at, updated_at)
                       VALUES (?, ?, ?, ?, NULL, ?, ?)""",
                    (p["symbol"], p["shares"], p["price"],
                     p["price"] * p["shares"], now, now),
                )
                count += 1
            conn.commit()
            logger.info("Synced {} positions to SQLite from tastytrade", count)
            return count
        finally:
            conn.close()
