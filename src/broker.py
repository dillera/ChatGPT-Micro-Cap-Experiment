"""Tastytrade brokerage client — synchronous facade over async SDK.

Provides OAuth2 authentication, session caching, account snapshot,
and position sync to SQLite. All async SDK calls are isolated here
behind synchronous methods (Anti-Pattern 4 from ARCHITECTURE.md).

Uses a persistent event loop so the tastytrade SDK's httpx AsyncClient
survives across multiple sync calls.
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


def _get_loop() -> asyncio.AbstractEventLoop:
    """Get or create a persistent event loop for tastytrade SDK calls."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


class TastytradeClient:
    """Synchronous facade over the async tastytrade SDK."""

    def __init__(self) -> None:
        self._session = None
        self._account = None
        self._loop = _get_loop()

    def _run(self, coro):
        """Run an async coroutine on the persistent event loop."""
        return self._loop.run_until_complete(coro)

    def authenticate(self) -> None:
        """Authenticate via OAuth2. Try cached session first, fall back to fresh auth."""
        self._run(self._async_authenticate())

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
            if not settings.tt_secret or not settings.tt_refresh:
                raise ValueError(
                    "TT_SECRET and TT_REFRESH must be set in .env"
                )
            self._session = Session(
                provider_secret=settings.tt_secret,
                refresh_token=settings.tt_refresh,
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
        return self._run(self._async_get_snapshot())

    async def _async_get_snapshot(self) -> AccountSnapshot:
        from tastytrade import Account

        # Refresh session token if needed
        await self._session.refresh()

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

    def get_quote(self, ticker: str) -> tuple[float, float, float]:
        """Get live bid, ask, spread_pct via DXLinkStreamer.

        Returns (bid, ask, spread_pct) tuple.
        """
        return self._run(self._async_get_quote(ticker))

    async def _async_get_quote(self, ticker: str) -> tuple[float, float, float]:
        from tastytrade import DXLinkStreamer
        from tastytrade.dxfeed import Quote

        async with DXLinkStreamer(self._session) as streamer:
            await streamer.subscribe(Quote, [ticker])
            async for quote in streamer.listen(Quote):
                if quote.event_symbol == ticker:
                    bid = float(quote.bid_price)
                    ask = float(quote.ask_price)
                    mid = (bid + ask) / 2
                    spread_pct = (ask - bid) / mid if mid > 0 else float('inf')
                    return bid, ask, spread_pct

    def place_otoco_order(
        self,
        ticker: str,
        shares: int,
        limit_price: Decimal,
        stop_price: Decimal,
        dry_run: bool = True,
    ) -> dict:
        """Place OTOCO: limit buy (DAY) + GTC stop.

        Returns order response dict.
        """
        return self._run(
            self._async_place_otoco(ticker, shares, limit_price, stop_price, dry_run)
        )

    async def _async_place_otoco(
        self,
        ticker: str,
        shares: int,
        limit_price: Decimal,
        stop_price: Decimal,
        dry_run: bool,
    ) -> dict:
        from tastytrade.instruments import Equity
        from tastytrade.order import (
            NewOrder,
            NewComplexOrder,
            OrderAction,
            OrderType,
            OrderTimeInForce,
        )

        symbol = await Equity.get(self._session, ticker)
        opening = symbol.build_leg(Decimal(str(shares)), OrderAction.BUY_TO_OPEN)
        closing = symbol.build_leg(Decimal(str(shares)), OrderAction.SELL_TO_CLOSE)

        otoco = NewComplexOrder(
            trigger_order=NewOrder(
                time_in_force=OrderTimeInForce.DAY,
                order_type=OrderType.LIMIT,
                legs=[opening],
                price=-limit_price,  # negative = debit (buy)
            ),
            orders=[
                NewOrder(
                    time_in_force=OrderTimeInForce.GTC,
                    order_type=OrderType.STOP,
                    legs=[closing],
                    stop_trigger=stop_price,
                ),
            ],
        )
        response = await self._account.place_complex_order(
            self._session, otoco, dry_run=dry_run
        )
        return {
            "order_response": response,
            "ticker": ticker,
            "shares": shares,
            "limit_price": float(limit_price),
            "stop_price": float(stop_price),
            "dry_run": dry_run,
        }

    def place_sell_order(
        self,
        ticker: str,
        shares: int,
        limit_price: Decimal,
        dry_run: bool = True,
    ) -> dict:
        """Place a simple limit sell order (DAY).

        Returns order response dict.
        """
        return self._run(
            self._async_place_sell(ticker, shares, limit_price, dry_run)
        )

    async def _async_place_sell(
        self,
        ticker: str,
        shares: int,
        limit_price: Decimal,
        dry_run: bool,
    ) -> dict:
        from tastytrade.instruments import Equity
        from tastytrade.order import (
            NewOrder,
            OrderAction,
            OrderType,
            OrderTimeInForce,
        )

        symbol = await Equity.get(self._session, ticker)
        leg = symbol.build_leg(Decimal(str(shares)), OrderAction.SELL_TO_CLOSE)

        order = NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=[leg],
            price=limit_price,  # positive = credit (sell)
        )
        response = await self._account.place_order(
            self._session, order, dry_run=dry_run
        )
        return {
            "order_response": response,
            "ticker": ticker,
            "shares": shares,
            "limit_price": float(limit_price),
            "dry_run": dry_run,
        }

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
