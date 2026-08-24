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
