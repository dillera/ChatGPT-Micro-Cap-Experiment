# Technology Stack

**Analysis Date:** 2026-03-19

## Languages

**Primary:**
- Python 3.11+ - Core scripting and trading automation; all core logic is written in Python

**Secondary:**
- Markdown - Documentation and research summaries

## Runtime

**Environment:**
- Python 3.11+ (specified in `README.md` as system requirement)

**Package Manager:**
- pip - Standard Python package manager
- Lockfile: `requirements.txt` present (not a pinned lockfile, but defines core dependencies)

## Frameworks

**Core:**
- pandas 2.2.2 - Data manipulation, portfolio management, CSV handling
- numpy 2.3.2 - Numerical computations for financial analysis
- yfinance 0.2.65 - Primary market data source (Yahoo Finance API integration)

**Data/Visualization:**
- matplotlib 3.8.4 - Performance visualization and charting

**Optional/Fallback:**
- pandas-datareader - Stooq data fallback (optional, gracefully handled if missing)
- openai - ChatGPT API integration for trading decisions (optional; imported conditionally in `simple_automation.py`)

**Development/Build:**
- None detected - No build system, testing framework, or dev toolchain currently in place

## Key Dependencies

**Critical:**
- yfinance 0.2.65 - Fetches OHLCV data from Yahoo Finance with configurable User-Agent and session management
- pandas 2.2.2 - Core data structure for portfolio state, CSV I/O, and time-series operations
- numpy 2.3.2 - Vectorized calculations for performance metrics, CAPM analysis, Sharpe/Sortino ratios

**Infrastructure:**
- pandas-datareader - Secondary data source; used as fallback to fetch from Stooq via `pandas_datareader.data.DataReader()`
- requests - HTTP client for direct Stooq CSV downloads (`https://stooq.com/q/d/l/`)
- openai - Optional; enables ChatGPT-4 integration in `simple_automation.py` for LLM-powered trading recommendations

## Configuration

**Environment:**
- `ASOF_DATE` - Optional environment variable (format: `YYYY-MM-DD`) to override the "today" date for backtesting/historical analysis
  - Set via: `export ASOF_DATE=2024-01-15` before running scripts
  - Used by: `trading_script.py` at module load time (line 55)
- `OPENAI_API_KEY` - Required for LLM trading features (optional if not using AI automation)
  - Set via: `export OPENAI_API_KEY=sk-...` or pass `--api-key` to `simple_automation.py`

**Build:**
- No explicit build system
- No `setup.py`, `pyproject.toml`, or wheel configuration detected
- Scripts run directly via `python trading_script.py` or `python simple_automation.py`

**Configuration Files:**
- `tickers.json` (optional) - Benchmark ticker list
  - Expected schema: `{"benchmarks": ["IWO", "XBI", "SPY", "IWM"]}`
  - Location: Searched in script directory or project root
  - Fallback: `DEFAULT_BENCHMARKS = ["IWO", "XBI", "SPY", "IWM"]` if missing

## Platform Requirements

**Development:**
- Python 3.11+ installed
- Internet connection for market data (Yahoo Finance, Stooq, optional OpenAI API)
- ~10MB storage for CSV data files (portfolio and trade logs)
- No OS-specific dependencies; cross-platform compatible

**Production:**
- Same as development (Python-based CLI tool)
- Deployment: Standalone scripts; can be scheduled via cron, Task Scheduler, or orchestrated via external runners
- Data storage: Local filesystem for CSV files (no database required)

## Data & Caching

**Data Storage:**
- CSV files stored in script directory by default
  - `chatgpt_portfolio_update.csv` - Current portfolio state
  - `chatgpt_trade_log.csv` - Complete trade history
- Overridable via `set_data_dir()` function in `trading_script.py`

**Network/Caching:**
- None detected - Scripts fetch fresh data on each run; no built-in caching layer
- Session management via `requests.Session()` with User-Agent for Yahoo Finance to avoid rate limits

---

*Stack analysis: 2026-03-19*
