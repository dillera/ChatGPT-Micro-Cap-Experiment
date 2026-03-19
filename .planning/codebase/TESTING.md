# Testing Patterns

**Analysis Date:** 2026-03-19

## Test Framework

**Status:** No formal testing framework configured

**Runner:**
- Not detected. No pytest.ini, tox.ini, setup.py, pyproject.toml, or test configuration files found.

**Assertion Library:**
- Not applicable. No test framework in use.

**Run Commands:**
- Not applicable. Manual verification through script execution only.

## Test File Organization

**Location:**
- Not applicable. No test files found in repository.
- No test discovery pattern: No `test_*.py` or `*_test.py` files in codebase.

**Testing Approach:**
- Ad-hoc manual testing: Scripts are run directly with command-line arguments
- Integration testing via manual portfolio CSV execution
- Validation through print output and result inspection

## Current Testing Practices

**Manual Verification Pattern:**

The codebase relies on inline validation and print-based output verification:

**Example from `process_portfolio()` in `trading_script.py`:**
```python
# Manual input/validation loop - implicitly tests user interaction
while True:
    print(portfolio_df)
    action = input(
        f""" You have {cash} in cash.
Would you like to log a manual trade? Enter 'b' for buy, 's' for sell, or press Enter to continue: """
    ).strip().lower()

    if action == "b":
        ticker = input("Enter ticker symbol: ").strip().upper()
        order_type = input("Order type? 'm' = market-on-open, 'l' = limit: ").strip().lower()

        try:
            shares = float(input("Enter number of shares: "))
            if shares <= 0:
                raise ValueError
        except ValueError:
            print("Invalid share amount. Buy cancelled.")
            continue
```

**Runtime Assertions:**

Functions validate state before operations:

```python
def _ensure_df(portfolio: pd.DataFrame | dict[str, list[object]] | list[dict[str, object]]) -> pd.DataFrame:
    if isinstance(portfolio, pd.DataFrame):
        return portfolio.copy()
    if isinstance(portfolio, (dict, list)):
        return pd.DataFrame(portfolio)
    raise TypeError("portfolio must be a DataFrame, dict, or list[dict]")
```

**Data Fetch Robustness Testing:**

The codebase includes a fallback chain tested implicitly:

```python
def download_price_data(ticker: str, **kwargs: Any) -> FetchResult:
    """Fetch price data with Yahoo->Stooq fallback chain"""
    # Try Yahoo first
    df = _yahoo_download(ticker, **kwargs)
    if not df.empty:
        return FetchResult(df=df, source="yahoo")

    # Fallback to Stooq CSV
    df = _stooq_csv_download(ticker, **kwargs)
    if not df.empty:
        return FetchResult(df=df, source="stooq-csv")

    # Fallback to Stooq PDR
    df = _stooq_download(ticker, **kwargs)
    if not df.empty:
        return FetchResult(df=df, source="stooq-pdr")

    # Return empty with diagnostic source
    return FetchResult(df=pd.DataFrame(), source="empty")
```

**Example Test Scenarios (Implicit):**
- Empty data handling: Functions return `pd.DataFrame()` which is explicitly checked with `.empty`
- Network failure handling: Try-except blocks catch connection errors and return empty DataFrames
- Date range normalization: `_weekend_safe_range()` validates and normalizes date inputs
- Type validation: `_ensure_df()` validates portfolio input types

## Error Testing

**Pattern:**
No formal error testing framework. Error scenarios are tested via console interaction and print verification.

**Example error condition from `log_manual_buy()`:**
```python
# Type check at function start
if not isinstance(chatgpt_portfolio, pd.DataFrame) or chatgpt_portfolio.empty:
    chatgpt_portfolio = pd.DataFrame(
        columns=["ticker", "shares", "stop_loss", "buy_price", "cost_basis"]
    )

# Subsequent operations check for empty data
fetch = download_price_data(ticker, start=s, end=e, auto_adjust=False, progress=False)
data = fetch.df
if data.empty:
    print(f"Manual buy for {ticker} failed: no market data available (source={fetch.source}).")
    return cash, chatgpt_portfolio

# Numeric range validation
o = float(data.get("Open", [np.nan])[-1])
if np.isnan(o):
    o = float(data["Close"].iloc[-1])

# Business logic validation (price vs available cash)
cost_amt = exec_price * shares
if cost_amt > cash:
    print(f"Manual buy for {ticker} failed: cost {cost_amt:.2f} exceeds cash balance {cash:.2f}.")
    return cash, chatgpt_portfolio
```

## Mock/Fixture Patterns

**Framework:** Not applicable. No formal mocking library used.

**Implicit Mocking:**
- `FetchResult` dataclass with `source` field allows inspecting which data source was used
- Mock data via test CSV files (e.g., `chatgpt_portfolio_update.csv`) used for integration testing

**Test Data:**
```python
# Example from simple_automation.py - mock portfolio creation
portfolio_df = pd.DataFrame(columns=["ticker", "shares", "stop_loss", "buy_price", "cost_basis"])
cash = 10000.0  # Default starting cash
```

**Global Test Hooks:**
- `set_asof()` global function allows setting an artificial "today" date for reproducible testing
- `set_data_dir()` allows redirecting portfolio/trade log files to test directories

**Example from trading_script.py:**
```python
ASOF_DATE: pd.Timestamp | None = None

def set_asof(date: str | datetime | pd.Timestamp | None) -> None:
    """Set a global 'as of' date so the script treats that day as 'today'. Use 'YYYY-MM-DD' format."""
    global ASOF_DATE
    if date is None:
        print("No prior date passed. Using today's date...")
        ASOF_DATE = None
        return
    ASOF_DATE = pd.Timestamp(date).normalize()
    pure_date = ASOF_DATE.date()
    print(f"Setting date as {pure_date}.")
```

## Coverage

**Requirements:** Not enforced. No coverage tool configured.

**What Would Need Testing:**
- Data fetch fallback chain (`download_price_data()` → Yahoo → Stooq CSV → Stooq PDR)
- Weekend/holiday date normalization (`last_trading_date()`, `trading_day_window()`)
- Portfolio state transitions (buy, sell, stop-loss triggers)
- CSV I/O: Loading/saving portfolio, trade logs
- JSON configuration parsing (`load_benchmarks()`)
- Interactive user input validation
- Financial calculations: P&L, drawdown, Sharpe ratio, sortino ratio
- LLM response parsing and trade validation (`parse_llm_response()`)

## Test Types

**Unit Test Coverage (Implicit):**
- Date utilities: `_effective_now()`, `last_trading_date()`, `check_weekend()`, `trading_day_window()`
- Data normalization: `_normalize_ohlcv()`, `_to_datetime_index()`
- Configuration loading: `load_benchmarks()`, `_read_json_file()`
- Type validation: `_ensure_df()`

**Integration Test Coverage (Implicit):**
- End-to-end portfolio processing: `process_portfolio()` with live/test CSV files
- Daily results reporting: `daily_results()` with portfolio history
- Automated trading: `simple_automation.py` chain (generate prompt → call LLM → parse → execute)

**Manual E2E Tests:**
- Command: `python trading_script.py --file chatgpt_portfolio_update.csv`
- Expected: Portfolio loads, user prompted for trades, results printed
- Validation: CSV updates, trade log appends, calculations accurate

## Critical Untested Paths

**High Risk - Missing Tests:**
1. **Network Failures:** No retry logic or timeout handling tests for data fetch failures
2. **Data Quality:** No validation of stale/corrupted CSV data or malformed JSON configs
3. **Concurrency:** No safety for parallel script execution (shared CSV access)
4. **Boundary Conditions:** Single-day portfolios, missing OHLCV fields, zero-division in metrics
5. **LLM Response Parsing:** Regex-based JSON extraction (`parse_llm_response()`) brittle to varied formatting

**Example high-risk code from `simple_automation.py`:**
```python
def parse_llm_response(response: str) -> Dict[str, Any]:
    """Parse LLM response and extract trading decisions"""
    try:
        # Brittle regex extraction
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            json_str = json_match.group()
            return json.loads(json_str)
        else:
            return json.loads(response)
    except json.JSONDecodeError as e:
        print(f"Failed to parse LLM response: {e}")
        print(f"Raw response: {response}")
        return {"error": "Failed to parse response", "raw_response": response}
```

## Recommended Testing Strategy

**If formal testing is to be added:**

1. **Framework:** pytest (lightweight, no setup.py needed, fixtures via conftest.py)
2. **Structure:** Co-located test files
   - `test_trading_script.py` (unit tests for data layer, date utilities)
   - `test_portfolio.py` (integration tests for portfolio operations)
   - `test_automation.py` (LLM response parsing, trade validation)
3. **Key test categories:**
   - Date handling (weekends, holidays)
   - Data fetch fallback chain
   - CSV I/O (load, save, append)
   - Financial calculations
   - Error recovery
   - Input validation

---

*Testing analysis: 2026-03-19*
