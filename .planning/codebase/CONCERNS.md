# Codebase Concerns

**Analysis Date:** 2026-03-19

## Tech Debt

**Broad exception handling with silent failures:**
- Issue: Multiple try-except blocks catch `Exception` broadly without specific error types, masking root causes
- Files: `trading_script.py` (lines 33-37, 199-202, 233-234, 272-273), `simple_automation.py` (line 36, 101-102)
- Impact: Debugging becomes difficult; failures go unlogged; data fetching silently returns empty DataFrames when it should alert
- Fix approach: Replace `except Exception:` with specific exception types (FileNotFoundError, JSONDecodeError, RequestException, etc.). Add logging for all caught exceptions. Distinguish between expected and unexpected failures.

**Simulated trades instead of actual execution:**
- Issue: `simple_automation.py` contains stub code that simulates trades but doesn't execute them against the actual portfolio
- Files: `simple_automation.py` (lines 140, 153 with "For now, just simulate the trade" comments)
- Impact: LLM-recommended trades are parsed but never actually applied to the portfolio; automation feature is incomplete
- Fix approach: Implement actual trade execution by calling buy/sell functions from `trading_script.py` and persisting changes to CSV files

**Hard-coded configuration scattered throughout code:**
- Issue: Magic numbers and default values (benchmark tickers, risk-free rate, window sizes) embedded directly in functions
- Files: `trading_script.py` (lines 69, 942, 960, 1007, 1174); `simple_automation.py` (lines 97-98, 183)
- Impact: Difficult to adapt for different use cases; risk-free rate assumptions outdated; changing parameters requires code edits
- Fix approach: Move all configuration to a central config file (JSON or YAML) or environment variables; load at startup

**Global mutable state (ASOF_DATE):**
- Issue: Module-level `ASOF_DATE` global variable modified by `set_asof()` function creates hidden dependencies and state management issues
- Files: `trading_script.py` (lines 40, 42-57, 59-60)
- Impact: Tests can interfere with each other if run in sequence; concurrent execution could cause race conditions; state is invisible to callers
- Fix approach: Replace with explicit parameter passing or a configuration object; remove global state from module initialization

**Bare `pass` statements in error handlers:**
- Issue: Exception handlers contain only `pass` without logging or recovery logic
- Files: `trading_script.py` (lines 202, 1071)
- Impact: Silent failures when converting datetime indices and fetching data; no indication of what went wrong
- Fix approach: Add descriptive logging and potentially raise or return sentinel values to signal failures

## Known Bugs

**Empty DataFrame handling in data layer:**
- Symptoms: When Yahoo Finance fails, code relies on Stooq fallback, but empty DataFrame returns don't log the failure
- Files: `trading_script.py` (lines 214-235 for `_yahoo_download`, lines 237-273 for `_stooq_csv_download`)
- Trigger: Network outage, API rate limiting, delisted ticker, or invalid ticker symbol
- Workaround: Check the source field in FetchResult to determine if fallback was used

**Unvalidated DataFrame operations:**
- Symptoms: Code assumes specific columns exist (Open, High, Low, Close, Adj Close) but doesn't validate before access
- Files: `trading_script.py` (lines 874-876 in `daily_results`, lines 874-879 accessing `df["Close"].iloc[-1]`)
- Trigger: Malformed CSV, incompatible data source, or missing OHLCV data
- Workaround: Manual inspection of CSV data files; crashes with IndexError if data is missing

**Date parsing with `errors="coerce"`:**
- Symptoms: Invalid dates are silently converted to NaT (Not a Time), potentially causing silent data loss
- Files: `trading_script.py` (lines 903, 1109, 1141 using `pd.to_datetime(..., errors="coerce")`)
- Trigger: Malformed date strings in CSV imports; inconsistent date formats
- Workaround: Data validation after parsing to identify NaT values before processing

**JSON parsing with regex extraction:**
- Symptoms: LLM response parsing uses greedy regex to extract JSON, which can extract malformed JSON
- Files: `simple_automation.py` (lines 109-114: `re.search(r'\{.*\}', response, re.DOTALL)`)
- Trigger: LLM response contains multiple JSON objects or nested structures
- Workaround: Fallback parsing catches JSONDecodeError but loses context about which part failed

## Security Considerations

**API key handling in simple_automation.py:**
- Risk: API keys passed via command-line arguments are visible in process lists; environment variables recommended but not enforced
- Files: `simple_automation.py` (lines 243-254)
- Current mitigation: Code accepts env var `OPENAI_API_KEY` as fallback
- Recommendations: (1) Warn when API key passed via CLI, (2) Document secure env var setup, (3) Never log raw API keys, (4) Consider keyring/vault for credentials

**No input validation for trading parameters:**
- Risk: User-provided trade quantities, prices, and stop-losses are not validated for reasonableness
- Files: `simple_automation.py` (lines 126-146 in `execute_automated_trades` and `trading_script.py` interactive entry)
- Current mitigation: Basic type conversion and comparison against cash balance
- Recommendations: (1) Add bounds checking (min/max quantities, price sanity checks), (2) Validate ticker symbols before submission, (3) Add circuit breakers for large position sizes

**Network requests without timeout or retry limits:**
- Risk: Stooq CSV endpoint requests use timeout=10 but no retry logic; hung connections possible
- Files: `trading_script.py` (line 254 in `_stooq_csv_download`)
- Current mitigation: 10-second timeout on requests.get()
- Recommendations: (1) Implement exponential backoff for transient failures, (2) Add max retry count, (3) Log all network failures with timing info

**Data source reliability not validated:**
- Risk: Code silently falls back from Yahoo → Stooq → empty DataFrame without alerting user
- Files: `trading_script.py` (lines 192-240, 314-339 in `download_price_data`)
- Current mitigation: FetchResult includes source field for transparency
- Recommendations: (1) Log data source for every fetch, (2) Alert if fallback chain exhausted, (3) Track success/failure rates by source

## Performance Bottlenecks

**Full portfolio evaluation on every daily_results() call:**
- Problem: `daily_results()` downloads price data for ALL holdings + benchmarks sequentially
- Files: `trading_script.py` (lines 865-881)
- Cause: No parallelization; network latency multiplied by number of tickers
- Improvement path: (1) Use concurrent.futures.ThreadPoolExecutor for parallel downloads, (2) Cache OHLCV data when unchanged, (3) Add data refresh interval (skip if <60 sec old)

**CAPM calculation requires separate S&P 500 fetch:**
- Problem: Beta/alpha calculation downloads S&P 500 data even if already available
- Files: `trading_script.py` (lines 976-977, 1007-1008)
- Cause: No caching of benchmark data; fetched twice per daily_results() call
- Improvement path: (1) Cache S&P 500 data in module-level dict with timestamp, (2) Reuse across calculations, (3) Add cache TTL config

**CSV reads for every portfolio state lookup:**
- Problem: `process_portfolio()` reads full CSV files without indexing or caching
- Files: `trading_script.py` (line 884)
- Cause: Linear scan through entire portfolio history to find TOTAL rows
- Improvement path: (1) Index by ticker or date, (2) Implement rolling window for recent data only, (3) Consider SQLite for structured queries

**No connection pooling for requests:**
- Problem: Each Stooq fetch creates new requests.Session()
- Files: `trading_script.py` (line 254 in `_stooq_csv_download` calls requests.get without reusing sessions)
- Cause: New TCP connection per request; SSL handshake overhead
- Improvement path: (1) Create global session in module init, (2) Reuse for all network requests, (3) Configure connection pooling

## Fragile Areas

**Portfolio CSV mutation without backup:**
- Files: `trading_script.py` (all buy/sell operations that append to CSV)
- Why fragile: Single corrupt line or failed write leaves portfolio in inconsistent state; no rollback mechanism
- Safe modification: (1) Write to temporary file first, (2) Validate before atomic rename, (3) Keep backup copies with timestamps, (4) Implement WAL-style journaling
- Test coverage: No unit tests for CSV mutation; manual testing only

**Interactive trade entry state machine:**
- Files: `trading_script.py` (lines 407-531 in user input loop)
- Why fragile: Complex nested conditionals and exception handling for MOO/limit order parsing; easy to introduce edge cases
- Safe modification: (1) Extract to separate class/module, (2) Add state machine tests, (3) Document all exit paths, (4) Add timeout for user input
- Test coverage: No automated tests; manual CLI only

**Benchmark loading with silent defaults:**
- Files: `trading_script.py` (lines 98-146 in `load_benchmarks()`)
- Why fragile: Malformed tickers.json silently falls back to hardcoded defaults; user unaware of misconfiguration
- Safe modification: (1) Fail loudly if config file exists but is invalid, (2) Log all config sources, (3) Add validation schema, (4) Document expected format
- Test coverage: No tests for tickers.json parsing; default path untested

**Date alignment for multi-source data:**
- Files: `trading_script.py` (lines 262-266 date filtering, lines 988-990 index alignment)
- Why fragile: Timezone-naive timestamps; DST transitions; market holidays not considered; silent NaN propagation
- Safe modification: (1) Use explicit UTC timezone throughout, (2) Define trading calendar, (3) Handle holiday gaps, (4) Validate alignment before calculations
- Test coverage: No tests for edge cases (holidays, weekends, gaps)

**LLM response parsing with no schema validation:**
- Files: `simple_automation.py` (lines 105-118 in `parse_llm_response`)
- Why fragile: JSON structure assumed but not validated; missing fields cause KeyError; type conversion implicit
- Safe modification: (1) Use JSON schema validation library, (2) Provide default values for missing fields, (3) Validate trade parameters before execution, (4) Log parse failures with raw response
- Test coverage: No unit tests for parse_llm_response() with malformed input

## Scaling Limits

**Memory usage with large portfolio history:**
- Current capacity: CSV files grow unbounded; entire history loaded into memory for analytics
- Limit: At 250+ trading days with 20+ holdings, CSV grows to ~500KB; daily_results() calculates Sharpe/Sortino over full history
- Scaling path: (1) Implement rolling window calculations (e.g., 252-day Sharpe), (2) Archive old data to separate files, (3) Partition CSV by year, (4) Cache pre-calculated metrics

**Network request rate limits:**
- Current capacity: Stooq/Yahoo may rate-limit after ~100 requests
- Limit: With 30 holdings + benchmarks, daily_results() makes ~35 requests; at 2 requests/sec = 17+ seconds per run
- Scaling path: (1) Batch data fetches, (2) Implement request queue with rate limiting, (3) Cache data aggressively, (4) Use premium APIs for higher limits

**Concurrent execution collisions:**
- Current capacity: Single set_data_dir() call; all instances share module globals
- Limit: Running multiple portfolios or parallel backtests will corrupt shared state
- Scaling path: (1) Make state instance-based not global, (2) Add file-level locking for CSV access, (3) Use separate processes for independent runs

## Dependencies at Risk

**pandas_datareader optional dependency:**
- Risk: Imported conditionally; Stooq fallback only works if pandas_datareader installed; fails silently if missing
- Files: `trading_script.py` (lines 33-37)
- Impact: Stooq feature silently unavailable; users unaware they're limited to Yahoo-only fallback
- Migration plan: (1) Make explicit in requirements.txt as optional, (2) Raise error if Stooq access attempted without library, (3) Document installation step

**yfinance reliability concerns:**
- Risk: External dependency owned by third party; API changes, rate limits, or service outages break data pipeline
- Files: `trading_script.py` (lines 28, 215-235 in `_yahoo_download`)
- Impact: Complete loss of price data when yfinance fails; portfolio cannot be updated
- Migration plan: (1) Evaluate alternative sources (IEX Cloud, Alpaca, Polygon), (2) Implement aggregation across multiple sources, (3) Cache data locally, (4) Add data quality checks

**OpenAI API cost and availability:**
- Risk: simple_automation.py requires active API key and account; rate limits apply; pricing ongoing
- Files: `simple_automation.py` (lines 83-102, 251-254)
- Impact: Automated trading feature unavailable if quota exhausted or API down
- Migration plan: (1) Add cost estimation before API calls, (2) Implement fallback to local models, (3) Add dry-run mode to verify requests before execution, (4) Monitor API usage

**Python 3.11+ type hints:**
- Risk: Union types with `|` operator and PEP 604 syntax require Python 3.11+; older versions will fail
- Files: `trading_script.py` (lines 22, 40, 277)
- Impact: Code unmaintainable on Python <3.11
- Migration plan: (1) Document minimum version explicitly, (2) Add pre-flight Python version check, (3) Use typing.Union for compatibility if needed, (4) Test on minimum supported version

## Missing Critical Features

**No position size validation:**
- Problem: LLM recommendations for trade sizes not validated against margin/leverage limits
- Blocks: Cannot safely execute LLM trades without manual review
- Files: `simple_automation.py` (lines 121-165 in `execute_automated_trades`)
- Recommended implementation: (1) Calculate max position size based on account equity, (2) Check portfolio concentration, (3) Enforce max notional value, (4) Validate against buying power

**No order confirmation or approval workflow:**
- Problem: Trades execute immediately without human confirmation step
- Blocks: No safety net for LLM errors; cannot audit decisions before execution
- Files: `simple_automation.py` (lines 218-219)
- Recommended implementation: (1) Write trades to staging file, (2) Require CLI confirmation, (3) Log decision rationale, (4) Add 5-minute cancel window

**No backtesting framework:**
- Problem: ASOF_DATE override exists but no systematic backtesting on historical data
- Blocks: Cannot validate strategy performance before live trading
- Files: `trading_script.py` (lines 40-57)
- Recommended implementation: (1) Add date range parameter to main(), (2) Implement portfolio snapshots at each date, (3) Calculate returns, Sharpe, drawdown, (4) Compare across strategies

**No alert/notification system:**
- Problem: All output is console-only; no email/Slack/SMS for critical events
- Blocks: Cannot monitor portfolio outside of running the script manually
- Files: All functions print() to stdout
- Recommended implementation: (1) Implement observer pattern for critical events, (2) Add email on stop-loss triggers, (3) Send daily summary reports, (4) Alert on data fetch failures

**No transaction cost modeling:**
- Problem: Analytics assume zero commissions and slippage
- Blocks: Performance metrics overstate returns
- Files: `trading_script.py` (lines 947-970 Sharpe/Sortino calculations)
- Recommended implementation: (1) Add commission per trade, (2) Estimate slippage based on volume, (3) Adjust returns downward, (4) Track real vs modeled P&L

## Test Coverage Gaps

**Data layer not tested:**
- What's not tested: download_price_data(), _yahoo_download(), _stooq_csv_download() with various failure modes
- Files: `trading_script.py` (lines 192-339)
- Risk: Yahoo API changes break silently; Stooq fallback behavior unknown until failure occurs; date filtering incorrect
- Priority: **High** - Core data pipeline

**Portfolio persistence not tested:**
- What's not tested: CSV reads/writes, concurrent access, malformed CSV recovery, state consistency
- Files: `trading_script.py` (lines 747-849 for trades, lines 884-951 for reads)
- Risk: Portfolio state corruption, silent data loss, inability to recover from crashes
- Priority: **High** - Data integrity

**LLM integration not tested:**
- What's not tested: parse_llm_response() with malformed JSON, invalid trade formats, missing fields
- Files: `simple_automation.py` (lines 105-118)
- Risk: LLM output parsing fails unexpectedly; trades not executed
- Priority: **High** - Automation reliability

**Date calculations not tested:**
- What's not tested: Weekends, holidays, timezone handling, leap years, DST transitions
- Files: `trading_script.py` (lines 153-170 date helpers, lines 262-266 filtering)
- Risk: Silent data misalignment, off-by-one errors in returns calculation, incorrect Sharpe ratios
- Priority: **Medium** - Analytics accuracy

**Error recovery not tested:**
- What's not tested: Network timeouts, partial CSV corruption, missing config files, API failures
- Files: Multiple (all exception handlers)
- Risk: Unknown behavior under adverse conditions; graceful degradation unknown
- Priority: **Medium** - Robustness

---

*Concerns audit: 2026-03-19*
