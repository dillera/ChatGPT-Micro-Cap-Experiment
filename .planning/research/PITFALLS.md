# Domain Pitfalls

**Domain:** Autonomous micro-cap trading bot (tastytrade + multi-LLM + real money)
**Researched:** 2026-03-19
**Confidence:** HIGH on tastytrade-specific items (verified via official docs); MEDIUM on LLM behavior (multiple secondary sources); HIGH on regulatory/PDT (official tastytrade support)

---

## Critical Pitfalls

Mistakes that cause rewrites, account blowups, or regulatory problems.

---

### Pitfall 1: tastytrade Does Not Support OTC or Penny Stocks

**What goes wrong:** The project targets "micro-cap" stocks. Many micro-caps trade OTC or on pink sheets. tastytrade explicitly prohibits OTC, penny stock, and foreign-exchange-listed securities. Any automated screener that surfaces OTC-traded micro-caps will produce tickers that result in order rejections — silently or with a 422 error.

**Why it happens:** "Micro-cap" is a market-cap category, not an exchange category. Many micro-caps under $300M market cap are listed on major exchanges (NYSE/NASDAQ/NYSE American), but a significant subset trade OTC. The distinction is invisible unless you check exchange listing at screen time.

**Consequences:** Order rejected at execution time. If the bot doesn't handle rejections correctly, it may log a "success," leave the position untracked, or loop retry endlessly.

**Prevention:**
- Filter candidates at screening time: only include tickers with `exchange` in `['NYSE', 'NASDAQ', 'NYSE American', 'BATS']`.
- Add a pre-order validation step: verify the symbol resolves in tastytrade's instrument API before submitting.
- Never assume a ticker returned by Yahoo Finance or a screener API is exchange-listed.

**Detection:** Pre-execution dry run returns 422 or "symbol not found." Add an explicit exchange-listing check in the screening pipeline.

**Phase:** Address in Phase 1 (screening/instrument validation) and Phase 2 (order execution layer).

---

### Pitfall 2: Session Token Authentication Is Deprecated — OAuth2 Required

**What goes wrong:** Building authentication using the old session-token (username/password) flow against tastytrade's API. Session-token authentication was discontinued December 1, 2025. Any code using it will fail to authenticate.

**Why it happens:** Most existing tutorials, blog posts, and older community code (pre-2025) use the username/password session-token approach. It's simpler to implement. Training data for LLMs (including the ones helping build this system) may suggest this approach.

**Consequences:** Bot cannot log in at all. Complete failure of autonomous execution.

**Prevention:**
- Use OAuth2 exclusively. Set up refresh tokens once — they never expire, so re-authentication is not required on every run.
- Use the official `tastytrade` PyPI SDK (`tastyware/tastytrade`) version 12.0.0 or later, which handles token refresh automatically on every request.
- The OAuth access token expires in 15 minutes; the SDK refreshes it automatically — but only if you're on 12.x+. Pin the dependency.

**Detection:** HTTP 401 on any API call. Check that your code references OAuth2 endpoints, not the deprecated `/sessions` endpoint with credentials in the body.

**Phase:** Address in Phase 1 (infrastructure/auth setup) before any other API work.

---

### Pitfall 3: Pattern Day Trader (PDT) Rule Will Lock the Account

**What goes wrong:** An account under $25,000 that executes more than 3 day trades (open and close same security same day) in a rolling 5-business-day window gets flagged as a Pattern Day Trader (PDT). Once flagged, the account is restricted from placing new orders until equity reaches $25,000 or a one-time reset is used.

**Why it happens:** With aggressive position sizing on biotech catalysts, the bot may buy a position and then trigger a stop-loss the same day, counting as a day trade. This is not obvious during development when you're not tracking the rolling 5-day count.

**Consequences:** Account locked from new orders. The bot continues running (making LLM decisions, logging) but all orders are rejected. This can persist for days. The one-time reset is consumed immediately rather than saved for a real emergency.

**Prevention:**
- Track day trades per position, per day in the bot's state.
- Enforce a hard rule: no more than 2 day trades in any 5-business-day window (leave a 1-trade buffer).
- Design stop-losses to use GTC (good-till-cancelled) limit sell orders placed the next trading day, not same-day market orders.
- Consider a cash account instead of margin: cash accounts have different rules (settlement T+1 for equities, no margin, but no PDT restriction either). Verify tastytrade cash account rules before relying on this.

**Detection:** tastytrade API returns account flag indicating PDT status. Monitor `day-trade-count` in account balance endpoint. Alert immediately when count reaches 2.

**Phase:** Address in Phase 2 (order execution) and Phase 3 (circuit breakers/risk management).

---

### Pitfall 4: LLM Hallucination of Ticker Symbols, Financial Data, and Catalysts

**What goes wrong:** GPT-4 or Claude fabricates a ticker symbol, misidentifies an FDA decision date, invents a clinical trial result, or returns a real ticker for the wrong company. The bot acts on this with real money.

**Why it happens:** LLMs are trained on text; they don't have live market data. When asked to reason about specific companies, they interpolate from training data which may be months stale or simply wrong. Both models are known to hallucinate stock split ratios, earnings dates, and drug approval timelines even when reading provided context. Claude has documented biases toward affirming user framing and consensus views.

**Consequences:** Position opened in a wrong or irrelevant security. Fictitious catalyst never materializes. Stop-loss hits on a stock with no actual thesis.

**Prevention:**
- Never let LLMs provide raw ticker symbols as input to orders. The bot must validate every symbol against a verified screening universe before passing to execution.
- Supply LLMs with structured, pre-verified data (current price, market cap from a live data source) rather than asking them to recall it.
- Require the LLM response to include a confidence score and a stated catalyst with a date. If the date is in the past or the catalyst is not verifiable against a known source (FDA calendar, company IR), block the trade.
- Use two-model consensus (GPT-4 + Claude) as a hallucination check: if they disagree significantly on the thesis or ticker, treat it as a hallucination signal, not a trading signal.

**Detection:** Post-LLM validation step: look up the ticker via Yahoo Finance/tastytrade instruments API. If it doesn't exist or the company name doesn't match, reject the decision before execution.

**Phase:** Address in Phase 2 (LLM decision layer) with a dedicated validation/sanitization step.

---

### Pitfall 5: Market Orders on Illiquid Micro-Caps Cause Catastrophic Slippage

**What goes wrong:** A market order is placed on a micro-cap biotech with a wide bid-ask spread. The order executes at a price significantly worse than the mid-price at time of decision. For a $500 account, a $0.20 spread on a $2 stock is a 10% immediate loss before the trade has any chance to work.

**Why it happens:** LLMs (and developers) think in terms of "current price" but market orders execute against the order book, not the mid-price. Micro-cap biotechs can have spreads of 5-20% of price during normal hours, and dramatically worse at open or around catalyst events.

**Consequences:** Systematic erosion of capital on entry and exit, separate from position P&L. On a $500 account with aggressive position sizing, a single round-trip with 5% slippage on a 50% position is a $12.50 loss before the trade idea has any validity.

**Prevention:**
- Use limit orders exclusively for entries and exits. Never use market orders.
- Place limit orders at or slightly inside the spread mid-price, not at the ask.
- Implement a maximum allowed spread check: if `(ask - bid) / mid > threshold (e.g. 3%)`, do not enter the position that day.
- For stop-losses, use stop-limit orders (not stop-market), accepting the risk of a missed fill rather than guaranteed bad execution.

**Detection:** Log the bid/ask spread at time of order placement. Alert if fill price deviates more than 1% from decision-time mid-price.

**Phase:** Address in Phase 2 (order execution layer). Spread check belongs in screening filter.

---

### Pitfall 6: Duplicate Orders on Bot Restart or Cron Overlap

**What goes wrong:** The cron job fires, the bot starts executing, and partway through the run the process crashes or is killed. On restart (or if the next day's cron fires before the previous run completes), the bot re-evaluates the same positions and places duplicate orders for securities it already holds or has already traded.

**Why it happens:** No persistent state tracking of "orders placed this session." Without idempotency, every bot run is stateless. The bot sees a BUY signal, doesn't know it already placed that order, and places it again.

**Consequences:** Position doubled unintentionally. Account depleted beyond intended exposure. If a stop-loss was already executed, a duplicate buy re-enters the same losing position.

**Prevention:**
- Before placing any order, call the tastytrade API to fetch current live positions and open orders. Treat this as the single source of truth, not local state.
- Implement a trade log file that records order IDs. On startup, read this file and skip any decisions already executed today.
- Use tastytrade's `dry_run=True` first, compare buying power effect against current buying power, then place.
- Ensure cron job uses a lockfile: if the previous run is still active, the new run exits immediately.

**Detection:** Check for duplicate order IDs in the trade log. Alert if the bot attempts to buy a security already in positions.

**Phase:** Address in Phase 2 (execution layer) and Phase 3 (scheduling/operations).

---

### Pitfall 7: Trading Halt Leaves Position Unexitable

**What goes wrong:** A micro-cap biotech stock is halted by the exchange during an FDA decision, SEC inquiry, or pending news release. The bot's stop-loss order cannot execute because the stock cannot be traded. The halt can last minutes, hours, or an entire trading day.

**Why it happens:** Biotech stocks are specifically prone to regulatory trading halts around binary catalyst events (PDUFA dates, advisory committee votes, Phase 3 results). The SEC can also suspend trading in any stock for up to 10 days. These events are unannounced and cannot be predicted.

**Consequences:** A position that should have been stopped out at -15% instead exits at -60% or worse when trading resumes (gap down after bad FDA news). For a concentrated position on a small account, this is account-destroying.

**Prevention:**
- Size positions with the assumption that in the worst case, the full position goes to zero (not just the stop-loss amount). Never risk more than the account can absorb on a single halt event.
- Monitor for halt status before the daily run using the market status endpoints in the tastytrade API.
- Consider avoiding positions directly on PDUFA dates (the day of the FDA decision) — these are the highest halt-risk days.
- Design the circuit breaker to check for halt status and escalate to human notification rather than waiting for a fill that will never come.

**Detection:** tastytrade API will return order status of "pending" indefinitely during a halt. Implement a timeout: if an order has been open for more than X minutes without a fill, alert and pause further execution.

**Phase:** Address in Phase 3 (circuit breakers and risk management).

---

## Moderate Pitfalls

---

### Pitfall 8: Timezone and Market Hours Mishandling

**What goes wrong:** The cron job runs at the right UTC time on the server but fires outside market hours due to DST transitions, holidays, or server timezone misconfiguration. The bot attempts to place orders that are either rejected or queued as after-hours limit orders (filling at unexpected prices next morning).

**Why it happens:** US equity markets use Eastern Time, which shifts with DST (second Sunday in March, first Sunday in November). A cron expression that correctly fires at 9:45 AM ET in winter fires at 8:45 AM ET in summer after a DST-unaware schedule.

**Prevention:**
- Always schedule cron in ET-aware form, or use a scheduler library that handles DST explicitly.
- At the start of every bot run, call the tastytrade market status endpoint for `'Equity'` exchange and verify `is_open: true` before proceeding with any decisions or orders.
- Maintain a US federal holiday calendar and skip runs on holidays (tastytrade's market status endpoint will return closed, but checking it explicitly provides the guard).

**Detection:** Log the market status check result on every run. If the bot executes decisions while the market status is "closed," something is wrong with scheduling.

**Phase:** Address in Phase 3 (scheduling and operations).

---

### Pitfall 9: LLM Consensus Masking — Models Agree for the Wrong Reason

**What goes wrong:** GPT-4 and Claude are given the same data and both recommend BUY on the same ticker. The project treats this as a high-confidence signal. But both models are reasoning from the same publicly available information and have the same training biases. Consensus between two models trained on similar data is not independence.

**Why it happens:** Claude is documented to bias toward consensus views and affirm user framing. GPT-4 has similar tendencies toward safe, mainstream interpretations. When both models receive the same inputs, agreement is structurally likely even when the underlying thesis is weak.

**Consequences:** Overconfidence in model agreement leading to larger position sizing than the actual signal quality warrants.

**Prevention:**
- Treat model consensus as a necessary condition, not a sufficient one. Require at least one model to articulate a specific contrarian risk that the other model must explicitly rebut.
- Use different prompting strategies for each model (one plays bear, one plays bull) to force surface disagreement.
- Weight consensus by confidence margin: 70%/65% agreement is different from 95%/90%.

**Detection:** If the models agree on every single trade recommendation across multiple days, that is a signal of prompt design failure, not signal quality.

**Phase:** Address in Phase 2 (LLM decision layer prompt engineering).

---

### Pitfall 10: JSON Parsing Failure Silently Skips Trades or Executes Wrong Decisions

**What goes wrong:** The LLM returns a response that fails JSON parsing (markdown code fences, truncated output, schema drift, wrong field names). The bot either crashes, defaults to a safe no-op, or — most dangerously — executes a partially-parsed decision.

**Why it happens:** LLMs do not guarantee schema compliance even with JSON mode enabled. Token limits can truncate responses. Models occasionally wrap JSON in markdown. Schema fields may be renamed between model versions. Both OpenAI and Anthropic have documented cases where structured output fails with parsing errors under edge conditions.

**Prevention:**
- Use Pydantic models to validate every LLM response. Treat a Pydantic validation error the same as an LLM failure: abort the run, alert, do not trade.
- Implement a two-step LLM call for complex decisions: Step 1 (free-form reasoning), Step 2 (structured extraction). This preserves reasoning quality while improving schema compliance.
- Set explicit max_tokens high enough that the response cannot be truncated mid-JSON.
- Log every raw LLM response to an audit file before parsing. This enables debugging post-incident.

**Detection:** Pydantic validation errors in logs. Mismatched field names. `finish_reason: length` in API responses.

**Phase:** Address in Phase 2 (LLM integration layer).

---

### Pitfall 11: Sandbox Environment Lags — Symbols That Work Live Fail in Cert

**What goes wrong:** Testing in tastytrade's sandbox (cert environment, `api.cert.tastyworks.com`) shows valid symbols returning 422 errors. Developer concludes the code is broken. In reality, the sandbox instrumentation lags behind the live environment and some valid symbols are simply absent from the cert system.

**Why it happens:** tastytrade explicitly documents this: "The Sandbox Environment instrumentation sometimes lags behind the live trading environment, which occasionally causes valid symbols to fail with 422 error codes."

**Consequences:** Wasted debugging time. Worse: developer works around the issue in a way that masks real order validation bugs in the production path.

**Prevention:**
- Use cert environment for authentication and session flow testing only.
- For symbol validation logic, test against a small set of known-stable, highly-liquid tickers (AAPL, SPY) that are guaranteed to be present in cert.
- When a cert 422 appears on a real candidate ticker, email `api.support@tastytrade.com` per tastytrade's own documentation, or simply test that specific validation path in a paper trading session.
- Never change production code to work around cert-specific symbol gaps.

**Detection:** The 422 error message from cert will differ from a genuine order rejection. Log the full response body on all 4xx errors.

**Phase:** Address in Phase 1 (development environment setup).

---

### Pitfall 12: FINRA Trading Activity Fee on Every Equity Sale

**What goes wrong:** While tastytrade offers $0 commission on equity trades, FINRA Trading Activity Fees (TAF) apply to every sell. On a sub-$1K account with aggressive position sizing and frequent turnover, these fees erode returns.

**Why it happens:** TAF is approximately $0.000166 per share sold, with a maximum of $8.30. On a micro-cap position of 1,000 shares at $0.50, this is $0.17. Small, but nonzero — and cumulative across many trades.

**Consequences:** Minor P&L erosion. More importantly: the accounting must reconcile fees correctly or the bot's P&L tracking will diverge from reality.

**Prevention:**
- Include a fee estimate in the bot's P&L calculations. Use tastytrade's `BuyingPowerEffect` from a dry run to get the actual fee amount.
- Factor fees into position sizing: the minimum meaningful trade size must cover entry fees and still leave room for the thesis to play out.

**Phase:** Address in Phase 2 (order execution) and Phase 4 (P&L tracking).

---

## Minor Pitfalls

---

### Pitfall 13: Account Streamer Does Not Replay Missed Notifications

**What goes wrong:** The WebSocket connection to tastytrade's account streamer drops mid-day (network hiccup, server restart). Order fills that occurred during the disconnection are not replayed when reconnection happens.

**Prevention:** Never rely solely on the streamer for position state. After reconnection, always call the REST API `GET /accounts/{id}/positions` and `GET /accounts/{id}/orders?status=Filled` to reconcile state before proceeding.

**Phase:** Address in Phase 2 (execution) and Phase 3 (reliability layer).

---

### Pitfall 14: Wash Sale Rule on Automated Rapid Cycling

**What goes wrong:** The bot sells a position at a loss, then buys the same security within 30 days (before or after). The IRS wash sale rule disallows the tax loss deduction. With frequent cycling of the same biotech tickers, this can happen repeatedly without explicit tracking.

**Prevention:** Track last-sale dates per ticker. Block re-entry into a recently-sold-at-loss position for 31 days, or explicitly account for wash sales in tax reporting. (Note: wash sale rules apply to securities; current IRS guidance does not apply them to crypto, but micro-cap equities are definitively subject.)

**Phase:** Address in Phase 4 (accounting/reporting).

---

### Pitfall 15: Rate Limiting Without Backoff Causes Cascading Failures

**What goes wrong:** The bot makes rapid sequential API calls (fetch positions, fetch balances, fetch quotes, place orders) without respecting rate limits. tastytrade's API begins rejecting requests. The bot retries without backoff, making the problem worse, until the session is potentially banned.

**Prevention:** Implement 2 requests/second rate limiting (documented in community implementations). Use exponential backoff on 429 responses. Serialize API calls that don't need to be parallel.

**Phase:** Address in Phase 1 (API client layer).

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Phase 1: Auth + API setup | Deprecated session-token auth | Use OAuth2 with tastytrade SDK 12.x+ |
| Phase 1: Instrument validation | OTC tickers rejected | Exchange-listing filter before any order path |
| Phase 1: Dev environment | Cert sandbox symbol gaps | Test auth/flow with liquid stocks only |
| Phase 2: Order execution | Market order slippage | Limit orders exclusively; spread check gate |
| Phase 2: Order execution | Duplicate orders on restart | Live position check at run start; lockfile |
| Phase 2: LLM integration | JSON parse failures block/corrupt execution | Pydantic validation; abort on schema error |
| Phase 2: LLM integration | Hallucinated tickers/catalysts | Symbol validation against live data pre-order |
| Phase 2: LLM integration | False consensus between models | Adversarial prompting; confidence margin scoring |
| Phase 3: Risk/circuit breakers | PDT rule locks account | Day-trade counter; GTC stops not same-day |
| Phase 3: Risk/circuit breakers | Trading halt unexitable position | Halt status check; timeout on pending orders |
| Phase 3: Scheduling | Timezone/DST miscalculation | Market status API check at run start |
| Phase 3: Reliability | Streamer disconnection loses fill events | REST reconciliation on reconnect |
| Phase 4: Accounting | Wash sale accumulation | 31-day re-entry block on loss positions |
| Phase 4: Accounting | TAF fee divergence in P&L | Use dry_run BuyingPowerEffect for fee estimates |

---

## Sources

- tastytrade OAuth deprecation notice: [GitHub Issue #269 tastyware/tastytrade](https://github.com/tastyware/tastytrade/issues/269)
- tastytrade Sessions docs: [Sessions — tastytrade 12.2.0](https://tastyworks-api.readthedocs.io/en/latest/sessions.html)
- tastytrade Developer Guide: [Sessions — developer.tastytrade.com](https://developer.tastytrade.com/api-guides/sessions/)
- tastytrade Sandbox docs: [Sandbox Environment — developer.tastytrade.com](https://developer.tastytrade.com/sandbox/)
- tastytrade PDT Rule: [Pattern Day Trading and Equity Maintenance Calls — tastytrade Support](https://support.tastytrade.com/support/s/solutions/articles/43000435180)
- tastytrade OTC/Penny Stock restriction: [OTC And Penny Stocks at tastytrade — Support](https://support.tastytrade.com/support/s/solutions/articles/43000478158)
- tastytrade Commissions (Jan 2026): [Commissions and Fees](https://assets.contentstack.io/v3/assets/blt7dc2e3d4a7071563/blt2b752fef372188fe/commissions-and-fees)
- tastytrade Account Streamer: [Account Streamer — tastytrade docs](https://tastyworks-api.readthedocs.io/en/latest/account-streamer.html)
- tastytrade Orders: [Orders — tastytrade 12.1.0](https://tastyworks-api.readthedocs.io/en/latest/orders.html)
- LLM hallucination in finance: [LLM Hallucinations — BizTech Magazine](https://biztechmagazine.com/article/2025/08/llm-hallucinations-what-are-implications-financial-institutions)
- LLM structured output failures: [LLM Structured Output in 2026 — DEV Community](https://dev.to/pockit_tools/llm-structured-output-in-2026-stop-parsing-json-with-regex-and-do-it-right-34pk)
- Claude bias in finance: [AI Bias by Design — CFA Institute](https://blogs.cfainstitute.org/investor/2025/05/14/ai-bias-by-design-what-the-claude-prompt-leak-reveals-for-investment-professionals/)
- Micro-cap liquidity risks: [SEC Investor Bulletin: Microcap Stock Basics (Risk)](https://sec.gov/oiea/investor-alerts-bulletins/ib_microcap_3.html)
- Biotech trading halt mechanics: [Trading Halts — FINRA](https://www.finra.org/investors/investing/investment-products/stocks/trading-halts-delays-suspensions)
- Duplicate order prevention: [Idempotency Keys — Token Metrics](https://www.tokenmetrics.com/blog/idempotency-keys-order-placement)
- AI trading bot risks: [Why Most Trading Bots Lose Money — ForTraders](https://www.fortraders.com/blog/trading-bots-lose-money)
- CFTC AI trading advisory: [Customer Advisory: AI Won't Turn Trading Bots into Money Machines — CFTC](https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/AITradingBots.html)
