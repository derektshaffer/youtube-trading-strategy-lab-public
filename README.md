# YouTube Trading Strategy Lab

A standalone Streamlit research app that analyzes public YouTube trading videos,
extracts timestamped trading hypotheses, backtests their measurable rules with
Alpaca historical candles, scans live candidates, and tracks editable practice
positions.

This is a **separate application**. Do not replace the existing stock scanner or
single-stock analyzer. Deploy this project from a new GitHub repository and it
will have its own Streamlit URL, secrets, and saved strategy library.

## Upload these three files to a new GitHub repository

1. `youtube_strategy_app.py`
2. `youtube_strategy_engine.py`
3. `requirements.txt`

The `README.md`, `secrets.example.toml`, and test file are optional.

Suggested repository name: `youtube-trading-strategy-lab`.

## Deploy on Streamlit Community Cloud

1. Open Streamlit Community Cloud.
2. Select **Create app** or **New app**.
3. Select your new GitHub repository, not your existing `stock-scanner` app.
4. Set the main file path to `youtube_strategy_app.py`.
5. Open the app's **Advanced settings** or **Settings → Secrets**.
6. Paste the following, replacing the example values:

```toml
ALPACA_API_KEY = "your-existing-alpaca-api-key"
ALPACA_SECRET_KEY = "your-existing-alpaca-secret-key"
GEMINI_API_KEY = "your-new-google-gemini-api-key"

ALPACA_LIVE_FEED = "iex"
ALPACA_HISTORICAL_FEED = "sip"
GEMINI_MODEL = "gemini-3.7-flash"
```

7. Select **Deploy**.

You can reuse the same Alpaca API key and secret that your other app uses. The
Gemini API key is new. Create one at https://aistudio.google.com/apikey.

Never put real API keys in a GitHub file. Add them only to Streamlit Secrets or
local environment variables.

## Run locally on a Mac

```bash
python3 -m pip install -r requirements.txt
export ALPACA_API_KEY="your-existing-alpaca-api-key"
export ALPACA_SECRET_KEY="your-existing-alpaca-secret-key"
export GEMINI_API_KEY="your-google-gemini-api-key"
streamlit run youtube_strategy_app.py
```

## How to use the app

1. Open **Analyze videos** and paste public YouTube video URLs, one per line.
2. Optionally tell the AI what to prioritize: VWAP, liquidity, momentum,
   stop-loss placement, low-float stocks, or avoiding bad entries.
3. Open **Strategy library** to inspect the AI's extracted rules and timestamps.
4. Edit any measurable thresholds that the video did not specify clearly.
5. Open **Backtesting** and test the strategy against historical stock candles.
6. Compare the full-period results with the separate out-of-sample results.
7. Return to **Strategy library** and approve strategies you want to scan.
8. Open **Live scanner** to search your own watchlist, top gainers, or active
   stocks for matches against approved strategies.
9. Record, edit, close, or delete practice positions in **Paper journal**.
10. Download JSON backups from **Setup & backups** whenever you want to preserve
    important analyses or practice-trade history.

## Honest limitations

- The app does not place real or simulated brokerage orders.
- Video extraction is a research tool. A creator showing winning examples does
  not establish that the method makes money.
- Small chart text, quickly changing visuals, and subjective tape-reading rules
  can be difficult for AI to interpret correctly.
- The initial backtester supports long trades, regular U.S. trading hours,
  next-bar entries, conservative same-bar stop handling, spreads, slippage,
  fees, position sizing, maximum drawdown, and holdout testing.
- Historical point-in-time catalysts, exact historical bid/ask quotes, halts,
  float, and proprietary indicators are not reconstructed. The app explicitly
  discloses missing or subjective strategy conditions.
- Alpaca Basic supplies real-time quotes from IEX only. Historical SIP requests
  are delayed beyond the most recent 15 minutes. Paid SIP access can be enabled
  by changing `ALPACA_LIVE_FEED` to `sip`.
- Streamlit Cloud's local filesystem can reset when an app restarts or is
  redeployed. Use the built-in backup export/import feature to retain records.

## Optional test command

```bash
python3 -m unittest test_youtube_strategy_engine.py -v
```

## Trading Intelligence Lab (new platform foundation)

A second Streamlit entrypoint now lives in this repository:

```
trading_intelligence_app.py
```

It is intentionally separate from the current YouTube Trading Lab home screen. The current build includes:

- PDF/TXT/Markdown knowledge-source ingestion
- resilient chunked Gemini extraction for books and research documents, with retry/fallback/resume support
- model-role routing for books: Gemini 3.6 Flash handles normal bulk extraction, Gemini 3.1 Pro Preview is reserved for genuinely difficult/ambiguous sections, and Gemini 3.5/2.5 remain reliability fallbacks
- automatic title/author detection when the source clearly identifies them
- AI Autopilot that converts qualitative lessons into clearly labeled research assumptions
- source-rule protection so AI assumptions never overwrite thresholds explicitly stated by the author
- research-readiness scoring before a strategy enters deterministic backtesting
- one canonical strategy representation across books and YouTube sources
- Strategy DNA fingerprints that decompose setups into universe, catalyst, momentum, structure, context, risk, exit, execution, and market-regime components
- cross-book concept mapping that counts independent-source agreement separately from historical validation
- automatic strategy-family clustering and research-only cross-source candidate blueprints, with explicit rule conflicts surfaced instead of silently averaged
- direct synthesized-candidate research: exact source-supported threshold disagreements are injected into the optimizer before generic nearby values, then candidates can run through historical discovery, optimization, walk-forward, untouched holdout, cost stress, and cross-stock validation from the Strategy DNA screen
- synthetic candidates are excluded from independent-source counts so the system can never increase its own corroboration score by citing its own generated research
- historical Strategy Lab optimization, untouched holdout testing, walk-forward validation, catalyst intelligence, universe research, market discovery, stock analysis, and Live/Paper integration
- a separate intelligence-library backup at `trading-intelligence-lab/intelligence_library.json`
- read/import access to strategies already saved by the YouTube Trading Lab

Normal book workflow:

1. Upload a PDF/TXT/Markdown source in **Knowledge Sources**.
2. Leave title, author, and research focus blank unless you want to override or narrow the AI.
3. Keep **AI Autopilot** and **Continue automatically into historical opportunity discovery + validation** enabled.
4. Click **Analyze source and extract strategies** once.
5. Gemini 3.6 Flash performs the normal bulk book extraction. Only sections with low confidence, conflicting/ambiguous requirements, evidence gaps, or unusually difficult formalization are escalated to Gemini 3.1 Pro Preview for a specialist second pass. If Pro is unavailable, the successful Flash extraction is kept rather than discarded.
6. The AI extracts strategies, preserves source evidence, and creates clearly labeled research assumptions where needed.
7. The Strategy DNA layer converts each strategy into reusable components and compares those concepts across independent books/documents without treating author agreement as proof of an edge.
8. Cross-source strategy families and candidate blueprints are generated as research hypotheses; conflicting explicit thresholds remain visible instead of being averaged into invented rules.
9. Open **Strategy DNA → Candidate blueprints** to inspect a synthesized setup. **Save / refresh research candidate** stores it in the unified library. **Run full historical research pipeline** compiles the candidate and sends it directly through the existing research engine.
10. When sources disagree on an explicit threshold (for example RVOL 5× versus 8×), each exact source-supported value is tested early as an optimizer seed before generic nearby values are explored.
11. The Historical Research Autopilot builds a point-in-time-capable Alpaca universe automatically from the exchange-listed U.S. equity master catalog, including both active and inactive/delisted symbols. A fixed share of each broad sample is reserved for inactive names while current movers/most-active stocks are retained for present-day coverage.
12. About five years of daily history is used only to identify stocks and dates that previously exhibited strategy-relevant conditions. Actual dated bar availability is used to infer when each symbol existed. Future trade P/L is not used for candidate selection.
13. Instead of forcing every strategy into the latest 60 days, the Lab selects bounded historical research windows around the strongest actual opportunity clusters for each finalist stock. Those windows can be years in the past and can belong to symbols that are inactive today.
14. The strongest research finalists receive intraday optimization, untouched holdout testing, cost stress testing, rolling walk-forward checks, and frozen-rule cross-stock testing inside those historical event windows.
15. Results are saved automatically into the strategy and validation libraries. Strategies that miss any autonomous validation gate remain research-only; qualifying strategies receive a frozen validated rule set.
16. Open **AI Research Autopilot** any time to inspect the automatic leaderboard, including current asset status, observed historical lifespan, and exact research window for each finalist.

Point-in-time limitation: historical membership is inferred from Alpaca's retained active + inactive asset master and actual dated bar history. This materially reduces survivorship bias, but extremely old symbols missing from Alpaca's retained catalog/history, ticker changes, mergers, and corporate actions can still create gaps or separate ticker identities.

To try it as a separate Streamlit app, deploy the same repository again and set the main file path to
`trading_intelligence_app.py`. It reuses the existing `GEMINI_API_KEY` and GitHub backup secrets.
An optional `GEMINI_PAID_API_KEY` may point to a different Google project for quota fallback; never reuse the same key in both fields.


## Continuous autonomous research worker

The Trading Intelligence Lab now includes a persistent research queue that can be processed outside Streamlit by `cloud_research_worker.py`.

Research roles are deliberately separated:

- `gemini-3.7-flash` is the default high-throughput grounded-web research model.
- `gemini-3.1-pro-preview` is the default specialist reasoning model for conflicting or high-value hypotheses.
- Gemini models can propose and critique hypotheses, but they never mark a strategy validated.
- Deterministic historical optimization, holdout, walk-forward, cost stress, stability, and cross-stock testing remain the promotion gate.

The scheduled workflow `.github/workflows/continuous-trading-research.yml` checks the durable queue every hour. Because it runs in GitHub Actions rather than the Streamlit session, research can continue when the browser or user's computer is off.

Required GitHub Actions repository secrets:

- `GEMINI_API_KEY`
- `GEMINI_PAID_API_KEY` (optional quota fallback)
- `GITHUB_BACKUP_REPOSITORY`
- `GITHUB_BACKUP_TOKEN`
- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`

Optional repository variables include `GEMINI_RESEARCH_BULK_MODEL`, `GEMINI_RESEARCH_SPECIALIST_MODEL`, `RESEARCH_JOBS_PER_RUN`, `RESEARCH_TOPICS_PER_CYCLE`, and validation batch/universe sizes.

The worker follows this bounded loop:

1. Seed a daily set of high-value research topics.
2. Flash performs Google-grounded research and saves source-quality metadata.
3. Each hypothesis is queued for Pro specialist review.
4. Pro can reject it, request targeted follow-up research, or send a machine-testable version to deterministic validation.
5. Promoted hypotheses are saved as **unvalidated research strategies** only.
6. The existing Autonomous Research pipeline tests them against historical data and records failures as well as successes.
7. Follow-up questions can create new research jobs, but the daily seeding and queue limits prevent an uncontrolled API loop.

The **AI Research Autopilot** page is the control center for queue status, latest grounded research, model routing, worker activity, and deterministic validation results.
