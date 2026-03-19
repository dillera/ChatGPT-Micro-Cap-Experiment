# Coding Conventions

**Analysis Date:** 2026-03-19

## Naming Patterns

**Files:**
- Snake_case for Python modules: `trading_script.py`, `simple_automation.py`, `ProcessPortfolio.py`
- Descriptive names indicating purpose: `Generate_Graph.py`, `ProcessPortfolio.py`

**Functions:**
- Snake_case: `set_asof()`, `download_price_data()`, `process_portfolio()`, `daily_results()`
- Private functions prefixed with underscore: `_effective_now()`, `_read_json_file()`, `_ensure_df()`, `_yahoo_download()`, `_stooq_csv_download()`, `_normalize_ohlcv()`
- Verb-first naming for actions: `load_benchmarks()`, `check_weekend()`, `log_sell()`, `log_manual_buy()`, `call_openai_api()`, `parse_llm_response()`

**Variables:**
- Snake_case: `chatgpt_portfolio`, `trading_day`, `cost_amt`, `portfolio_df`, `portfolio_dict`
- All-caps for module-level constants: `ASOF_DATE`, `PORTFOLIO_CSV`, `TRADE_LOG_CSV`, `STOOQ_MAP`, `STOOQ_BLOCKLIST`, `DEFAULT_BENCHMARKS`, `HAS_OPENAI`, `_HAS_PDR`
- Abbreviated names for loop/temporary variables: `df` (DataFrame), `e` (end date), `s` (start date), `r` (returns), `o` (open), `h` (high), `l` (low)

**Types:**
- PascalCase for custom types: `FetchResult` (dataclass)

## Code Style

**Formatting:**
- No explicit linter or formatter configured (no `.eslintrc`, `.prettierrc`, or `pyproject.toml` found)
- 4-space indentation standard (implicit from Python conventions)
- Line length appears to follow implicit ~100 character guideline

**Linting:**
- No linting tools configured
- Type hints used consistently for function signatures (e.g., `def set_asof(date: str | datetime | pd.Timestamp | None) -> None:`)
- Modern Python union syntax (`|`) for type hints: `pd.Timestamp | None`, `dict[str, list[object]] | list[dict[str, object]]`

## Import Organization

**Order:**
1. Docstring module documentation
2. `from __future__ import annotations` (modern type hint syntax)
3. Standard library imports (dataclasses, datetime, pathlib, typing, os, warnings)
4. Third-party imports (numpy, pandas, yfinance, json, logging)
5. Optional/conditional imports in try-except blocks (pandas_datareader, openai)

**Path Aliases:**
- No path aliases configured; absolute imports from `pathlib.Path`

**Example from `trading_script.py`:**
```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast, Dict, List, Optional
import os
import warnings

import numpy as np
import pandas as pd
import yfinance as yf
import json
import logging

try:
    import pandas_datareader.data as pdr
    _HAS_PDR = True
except Exception:
    _HAS_PDR = False
```

## Error Handling

**Patterns:**
- Empty DataFrame returns for failed operations: `_yahoo_download()`, `_stooq_download()`, `_stooq_csv_download()` return `pd.DataFrame()` on exception
- Broad exception handling with `try-except Exception`: Used when silencing third-party library warnings or handling data fetch failures
- Validation before operations: Checks for empty DataFrames, type validation (e.g., `isinstance()` checks), numeric range checks
- User-facing error messages with context: `print()` statements include operation details, sources, and specific failure reasons

**Example from `process_portfolio()`:**
```python
try:
    shares = float(input("Enter number of shares: "))
    if shares <= 0:
        raise ValueError
except ValueError:
    print("Invalid share amount. Buy cancelled.")
    continue
```

**Exception propagation with context:**
```python
try:
    fetch = download_price_data(ticker, ...)
    data = fetch.df
    if data.empty:
        print(f"MOO buy for {ticker} failed: no market data available (source={fetch.source}).")
        continue
except Exception as e:
    raise Exception(f"Download for {ticker} failed. {e} Try checking internet connection.")
```

## Logging

**Framework:** Python `logging` module

**Patterns:**
- Logger created at module level: `logger = logging.getLogger(__name__)` in `trading_script.py`
- Warning level for recoverable issues: `logger.warning()` for JSON parsing failures, missing configuration
- Third-party library silencing: `logging.getLogger("yfinance").setLevel(logging.CRITICAL)`
- Primary output via `print()` for user-facing messages (interactive console)

**Example:**
```python
logger = logging.getLogger(__name__)
logger.warning("tickers.json present but malformed: %s -> %s. Falling back to defaults.", path, exc)
```

## Comments

**When to Comment:**
- Module-level docstrings explaining design decisions and behaviors
- Complex algorithms (e.g., max drawdown calculation, CAPM stats)
- Fallback and retry logic (e.g., Yahoo→Stooq fetch chain)
- Configuration notes and non-obvious dependencies

**JSDoc/TSDoc:**
- Not used (Python project); docstrings follow standard Python conventions
- Function docstrings present but minimal: `"""Return last trading date (Mon–Fri), mapping Sat/Sun -> Fri."""`
- Module docstrings detailed: Large multi-line docstrings at file start explaining purpose and behavior

**Example module docstring from `trading_script.py`:**
```python
"""Utilities for maintaining the ChatGPT micro-cap portfolio.

This module rewrites the original script to:
- Centralize market data fetching with a robust Yahoo->Stooq fallback
- Ensure ALL price requests go through the same accessor
- Handle empty Yahoo frames (no exception) so fallback actually triggers
- Normalize Stooq output to Yahoo-like columns
- Make weekend handling consistent and testable
- Keep behavior and CSV formats compatible with prior runs

Notes:
- Some tickers/indices are not available on Stooq (e.g., ^RUT). These stay on Yahoo.
- Stooq end date is exclusive; we add +1 day for ranges.
- "Adj Close" is set equal to "Close" for Stooq to match downstream expectations.
"""
```

## Function Design

**Size:** Functions generally 20–100 lines
- Small utility functions (5–15 lines): `check_weekend()`, `trading_day_window()`, `_to_datetime_index()`
- Medium business logic (30–50 lines): `download_price_data()`, `process_portfolio()` (core loop)
- Large functions (100+ lines): `daily_results()` (1090+ lines with extensive output formatting and stats)

**Parameters:**
- Type-hinted parameters standard: `def download_price_data(ticker: str, **kwargs: Any) -> FetchResult:`
- Optional parameters with defaults: `def call_openai_api(prompt: str, api_key: str, model: str = "gpt-4") -> str:`
- Union types for flexibility: `def set_asof(date: str | datetime | pd.Timestamp | None) -> None:`
- `**kwargs` for forwarding API options: Used in `_yahoo_download()`, `download_price_data()`

**Return Values:**
- Single values: `str`, `float`, `pd.DataFrame`
- Tuples for multiple related values: `tuple[pd.Timestamp, pd.Timestamp]`, `tuple[float, pd.DataFrame]`
- Custom dataclasses for complex returns: `FetchResult` with `.df` and `.source` fields
- Empty collections for "no data" cases: `pd.DataFrame()`, `[]`, `{}`

**Example function signature pattern:**
```python
def log_manual_buy(
    buy_price: float,
    shares: float,
    ticker: str,
    stoploss: float,
    cash: float,
    chatgpt_portfolio: pd.DataFrame,
    interactive: bool = True,
) -> tuple[float, pd.DataFrame]:
```

## Module Design

**Exports:**
- Public API functions intended for import: `process_portfolio()`, `daily_results()`, `load_latest_portfolio_state()`, `set_data_dir()`, `set_asof()`, `check_weekend()`, `last_trading_date()`, `download_price_data()`
- Constants available for import: `PORTFOLIO_CSV`, `TRADE_LOG_CSV`
- Private functions (leading underscore) not meant for external use: `_effective_now()`, `_ensure_df()`, `_normalize_ohlcv()`, `_yahoo_download()`

**Barrel Files:**
- Not used; modules import only needed functions directly
- Example: `from trading_script import process_portfolio, daily_results, load_latest_portfolio_state, set_data_dir, PORTFOLIO_CSV, TRADE_LOG_CSV, last_trading_date`

## Data Structures

**Conventions:**
- pandas DataFrames for tabular data: Portfolio holdings, price history, trade logs
- Dictionaries for configuration: Benchmark tickers, trade records
- Dataclasses for structured API responses: `FetchResult` with source metadata
- Column naming: DataFrame columns use human-readable names ("Ticker", "Shares", "Close Price") with space; underscore used in Python dicts ("ticker", "buy_price")

---

*Convention analysis: 2026-03-19*
