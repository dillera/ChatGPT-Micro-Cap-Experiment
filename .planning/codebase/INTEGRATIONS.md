# External Integrations

**Analysis Date:** 2026-03-19

## APIs & External Services

**Market Data:**
- **Yahoo Finance** - Primary data source for OHLCV (Open, High, Low, Close, Volume) data on equities and indices
  - SDK/Client: `yfinance` (v0.2.65)
  - Integration: `trading_script.py` lines 215-235 (`_yahoo_download()`)
  - Features: Configurable User-Agent, session management, progress/threading disabled for reliability
  - Behavior: Returns empty DataFrame on failure, triggering fallback chain

- **Stooq** - Secondary market data source for fallback coverage
  - SDK/Client: `pandas-datareader.data` (optional; gracefully skipped if unavailable)
  - Integration Points:
    - Primary fallback: `trading_script.py` lines 275-297 (`_stooq_download()` via pandas-datareader)
    - Direct CSV fallback: `trading_script.py` lines 237-273 (`_stooq_csv_download()`)
  - Endpoint: `https://stooq.com/q/d/l/?s={symbol}&i=d` (daily data)
  - Symbol Mapping: Stooq uses different naming conventions (e.g., `^GSPC` → `^spx`, equities suffixed with `.us`)
  - Blocklist: `^RUT` (Russell 2000) not available on Stooq; fallback uses proxy (`IWM`)

**LLM & Trading Intelligence:**
- **OpenAI (ChatGPT-4)** - AI-powered trading decision engine
  - SDK/Client: `openai` (Python SDK; conditionally imported in `simple_automation.py` line 26)
  - Integration: `simple_automation.py` lines 83-103 (`call_openai_api()`)
  - API Endpoint: OpenAI Chat Completions API (`client.chat.completions.create()`)
  - Authentication: API key required (see Environment Configuration below)
  - Model: `gpt-4` (configurable in function parameter)
  - Temperature: 0.3 (low variability for consistent decisions)
  - Max tokens: 1500
  - Purpose: Analyzes portfolio state and generates JSON-formatted trading recommendations
  - Request Format: System prompt + user portfolio data → JSON response with trades array

## Data Storage

**Databases:**
- Not applicable - No database integration detected

**File Storage:**
- **Local Filesystem** - Only storage mechanism
  - Portfolio state: `chatgpt_portfolio_update.csv`
  - Trade history: `chatgpt_trade_log.csv`
  - Location: Configurable via `set_data_dir()` in `trading_script.py` (defaults to script directory)
  - Format: CSV with Pandas serialization/deserialization
  - Columns (Portfolio): ticker, shares, stop_loss, buy_price, cost_basis
  - Columns (Trade Log): Date, Ticker, Shares Bought, Buy Price, Cost Basis, PnL, Reason

**Caching:**
- None detected - No explicit caching layer

## Authentication & Identity

**Auth Provider:**
- OpenAI API
  - Method: Bearer token via `openai.OpenAI(api_key=...)` constructor
  - Configuration: Via environment variable `OPENAI_API_KEY` or `--api-key` CLI argument
  - Usage: `simple_automation.py` line 88 (`client = openai.OpenAI(api_key=api_key)`)

**API Key Requirements:**
- Optional if using only core portfolio management (yfinance/Stooq)
- Required only for `simple_automation.py` LLM features

## Monitoring & Observability

**Error Tracking:**
- Not detected - No external error tracking service

**Logs:**
- **Python logging module** - Built-in logging
  - Logger: `logging.getLogger(__name__)` in `trading_script.py` line 77
  - Output: Console/stdout by default
  - Use cases:
    - Warning-level logging for malformed `tickers.json` (line 92)
    - Warning-level logging for JSON read failures (line 95)
- **CSV Logs** - Trade execution logs written to `chatgpt_trade_log.csv`
  - Fields: Date, Ticker, Shares Bought, Buy Price, Cost Basis, PnL, Reason
  - Used for audit trail and transparency

**Performance Metrics:**
- Calculated and stored in CSV format
  - CAPM analysis, Sharpe ratio, Sortino ratio, drawdown metrics
  - Visualized via `matplotlib` charts

## CI/CD & Deployment

**Hosting:**
- Not applicable - CLI/batch processing tool
- Intended deployment: Manual scheduling or external orchestration (cron, GitHub Actions, Task Scheduler)

**CI Pipeline:**
- Not detected - No CI/CD configuration found

## Environment Configuration

**Required Environment Variables:**
- `OPENAI_API_KEY` - OpenAI API key (only for LLM features in `simple_automation.py`)
  - Example: `OPENAI_API_KEY=sk-proj-...`

**Optional Environment Variables:**
- `ASOF_DATE` - Override "today" date for backtesting (format: `YYYY-MM-DD`)
  - Example: `ASOF_DATE=2024-06-15`

**Secrets Location:**
- Not committed to repository (`.gitignore` prevents accidental commits)
- Should be managed via:
  - `.env` file (user-local, not committed)
  - Shell environment variables
  - Secret management system (for production automation)

## Webhooks & Callbacks

**Incoming:**
- None detected

**Outgoing:**
- None detected

## Data Source Fallback Chain

The portfolio system implements a robust multi-stage fallback for market data retrieval (in `trading_script.py` `download_price_data()` lines 323-368):

1. **Yahoo Finance** (primary) via `yfinance`
2. **Stooq via pandas-datareader** (secondary)
3. **Stooq direct CSV** (tertiary)
4. **Index proxies** (quaternary)
   - `^GSPC` (S&P 500) → `SPY` via Yahoo
   - `^RUT` (Russell 2000) → `IWM` via Yahoo
   - Indices not available on Stooq fall back to proxy tickers

Each stage returns empty DataFrame on failure, allowing the next stage to execute. Returns normalized OHLCV with columns: [Open, High, Low, Close, Adj Close, Volume]

---

*Integration audit: 2026-03-19*
