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
            OrderAction,
            OrderType,
            OrderTimeInForce,
        )

        symbol = await Equity.get(self._session, ticker)
        opening = symbol.build_leg(Decimal(str(shares)), OrderAction.BUY_TO_OPEN)

        order = NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=[opening],
            price=-limit_price,  # negative = debit (buy)
        )
        response = await self._account.place_order(
            self._session, order, dry_run=dry_run
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

    def get_put_contract(self, symbol: str, target_dte: int = 30) -> dict | None:
        """Find the ATM put contract closest to target_dte for a symbol.

        Returns a dict with occ_symbol, streamer_symbol, strike, expiry, dte,
        and underlying_price — or None if no chain is found.
        """
        return self._run(self._async_get_put_contract(symbol, target_dte))

    async def _async_get_put_contract(self, symbol: str, target_dte: int) -> dict | None:
        import yfinance as yf
        from tastytrade.instruments import NestedOptionChain

        try:
            price = yf.Ticker(symbol).fast_info.last_price
        except Exception as e:
            logger.warning("Could not get underlying price for {}: {}", symbol, e)
            return None

        if not price or price <= 0:
            logger.warning("Invalid underlying price for {}: {}", symbol, price)
            return None

        # Price gate before fetching chain — avoids wasted API call
        min_price = get_settings().options_min_underlying_price
        if price < min_price:
            logger.info("Skipping options chain for {}: ${:.2f} below ${:.2f} minimum", symbol, price, min_price)
            return None

        try:
            chains = await NestedOptionChain.get(self._session, symbol)
        except Exception as e:
            logger.warning("No options chain for {}: {}", symbol, e)
            return None

        if not chains or not chains[0].expirations:
            logger.warning("Empty options chain for {}", symbol)
            return None

        chain = chains[0]

        # Pick expiration closest to target_dte
        best_exp = min(chain.expirations, key=lambda e: abs(e.days_to_expiration - target_dte))
        if not best_exp.strikes:
            logger.warning("No strikes for {} expiry {}", symbol, best_exp.expiration_date)
            return None

        # Pick put strike closest to ATM
        best_strike = min(best_exp.strikes, key=lambda s: abs(float(s.strike_price) - price))

        logger.info(
            "Selected put for {}: strike={} expiry={} DTE={}",
            symbol, best_strike.strike_price, best_exp.expiration_date, best_exp.days_to_expiration,
        )
        return {
            "occ_symbol": best_strike.put,
            "streamer_symbol": best_strike.put_streamer_symbol,
            "strike": float(best_strike.strike_price),
            "expiry": str(best_exp.expiration_date),
            "dte": best_exp.days_to_expiration,
            "underlying_price": price,
        }

    def place_put_order(
        self,
        occ_symbol: str,
        contracts: int,
        limit_price: Decimal,
        dry_run: bool = True,
    ) -> dict:
        """Place a DAY limit buy-to-open for a put contract."""
        return self._run(self._async_place_put_order(occ_symbol, contracts, limit_price, dry_run))

    async def _async_place_put_order(
        self,
        occ_symbol: str,
        contracts: int,
        limit_price: Decimal,
        dry_run: bool,
    ) -> dict:
        from tastytrade.instruments import Option
        from tastytrade.order import NewOrder, OrderAction, OrderType, OrderTimeInForce

        option = await Option.get(self._session, occ_symbol)

        if getattr(option, "is_closing_only", False):
            raise ValueError(f"{occ_symbol} is closing-only — cannot open new put position")

        leg = option.build_leg(Decimal(str(contracts)), OrderAction.BUY_TO_OPEN)

        order = NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=[leg],
            price=-limit_price,  # negative = debit (buying premium)
        )
        response = await self._account.place_order(self._session, order, dry_run=dry_run)
        return {
            "order_response": response,
            "occ_symbol": occ_symbol,
            "contracts": contracts,
            "limit_price": float(limit_price),
            "dry_run": dry_run,
        }

    def get_option_chain(self, symbol: str, dte_target: int = 0) -> dict | None:
        """Fetch full option chain for a symbol at target DTE.

        Returns {underlying_price, expiry, dte,
                 calls: [{strike, occ_symbol, streamer_symbol}],
                 puts:  [{strike, occ_symbol, streamer_symbol}]}
        Falls back to dte_target=1 if 0DTE unavailable for this symbol/day.
        Returns None if chain cannot be fetched.
        """
        return self._run(self._async_get_option_chain(symbol, dte_target))

    async def _async_get_option_chain(self, symbol: str, dte_target: int) -> dict | None:
        import yfinance as yf
        from tastytrade.instruments import NestedOptionChain

        try:
            price = yf.Ticker(symbol).fast_info.last_price
        except Exception as e:
            logger.warning("Could not get underlying price for {}: {}", symbol, e)
            return None

        if not price or price <= 0:
            logger.warning("Invalid underlying price for {}: {}", symbol, price)
            return None

        try:
            chains = await NestedOptionChain.get(self._session, symbol)
        except Exception as e:
            logger.warning("No options chain for {}: {}", symbol, e)
            return None

        if not chains or not chains[0].expirations:
            return None

        chain = chains[0]

        # Try exact dte_target first; fall back to 1DTE if 0DTE unavailable
        candidates = sorted(chain.expirations, key=lambda e: abs(e.days_to_expiration - dte_target))
        best_exp = candidates[0]
        if dte_target == 0 and best_exp.days_to_expiration > 1:
            logger.info("0DTE not available for {}, falling back to 1DTE", symbol)
            candidates = sorted(chain.expirations, key=lambda e: abs(e.days_to_expiration - 1))
            best_exp = candidates[0]

        if not best_exp.strikes:
            return None

        calls = []
        puts = []
        for s in best_exp.strikes:
            strike = float(s.strike_price)
            if s.call and s.call_streamer_symbol:
                calls.append({"strike": strike, "occ_symbol": s.call, "streamer_symbol": s.call_streamer_symbol})
            if s.put and s.put_streamer_symbol:
                puts.append({"strike": strike, "occ_symbol": s.put, "streamer_symbol": s.put_streamer_symbol})

        logger.info(
            "Option chain for {}: expiry={} DTE={} ({} calls, {} puts)",
            symbol, best_exp.expiration_date, best_exp.days_to_expiration, len(calls), len(puts),
        )
        return {
            "underlying_price": price,
            "expiry": str(best_exp.expiration_date),
            "dte": best_exp.days_to_expiration,
            "calls": sorted(calls, key=lambda x: x["strike"]),
            "puts": sorted(puts, key=lambda x: x["strike"]),
        }

    def get_spread_quotes(
        self,
        long_streamer: str,
        short_streamer: str,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        """Fetch bid/ask for two option legs concurrently.

        Returns ((long_bid, long_ask), (short_bid, short_ask)).
        """
        return self._run(self._async_get_spread_quotes(long_streamer, short_streamer))

    async def _async_get_spread_quotes(
        self,
        long_streamer: str,
        short_streamer: str,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        from tastytrade import DXLinkStreamer
        from tastytrade.dxfeed import Quote

        quotes: dict[str, tuple[float, float]] = {}
        targets = {long_streamer, short_streamer}

        async with DXLinkStreamer(self._session) as streamer:
            await streamer.subscribe(Quote, list(targets))
            async for quote in streamer.listen(Quote):
                if quote.event_symbol in targets:
                    bid = float(quote.bid_price or 0)
                    ask = float(quote.ask_price or 0)
                    quotes[quote.event_symbol] = (bid, ask)
                if len(quotes) == len(targets):
                    break

        long_q = quotes.get(long_streamer, (0.0, 0.0))
        short_q = quotes.get(short_streamer, (0.0, 0.0))
        return long_q, short_q

    def place_vertical_spread(
        self,
        long_occ: str,
        short_occ: str,
        contracts: int,
        net_debit: Decimal,
        dry_run: bool = True,
    ) -> dict:
        """Place a two-leg debit vertical spread as a single order.

        Price = -net_debit (TastyTrade convention: negative = debit paid).
        Uses BUY_TO_OPEN (long) + SELL_TO_OPEN (short) in one NewOrder.
        Returns order response dict.
        """
        return self._run(
            self._async_place_vertical_spread(long_occ, short_occ, contracts, net_debit, dry_run)
        )

    async def _async_place_vertical_spread(
        self,
        long_occ: str,
        short_occ: str,
        contracts: int,
        net_debit: Decimal,
        dry_run: bool,
    ) -> dict:
        from tastytrade.instruments import Option
        from tastytrade.order import NewOrder, OrderAction, OrderType, OrderTimeInForce

        long_opt = await Option.get(self._session, long_occ)
        short_opt = await Option.get(self._session, short_occ)

        if getattr(long_opt, "is_closing_only", False):
            raise ValueError(f"{long_occ} is closing-only — cannot open long leg")

        qty = Decimal(str(contracts))
        long_leg = long_opt.build_leg(qty, OrderAction.BUY_TO_OPEN)
        short_leg = short_opt.build_leg(qty, OrderAction.SELL_TO_OPEN)

        order = NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=[long_leg, short_leg],
            price=-net_debit,  # negative = debit
        )
        response = await self._account.place_order(self._session, order, dry_run=dry_run)
        return {
            "order_response": response,
            "long_occ": long_occ,
            "short_occ": short_occ,
            "contracts": contracts,
            "net_debit": float(net_debit),
            "dry_run": dry_run,
        }

    def place_spread_close(
        self,
        long_occ: str,
        short_occ: str,
        contracts: int,
        net_credit: Decimal,
        dry_run: bool = True,
    ) -> dict:
        """Close a vertical spread: SELL_TO_CLOSE long + BUY_TO_CLOSE short.

        Price = net_credit (positive = credit received).
        Returns order response dict.
        """
        return self._run(
            self._async_place_spread_close(long_occ, short_occ, contracts, net_credit, dry_run)
        )

    async def _async_place_spread_close(
        self,
        long_occ: str,
        short_occ: str,
        contracts: int,
        net_credit: Decimal,
        dry_run: bool,
    ) -> dict:
        from tastytrade.instruments import Option
        from tastytrade.order import NewOrder, OrderAction, OrderType, OrderTimeInForce

        long_opt = await Option.get(self._session, long_occ)
        short_opt = await Option.get(self._session, short_occ)

        qty = Decimal(str(contracts))
        long_leg = long_opt.build_leg(qty, OrderAction.SELL_TO_CLOSE)
        short_leg = short_opt.build_leg(qty, OrderAction.BUY_TO_CLOSE)

        order = NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=[long_leg, short_leg],
            price=net_credit,  # positive = credit received
        )
        response = await self._account.place_order(self._session, order, dry_run=dry_run)
        return {
            "order_response": response,
            "long_occ": long_occ,
            "short_occ": short_occ,
            "contracts": contracts,
            "net_credit": float(net_credit),
            "dry_run": dry_run,
        }

    def place_credit_spread(
        self,
        short_occ: str,
        long_occ: str,
        contracts: int,
        net_credit: Decimal,
        dry_run: bool = True,
    ) -> dict:
        """Place a two-leg credit vertical spread as a single order.

        short_occ = the OTM leg we sell (SELL_TO_OPEN).
        long_occ  = the further OTM protection leg (BUY_TO_OPEN).
        Price = +net_credit (positive = credit received).
        """
        return self._run(
            self._async_place_credit_spread(short_occ, long_occ, contracts, net_credit, dry_run)
        )

    async def _async_place_credit_spread(
        self,
        short_occ: str,
        long_occ: str,
        contracts: int,
        net_credit: Decimal,
        dry_run: bool,
    ) -> dict:
        from tastytrade.instruments import Option
        from tastytrade.order import NewOrder, OrderAction, OrderType, OrderTimeInForce

        short_opt = await Option.get(self._session, short_occ)
        long_opt = await Option.get(self._session, long_occ)

        if getattr(short_opt, "is_closing_only", False):
            raise ValueError(f"{short_occ} is closing-only — cannot open short leg")

        qty = Decimal(str(contracts))
        short_leg = short_opt.build_leg(qty, OrderAction.SELL_TO_OPEN)
        long_leg = long_opt.build_leg(qty, OrderAction.BUY_TO_OPEN)

        order = NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=[short_leg, long_leg],
            price=net_credit,  # positive = credit received
        )
        response = await self._account.place_order(self._session, order, dry_run=dry_run)
        return {
            "order_response": response,
            "short_occ": short_occ,
            "long_occ": long_occ,
            "contracts": contracts,
            "net_credit": float(net_credit),
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
