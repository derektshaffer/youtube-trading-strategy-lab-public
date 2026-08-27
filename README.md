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
- automatic title/author detection when the source clearly identifies them
- AI Autopilot that converts qualitative lessons into clearly labeled research assumptions
- source-rule protection so AI assumptions never overwrite thresholds explicitly stated by the author
- research-readiness scoring before a strategy enters deterministic backtesting
- one canonical strategy representation across books and YouTube sources
- historical Strategy Lab optimization, untouched holdout testing, walk-forward validation, catalyst intelligence, universe research, market discovery, stock analysis, and Live/Paper integration
- a separate intelligence-library backup at `trading-intelligence-lab/intelligence_library.json`
- read/import access to strategies already saved by the YouTube Trading Lab

Normal book workflow:

1. Upload a PDF/TXT/Markdown source in **Knowledge Sources**.
2. Leave title, author, and research focus blank unless you want to override or narrow the AI.
3. Keep **AI Autopilot** and **Continue automatically into historical opportunity discovery + validation** enabled.
4. Click **Analyze source and extract strategies** once.
5. The AI extracts strategies, preserves source evidence, and creates clearly labeled research assumptions where needed.
6. The Historical Research Autopilot builds a broad Alpaca stock universe automatically. It samples active U.S. equities and always includes current movers/most-active names.
7. Daily history is used only to identify stocks that previously exhibited strategy-relevant conditions. Future trade P/L is not used for candidate selection.
8. The strongest research finalists receive intraday optimization, untouched holdout testing, cost stress testing, rolling walk-forward checks, and frozen-rule cross-stock testing.
9. Results are saved automatically into the strategy and validation libraries. Strategies that miss any autonomous validation gate remain research-only; qualifying strategies receive a frozen validated rule set.
10. Open **AI Research Autopilot** any time to inspect the automatic leaderboard or rerun the current library without choosing tickers or optimizer settings.

Important research limitation: the broad universe is sampled from equities active today. This is much broader than testing only current movers, but delisted historical securities are absent, so survivorship bias is still disclosed in every autonomous run.

To try it as a separate Streamlit app, deploy the same repository again and set the main file path to
`trading_intelligence_app.py`. It reuses the existing `GEMINI_API_KEY` and GitHub backup secrets.
An optional `GEMINI_PAID_API_KEY` may point to a different Google project for quota fallback; never reuse the same key in both fields.
