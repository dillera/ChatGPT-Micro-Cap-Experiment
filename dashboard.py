"""Micro-Cap AI Trading Bot -- Streamlit Dashboard.

Launch with: streamlit run dashboard.py

Displays portfolio positions/P&L, watchlist management, and circuit
breaker status with manual reset capability.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from src.config import get_settings, reset_settings
from src.db import get_db, init_db
from src.run_logger import RUN_LOG_DIR
from src.screener import screen_sector
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
# LLM Model Settings
# ---------------------------------------------------------------------------
st.sidebar.divider()
st.sidebar.subheader("LLM Models")

OPENAI_MODELS = ["gpt-4o", "gpt-4o-mini", "gpt-5.4-mini", "o1", "o3-mini"]
ANTHROPIC_MODELS = ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001"]

current_oai = settings.openai_model
current_ant = settings.anthropic_model

oai_idx = OPENAI_MODELS.index(current_oai) if current_oai in OPENAI_MODELS else 0
ant_idx = ANTHROPIC_MODELS.index(current_ant) if current_ant in ANTHROPIC_MODELS else 0

selected_oai = st.sidebar.selectbox("OpenAI (Bull)", OPENAI_MODELS, index=oai_idx)
selected_ant = st.sidebar.selectbox("Anthropic (Bear)", ANTHROPIC_MODELS, index=ant_idx)

if st.sidebar.button("Save Model Settings"):
    env_path = __import__("pathlib").Path(".env")
    if env_path.exists():
        lines = env_path.read_text().splitlines()
    else:
        lines = []
    updated = {"OPENAI_MODEL": False, "ANTHROPIC_MODEL": False}
    for i, line in enumerate(lines):
        if line.startswith("OPENAI_MODEL="):
            lines[i] = f"OPENAI_MODEL={selected_oai}"
            updated["OPENAI_MODEL"] = True
        elif line.startswith("ANTHROPIC_MODEL="):
            lines[i] = f"ANTHROPIC_MODEL={selected_ant}"
            updated["ANTHROPIC_MODEL"] = True
    if not updated["OPENAI_MODEL"]:
        lines.append(f"OPENAI_MODEL={selected_oai}")
    if not updated["ANTHROPIC_MODEL"]:
        lines.append(f"ANTHROPIC_MODEL={selected_ant}")
    env_path.write_text("\n".join(lines) + "\n")
    reset_settings()
    st.sidebar.success(f"Saved — Bull: {selected_oai} / Bear: {selected_ant}")
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

# ---------------------------------------------------------------------------
# Run Log History (DASH-03)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Run Log History")

if RUN_LOG_DIR.exists():
    log_files = sorted(RUN_LOG_DIR.glob("cycle_*.json"), reverse=True)[:50]
else:
    log_files = []

if not log_files:
    st.info("No run logs found. Run a trading cycle to generate logs.")
else:
    for log_file in log_files:
        try:
            log_data = json.loads(log_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        status = log_data.get("status", "unknown")
        dry_run = log_data.get("dry_run", False)
        label = f"{log_file.stem} - {status}"
        if dry_run:
            label += " (dry run)"
        with st.expander(label):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Status", status)
            c2.metric("Dry Run", str(dry_run))
            account = log_data.get("account") or {}
            c3.metric("NLV", f"${account.get('nlv', 0):,.2f}")
            snapshot = log_data.get("daily_snapshot") or {}
            c4.metric("Daily P&L", f"${snapshot.get('pnl', 0):,.2f}")
            st.json(log_data)

# ---------------------------------------------------------------------------
# Sector Screener (DASH-05)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Sector Screener")

watchlist_set = set(list_tickers())

for sector in settings.screener_sectors:
    st.markdown(f"**{sector.title()}**")
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT symbol, market_cap, avg_volume, exchange, cached_at "
            "FROM screener_cache WHERE sector = ? ORDER BY symbol",
            (sector,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        st.caption(
            f"No cached results for {sector}. Run screener via CLI: "
            f"python -m src.cli screener --sector {sector}"
        )
    else:
        table_data = []
        for row in rows:
            table_data.append(
                {
                    "Symbol": row["symbol"],
                    "Market Cap": f"${row['market_cap']:,.0f}" if row["market_cap"] else "N/A",
                    "Avg Volume": f"{row['avg_volume']:,.0f}" if row["avg_volume"] else "N/A",
                    "Exchange": row["exchange"] or "N/A",
                    "Cached At": row["cached_at"] or "N/A",
                }
            )
        st.dataframe(pd.DataFrame(table_data), use_container_width=True, hide_index=True)

        for row in rows:
            sym = row["symbol"]
            if sym in watchlist_set:
                st.caption(f"{sym} -- Already in watchlist")
            else:
                if st.button(f"Add {sym}", key=f"screener_add_{sector}_{sym}"):
                    add_ticker(sym, notes=f"From {sector} screener")
                    st.rerun()

if st.button("Refresh Screener"):
    try:
        for sector in settings.screener_sectors:
            screen_sector(sector)
        st.rerun()
    except Exception as e:
        st.error(f"Screener refresh failed: {e}")
