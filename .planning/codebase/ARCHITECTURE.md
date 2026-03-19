# Architecture

**Analysis Date:** 2026-03-19

## Pattern Overview

**Overall:** Modular data pipeline with layered separation between market data access, portfolio management, and presentation.

**Key Characteristics:**
- Multi-stage fallback pattern for data resilience (Yahoo → Stooq-PDR → Stooq-CSV → Index proxies)
- Clean separation between data fetching, portfolio operations, and visualization
- LLM integration layer (`simple_automation.py`) for autonomous trading decisions
- Stateful portfolio tracking via CSV files with complete trade logging
- Daily trading cycle with automatic stop-loss enforcement

## Layers

**Data Access Layer:**
- Purpose: Fetch and normalize market data from multiple sources with graceful fallback
- Location: `trading_script.py` lines 174–368 (functions: `download_price_data`, `_yahoo_download`, `_stooq_download`, `_stooq_csv_download`)
- Contains: Price fetching, data normalization, source selection logic, Stooq symbol remapping
- Depends on: yfinance, pandas-datareader, requests, pandas
- Used by: Portfolio operations, daily pricing updates, visualization

**Portfolio Operations Layer:**
- Purpose: Manage holdings, execute trades, track cost basis, enforce stop-losses, calculate P&L
- Location: `trading_script.py` lines 375–850 (functions: `process_portfolio`, `log_manual_buy`, `log_manual_sell`, `log_sell`, `load_latest_portfolio_state`)
- Contains: Interactive trade entry, MOO/limit order handling, stop-loss checks, PnL calculations, CSV I/O
- Depends on: Data Access Layer, pandas, Path utilities
- Used by: Main script, automation layer, daily results reporting

**LLM Decision Engine:**
- Purpose: Generate trading recommendations based on portfolio state; parse and execute LLM-generated trades
- Location: `simple_automation.py` (functions: `generate_trading_prompt`, `call_openai_api`, `parse_llm_response`, `execute_automated_trades`)
- Contains: Prompt construction, OpenAI API integration, trade validation, dry-run mode
- Depends on: Portfolio Operations Layer, OpenAI client
- Used by: Automated trading workflow

**Analytics & Visualization Layer:**
- Purpose: Calculate performance metrics, generate comparison charts against benchmarks
- Location: `Scripts and CSV Files/Generate_Graph.py` (functions: `load_portfolio_totals`, `download_sp500`, `find_largest_gain`, `compute_drawdown`)
- Contains: Portfolio totals aggregation, S&P 500 normalization, gain/drawdown metrics, matplotlib charting
- Depends on: yfinance, pandas, matplotlib
- Used by: Performance reporting

**Utility & Configuration Layer:**
- Purpose: Handle date/time logic, environment configuration, benchmark loading
- Location: `trading_script.py` lines 39–172 (functions: `set_asof`, `last_trading_date`, `check_weekend`, `load_benchmarks`)
- Contains: Trading date resolution (weekend handling), as-of-date override, benchmark ticker loading from `tickers.json`
- Depends on: pandas, os, json
- Used by: All layers

## Data Flow

**Daily Trading Workflow:**

1. **Initialize**: Load portfolio CSV, retrieve current cash balance, determine last trading date (maps Sat/Sun → Fri)
2. **Fetch Prices**: Call `download_price_data()` for each ticker; fallback chain: Yahoo → Stooq-PDR → Stooq-CSV → Index proxy
3. **Check Stop-Losses**: Compare current price against stop-loss levels; execute auto-sells if breached
4. **Calculate P&L**: For remaining positions, compute unrealized gains/losses; update cost basis if partially sold
5. **Record Results**: Append daily totals (ticker='TOTAL') to portfolio CSV with equity, PnL, benchmark comparison
6. **Save State**: Update CSV files with new holdings and trade log entries

**Automated Trading Flow:**

1. **Load Portfolio**: Call `load_latest_portfolio_state()` to get current holdings and cash
2. **Generate Prompt**: Call `generate_trading_prompt()` with portfolio data, cash, equity totals
3. **Call LLM**: Send prompt to OpenAI API (configurable model, temperature=0.3)
4. **Parse Response**: Extract JSON from LLM response; validate format and trade structure
5. **Execute Trades**: For each recommended trade: validate, check cash sufficiency, simulate or execute buy/sell
6. **Log Results**: Save LLM response and execution status to `llm_responses.jsonl`

**State Management:**

- **Portfolio CSV** (`chatgpt_portfolio_update.csv`): One row per holding + daily TOTAL row. Columns: Date, Ticker, Shares, Buy Price, Cost Basis, Current Price, Current Value, PnL, Stop-Loss, etc.
- **Trade Log CSV** (`chatgpt_trade_log.csv`): One row per trade. Columns: Date, Ticker, Shares Bought/Sold, Price, Cost Basis, PnL, Reason
- **LLM Responses JSONL** (`llm_responses.jsonl`): One JSON object per LLM call. Fields: timestamp, response (parsed), raw_response
- **Benchmarks Config** (`tickers.json`, optional): JSON file with `{"benchmarks": ["IWO", "XBI", "SPY", "IWM"]}` (or defaults to hardcoded list)

## Key Abstractions

**FetchResult:**
- Purpose: Encapsulate price data with its source origin for traceability
- Examples: `FetchResult(df=..., source="yahoo")`, `FetchResult(df=..., source="stooq-csv")`
- Pattern: Dataclass with `df: pd.DataFrame` and `source: str` fields (lines 192–195)

**Portfolio Dataframe:**
- Purpose: In-memory representation of holdings with calculated metrics
- Columns: `ticker`, `shares`, `stop_loss`, `buy_price`, `cost_basis`, plus computed: `current_price`, `current_value`, `pnl`
- Pattern: Pandas DataFrame loaded from CSV, modified in-memory, written back to CSV

**Trading Prompt Template:**
- Purpose: Structured text sent to LLM with current portfolio state
- Pattern: Multi-line string with sections: Holdings, Cash Snapshot, Trading Rules, JSON format request (lines 44–78 in simple_automation.py)

**Trade Command Object:**
- Purpose: LLM-generated trade instruction parsed from JSON
- Pattern: Dict with keys: `action` (buy/sell/hold), `ticker`, `shares`, `price`, `stop_loss`, `reason`, `confidence` (optional)

## Entry Points

**Main Trading Script:**
- Location: `trading_script.py` line 1147 (function `main()`)
- Triggers: Direct execution `python trading_script.py` with optional `--portfolio` and `--data-dir` flags
- Responsibilities: Load portfolio CSV, run daily pricing loop, execute stop-losses, calculate and log results

**Automated Trading Entry:**
- Location: `simple_automation.py` line 240 (function `main()`)
- Triggers: Direct execution `python simple_automation.py --api-key=... [--model=...] [--dry-run]`
- Responsibilities: Call LLM API, parse responses, validate and execute trades, log interactions

**Graph Generation Entry:**
- Location: `Scripts and CSV Files/Generate_Graph.py` line 104 (function `main()`)
- Triggers: Direct execution `python Generate_Graph.py` or imported by other scripts
- Responsibilities: Load portfolio totals, download benchmark data, calculate metrics, generate comparison chart

## Error Handling

**Strategy:** Graceful degradation with logged failures and fallback paths.

**Patterns:**
- **Data Fetching**: Return empty DataFrame on failure; multi-stage fallback chain ensures *some* data retrieved if possible. Failures logged but do not halt execution.
- **CSV I/O**: Use pandas `read_csv()` with error coercion; concat/append patterns used to safely merge trade records.
- **JSON Parsing**: Try `json.load()` then regex extraction as fallback; on failure, log warning and return empty/error dict.
- **LLM API**: Wrap `openai.ChatCompletion.create()` in try-except; return error dict on API failure; dry-run mode bypasses actual execution.
- **Type Coercion**: Use `pd.to_numeric(..., errors='coerce')` for CSV values; check `pd.isna()` before accessing fields; provide defaults (e.g., stop_loss=0.0 if missing).

## Cross-Cutting Concerns

**Logging:**
- Info/warnings via Python `logging` module (initialized with `logger = logging.getLogger(__name__)` in trading_script.py line 77)
- CSV files serve as execution audit trail (trade log, portfolio snapshots)
- JSONL file captures all LLM interactions for transparency

**Validation:**
- Ticker symbols normalized to uppercase before lookups
- Share counts validated > 0, prices > 0, cash sufficiency checked before trade execution
- Stop-loss values accepted as-is (0 means no stop-loss enforced)
- LLM trade recommendations validated for required fields and reasonable values

**Authentication:**
- OpenAI API key retrieved from command-line arg or `OPENAI_API_KEY` environment variable (simple_automation.py line 88)
- No session/token management; API key passed directly to OpenAI client on each call

**Time Handling:**
- All dates normalized to OHLCV index (pd.DatetimeIndex); weekend/Saturday/Sunday mapped to prior Friday
- `last_trading_date()` called at start of each daily cycle; ASOF_DATE override allows backtesting
- CSV Date columns stored as ISO strings (YYYY-MM-DD); converted to Timestamp on load

---

*Architecture analysis: 2026-03-19*
