"""Micro-Cap AI Trading Bot -- Streamlit Dashboard.

Launch with: streamlit run dashboard.py

Displays portfolio positions/P&L, watchlist management, and circuit
breaker status with manual reset capability.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.db import init_db
from src.watchlist import add_ticker, remove_ticker, list_tickers
from src.circuit_breaker import get_cb_status
from src.dashboard_helpers import (
    get_positions_with_pnl,
    get_portfolio_summary,
    reset_circuit_breaker,
)

# ---------------------------------------------------------------------------
# Page config and DB init
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Micro-Cap Trading Bot", layout="wide")
init_db()
st.title("Micro-Cap AI Trading Bot")

settings = get_settings()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.markdown(f"**Database:** `{settings.db_path}`")
if st.sidebar.button("Refresh Data"):
    st.rerun()

# ---------------------------------------------------------------------------
# Portfolio Summary (DASH-01)
# ---------------------------------------------------------------------------
st.header("Portfolio Summary")

summary = get_portfolio_summary()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Equity", f"${summary['total_equity']:,.2f}")
col2.metric("Cash Balance", f"${summary['cash_balance']:,.2f}")
col3.metric(
    "Daily P&L",
    f"${summary['daily_pnl']:,.2f}",
    delta=f"{summary['daily_pnl_pct']:.2%}",
)
col4.metric("Drawdown", f"{summary['drawdown_pct']:.2%}")

if summary["snapshot_date"] != "N/A":
    st.caption(f"Latest snapshot: {summary['snapshot_date']}")

st.subheader("Open Positions")

positions = get_positions_with_pnl()
if positions:
    df = pd.DataFrame(positions)
    st.dataframe(
        df[["symbol", "shares", "buy_price", "cost_basis", "current_value", "pnl"]],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No open positions")

# ---------------------------------------------------------------------------
# Watchlist Management (DASH-02)
# ---------------------------------------------------------------------------
st.header("Watchlist")

# Add ticker form
with st.form("add_ticker_form", clear_on_submit=True):
    form_col1, form_col2 = st.columns([1, 2])
    new_ticker = form_col1.text_input("Ticker Symbol", key="new_ticker")
    ticker_notes = form_col2.text_input("Notes (optional)", key="ticker_notes")
    submitted = st.form_submit_button("Add to Watchlist")
    if submitted and new_ticker.strip():
        add_ticker(new_ticker.strip().upper(), ticker_notes.strip() or None)
        st.rerun()

# Current watchlist with remove buttons
tickers = list_tickers()
if tickers:
    for symbol in tickers:
        wcol1, wcol2 = st.columns([3, 1])
        wcol1.write(symbol)
        if wcol2.button("Remove", key=f"remove_{symbol}"):
            remove_ticker(symbol)
            st.rerun()
else:
    st.info("Watchlist is empty. Add tickers above.")

# ---------------------------------------------------------------------------
# Circuit Breaker (DASH-04)
# ---------------------------------------------------------------------------
st.header("Circuit Breaker")

cb = get_cb_status()

if cb.status == "ACTIVE":
    st.success("ACTIVE")
elif cb.status == "HALTED_DAILY":
    st.warning(f"HALTED - Daily Loss Limit (tripped: {cb.tripped_at})")
elif cb.status == "HALTED_DRAWDOWN":
    st.error(f"HALTED - Max Drawdown (tripped: {cb.tripped_at})")
else:
    st.warning(f"Unknown status: {cb.status}")

if cb.status != "ACTIVE":
    if cb.reason:
        st.caption(f"Reason: {cb.reason}")
    if st.button("Reset Circuit Breaker"):
        reset_circuit_breaker()
        st.rerun()
