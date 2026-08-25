from pathlib import Path

engine_path = Path("youtube_strategy_engine.py")
engine = engine_path.read_text(encoding="utf-8")

api_anchor = '''def _json_request(
    url: str,
    headers: dict[str, str],
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: int = 45,
) -> Any:
'''
if api_anchor not in engine:
    raise SystemExit("Could not find _json_request")

# Insert the helper after the complete _json_request function, before schema constants.
schema_anchor = '''\n\nNULLABLE_NUMBER = {"type": ["number", "null"]}\n'''
helper = '''\n\ndef ask_chatgpt_help(
    api_key: str,
    question: str,
    *,
    model: str = "gpt-5.6-luna",
    glossary_context: str = "",
) -> str:
    """Ask OpenAI for a plain-language explanation inside the app's help tab."""
    key = str(api_key or "").strip()
    if not key:
        raise AppError("Add OPENAI_API_KEY to Streamlit Secrets to use Ask ChatGPT.")
    prompt = str(question or "").strip()
    if not prompt:
        raise AppError("Type a question for ChatGPT first.")
    if len(prompt) > 4000:
        raise AppError("Keep Help questions under 4,000 characters.")
    chosen_model = str(model or "gpt-5.6-luna").strip() or "gpt-5.6-luna"
    context = str(glossary_context or "").strip()
    instructions = (
        "You are the in-app Help assistant for YouTube Trading Strategy Lab, a research and paper-trading app. "
        "Explain trading, market-data, backtesting, optimization, and app terminology clearly and concretely. "
        "Prefer short plain-English explanations first, then a simple example when useful. "
        "Distinguish training, validation, and holdout data carefully. Do not imply that backtest results guarantee future returns. "
        "Do not claim access to live prices, the user's brokerage account, or unseen app state. "
        "If a question could affect real-money trading, explain the concept and risk rather than telling the user to buy or sell."
    )
    if context:
        instructions += " Relevant glossary context from the app: " + context[:12000]
    request_text = instructions + "\\n\\nUser question: " + prompt
    response = _json_request(
        "https://api.openai.com/v1/responses",
        {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
        payload={"model": chosen_model, "input": request_text},
        timeout=60,
    )
    if isinstance(response, dict):
        direct = response.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        parts: list[str] = []
        for item in response.get("output") or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    text = content["text"].strip()
                    if text:
                        parts.append(text)
        if parts:
            return "\\n\\n".join(parts)
    raise AppError("ChatGPT returned a response without readable text. Try the question again.")
'''
if "def ask_chatgpt_help(" not in engine:
    if schema_anchor not in engine:
        raise SystemExit("Could not find schema anchor")
    engine = engine.replace(schema_anchor, helper + schema_anchor, 1)
engine_path.write_text(engine, encoding="utf-8")

app_path = Path("youtube_strategy_app.py")
app = app_path.read_text(encoding="utf-8")

# Import helper.
import_anchor = '''    average_completed_daily_volume,\n'''
if "    ask_chatgpt_help,\n" not in app:
    if import_anchor not in app:
        raise SystemExit("Could not find engine import anchor")
    app = app.replace(import_anchor, import_anchor + "    ask_chatgpt_help,\n", 1)

# Add glossary and rendering helpers before credentials_ready.
helper_anchor = '''def credentials_ready() -> tuple[bool, bool]:\n'''
glossary_helpers = '''HELP_GLOSSARY: list[dict[str, str]] = [
    {"term": "Adaptive refinement", "category": "Optimizer", "meaning": "A second, finer search around promising optimizer settings instead of testing only a fixed coarse grid."},
    {"term": "Backtest", "category": "Backtesting", "meaning": "A simulation that applies strategy rules to historical price data to estimate how the rules would have behaved."},
    {"term": "Basis point (bps)", "category": "Execution", "meaning": "One hundredth of one percent. 100 bps = 1%. The app uses bps for spread and slippage assumptions."},
    {"term": "Breakout", "category": "Trading", "meaning": "Price moving above a defined prior high or resistance area, often with traders watching for volume confirmation."},
    {"term": "Candle / candlestick", "category": "Charts", "meaning": "A price bar summarizing open, high, low, and close for a set interval such as 1, 5, or 15 minutes."},
    {"term": "Catalyst", "category": "Trading", "meaning": "News or an event that can cause unusual interest and price movement, such as earnings, FDA news, contracts, or mergers."},
    {"term": "Drawdown", "category": "Risk", "meaning": "The decline from a previous equity peak to a later low. Maximum drawdown is the largest such decline in the test."},
    {"term": "Dollar volume", "category": "Liquidity", "meaning": "Share volume multiplied by price. It is a rough measure of how much money is trading in a stock."},
    {"term": "Entry", "category": "Trading", "meaning": "The price or condition at which a strategy opens a position."},
    {"term": "Expectancy", "category": "Performance", "meaning": "The average amount a strategy is expected to gain or lose per trade based on wins, losses, and their sizes."},
    {"term": "Full-window P/L", "category": "Backtesting", "meaning": "Total simulated profit or loss across the entire selected historical period."},
    {"term": "Higher-cost stress test", "category": "Backtesting", "meaning": "A repeat of the test using worse spread/slippage assumptions to see whether the apparent edge survives more expensive execution."},
    {"term": "Historical best fit", "category": "Optimizer", "meaning": "Settings that performed best on the same historical period used to choose them. Useful for research, but especially vulnerable to overfitting."},
    {"term": "Holdout data", "category": "Validation", "meaning": "A final untouched slice of historical data that is not used to tune or choose the winning strategy."},
    {"term": "Holdout P/L", "category": "Validation", "meaning": "Simulated profit or loss produced only in the final untouched holdout period."},
    {"term": "Holdout trade", "category": "Validation", "meaning": "A trade that occurs in the final untouched test period after a strategy has already been selected."},
    {"term": "IEX", "category": "Market data", "meaning": "A single U.S. exchange/data source. IEX data can be useful but is not the complete consolidated U.S. market feed."},
    {"term": "In-sample", "category": "Validation", "meaning": "Data that was available to the optimization or strategy-development process. Strong in-sample results alone do not prove the strategy generalizes."},
    {"term": "Liquidity", "category": "Liquidity", "meaning": "How easily shares can be bought or sold without moving price much. Higher liquidity usually means tighter spreads and less slippage."},
    {"term": "Long trade", "category": "Trading", "meaning": "Buying shares first because the strategy expects price to rise, then selling later to close the position."},
    {"term": "Maximum historical P/L", "category": "Optimizer", "meaning": "Optimizer mode that ranks settings by simulated profit across the selected history rather than by a separate validation period."},
    {"term": "Maximum position", "category": "Risk", "meaning": "The largest percentage of available account equity the simulation is allowed to put into one position."},
    {"term": "Opening range", "category": "Trading", "meaning": "The high/low range formed during the first specified minutes after the market opens."},
    {"term": "Optimizer", "category": "Optimizer", "meaning": "The part of the app that tests many strategy and execution settings and ranks the resulting historical simulations."},
    {"term": "Out-of-sample", "category": "Validation", "meaning": "Historical data that was not used to tune the strategy. Validation and holdout periods are forms of out-of-sample testing."},
    {"term": "Overfitting", "category": "Validation", "meaning": "When settings match quirks of the historical test period so closely that they look excellent in backtesting but do not generalize well."},
    {"term": "P/L", "category": "Performance", "meaning": "Profit and loss. Positive P/L means the simulation gained money; negative P/L means it lost money."},
    {"term": "Position size", "category": "Risk", "meaning": "How much capital or how many shares are allocated to a trade."},
    {"term": "Profit factor", "category": "Performance", "meaning": "Gross winning dollars divided by gross losing dollars. Above 1 means gross wins exceeded gross losses. With no losing trades, a finite value cannot be calculated."},
    {"term": "Pullback", "category": "Trading", "meaning": "A temporary move against the larger short-term direction, such as a stock rising and then briefly dipping before another possible push."},
    {"term": "R / risk unit", "category": "Risk", "meaning": "The amount initially risked on a trade. A 2R winner earns twice the initial planned risk."},
    {"term": "Relative volume (RVOL)", "category": "Liquidity", "meaning": "Current trading volume compared with what is normal for the stock at a similar point in the session."},
    {"term": "Resistance", "category": "Charts", "meaning": "A price area where selling has previously been strong enough to slow or stop advances."},
    {"term": "Return %", "category": "Performance", "meaning": "Profit or loss expressed as a percentage of starting capital in the simulation."},
    {"term": "Reward/risk", "category": "Risk", "meaning": "Planned reward relative to planned risk. A 2.0 reward/risk target aims for about $2 of reward for each $1 initially risked."},
    {"term": "Risk per trade", "category": "Risk", "meaning": "The maximum percentage of account equity the sizing model intends to lose if the planned stop is hit."},
    {"term": "Session", "category": "Market data", "meaning": "One regular U.S. trading day in the backtest, normally 9:30 a.m. to 4:00 p.m. Eastern."},
    {"term": "SIP", "category": "Market data", "meaning": "The consolidated U.S. market feed combining quotes and trades from multiple exchanges, giving broader coverage than a single-exchange feed such as IEX."},
    {"term": "Slippage", "category": "Execution", "meaning": "The difference between the price a strategy expects and the price it may actually receive when an order executes."},
    {"term": "Spread", "category": "Execution", "meaning": "The gap between the best available bid and ask. Wider spreads make entering and exiting more expensive."},
    {"term": "Stop loss", "category": "Risk", "meaning": "A planned exit level intended to limit loss when a trade moves against the strategy."},
    {"term": "Strategy library", "category": "App", "meaning": "The saved collection of strategies extracted from videos, master strategies, and stock-specific optimized versions."},
    {"term": "Support", "category": "Charts", "meaning": "A price area where buying has previously been strong enough to slow or stop declines."},
    {"term": "Timeframe", "category": "Charts", "meaning": "The duration represented by each price candle, such as 1 minute, 5 minutes, or 15 minutes."},
    {"term": "Trade count", "category": "Validation", "meaning": "The number of completed simulated trades. Very small samples can make performance metrics look much more impressive or terrible than they really are."},
    {"term": "Training data", "category": "Validation", "meaning": "The historical portion used to tune candidate strategy settings."},
    {"term": "Training trade", "category": "Validation", "meaning": "A simulated trade occurring in the historical training period used while tuning settings."},
    {"term": "Validation data", "category": "Validation", "meaning": "A separate historical period used to compare settings after they are tuned, without tuning directly on those results."},
    {"term": "Validation trade", "category": "Validation", "meaning": "A simulated trade occurring in the separate validation period. It helps show whether tuned settings still work on data they were not directly optimized against."},
    {"term": "Validated edge", "category": "Optimizer", "meaning": "Optimizer mode that tunes on training data, compares candidates on separate validation data, then tests the chosen winner on a final holdout."},
    {"term": "Volume surge", "category": "Liquidity", "meaning": "Current candle volume that is unusually high compared with recent candles."},
    {"term": "VWAP", "category": "Charts", "meaning": "Volume-Weighted Average Price: the session's average traded price weighted by volume. Traders often use it as an intraday reference for trend and location."},
    {"term": "VWAP reclaim", "category": "Trading", "meaning": "Price moves back above VWAP after trading below it, sometimes used as a momentum or trend-recovery signal."},
    {"term": "Win rate", "category": "Performance", "meaning": "The percentage of completed trades that were profitable. Win rate alone does not show how large wins and losses were."},
]


def glossary_context_text() -> str:
    return " | ".join(f'{item["term"]}: {item["meaning"]}' for item in HELP_GLOSSARY)


def render_help_glossary_tab(openai_ready: bool) -> None:
    section(
        "Help & Glossary",
        "Search the terms used throughout the app, or ask ChatGPT to explain something in plain English.",
    )
    glossary_col, chat_col = st.columns([1.08, 0.92], gap="large")

    with glossary_col:
        st.markdown("### Search glossary")
        query = st.text_input(
            "Search terms",
            value="",
            placeholder="Try: validation trade, VWAP, drawdown, profit factor…",
            key="help_glossary_search",
        ).strip().lower()
        matches = [
            item for item in HELP_GLOSSARY
            if not query or query in item["term"].lower() or query in item["category"].lower() or query in item["meaning"].lower()
        ]
        st.caption(f"{len(matches)} of {len(HELP_GLOSSARY)} terms shown")
        if not matches:
            st.info("No glossary terms matched that search. Try a shorter word, or ask ChatGPT on the right.")
        else:
            for item in matches:
                with st.expander(f'{item["term"]} · {item["category"]}', expanded=bool(query and len(matches) <= 5)):
                    st.write(item["meaning"])

    with chat_col:
        st.markdown("### Ask ChatGPT")
        st.caption(
            "Ask about a metric, strategy rule, optimizer setting, market-data term, or something you see in the app. "
            "This uses your OpenAI API key and is separate from your ChatGPT subscription."
        )
        if openai_ready:
            st.success(f'ChatGPT help connected · {setting("OPENAI_HELP_MODEL", "gpt-5.6-luna")}')
        else:
            st.warning("Ask ChatGPT is not connected yet. Add OPENAI_API_KEY in Streamlit Secrets.")

        history = st.session_state.setdefault("help_chat_history", [])
        for message in history[-8:]:
            role = "assistant" if message.get("role") == "assistant" else "user"
            with st.chat_message(role):
                st.markdown(str(message.get("content") or ""))

        with st.form("help_chat_form", clear_on_submit=True):
            question = st.text_area(
                "Your question",
                height=105,
                placeholder="Example: Why can profit factor be blank even when the strategy made money?",
            )
            ask_submitted = st.form_submit_button("Ask ChatGPT", use_container_width=True, disabled=not openai_ready)

        if ask_submitted:
            clean_question = question.strip()
            if not clean_question:
                st.warning("Type a question first.")
            else:
                st.session_state["help_chat_history"].append({"role": "user", "content": clean_question})
                try:
                    with st.spinner("ChatGPT is answering…"):
                        answer = ask_chatgpt_help(
                            setting("OPENAI_API_KEY"),
                            clean_question,
                            model=setting("OPENAI_HELP_MODEL", "gpt-5.6-luna"),
                            glossary_context=glossary_context_text(),
                        )
                    st.session_state["help_chat_history"].append({"role": "assistant", "content": answer})
                    st.session_state["help_chat_history"] = st.session_state["help_chat_history"][-16:]
                    st.rerun()
                except AppError as error:
                    st.error(str(error))

        if history and st.button("Clear Help chat", key="clear_help_chat", use_container_width=True):
            st.session_state["help_chat_history"] = []
            st.rerun()


'''
if "HELP_GLOSSARY:" not in app:
    if helper_anchor not in app:
        raise SystemExit("Could not find credentials_ready helper")
    app = app.replace(helper_anchor, glossary_helpers + helper_anchor, 1)

# Track OpenAI readiness without changing existing credentials_ready signature.
ready_anchor = '''gemini_ready, alpaca_ready = credentials_ready()\n'''
ready_replacement = '''gemini_ready, alpaca_ready = credentials_ready()\nopenai_help_ready = bool(setting("OPENAI_API_KEY"))\n'''
if ready_replacement not in app:
    if ready_anchor not in app:
        raise SystemExit("Could not find credentials readiness assignment")
    app = app.replace(ready_anchor, ready_replacement, 1)

# Sidebar connection status.
sidebar_anchor = '''    st.success("Alpaca connected") if alpaca_ready else st.warning("Alpaca credentials needed")\n    st.caption(f'Model: {setting("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)}')\n'''
sidebar_replacement = '''    st.success("Alpaca connected") if alpaca_ready else st.warning("Alpaca credentials needed")\n    st.success("ChatGPT help connected") if openai_help_ready else st.caption("ChatGPT help not connected")\n    st.caption(f'Model: {setting("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)}')\n'''
if sidebar_replacement not in app:
    if sidebar_anchor not in app:
        raise SystemExit("Could not find sidebar connection anchor")
    app = app.replace(sidebar_anchor, sidebar_replacement, 1)

# Add optional OpenAI secrets to the displayed setup template.
secrets_anchor = '''        'GEMINI_PAID_API_KEY="your_separate_paid_google_key"\\n'\n        'ALPACA_LIVE_FEED="iex"\\n'\n'''
secrets_replacement = '''        'GEMINI_PAID_API_KEY="your_separate_paid_google_key"\\n'\n        '# Optional: powers the Help & Glossary Ask ChatGPT box\\n'\n        'OPENAI_API_KEY="your_openai_api_key"\\n'\n        'OPENAI_HELP_MODEL="gpt-5.6-luna"\\n'\n        'ALPACA_LIVE_FEED="iex"\\n'\n'''
if secrets_replacement not in app:
    if secrets_anchor not in app:
        raise SystemExit("Could not find sidebar secrets template")
    app = app.replace(secrets_anchor, secrets_replacement, 1)

# Add Help & Glossary as a dedicated tab before Setup & backups.
tabs_anchor = '''overview_tab, videos_tab, master_tab, strategies_tab, backtest_tab, optimizer_tab, scanner_tab, paper_tab, settings_tab = st.tabs(\n    [\n        "Overview", "Analyze videos", "Master strategy", "Strategy library", "Backtesting",\n        "Stock optimizer", "Live scanner", "Paper journal", "Setup & backups",\n    ]\n)\n'''
tabs_replacement = '''overview_tab, videos_tab, master_tab, strategies_tab, backtest_tab, optimizer_tab, scanner_tab, paper_tab, help_tab, settings_tab = st.tabs(\n    [\n        "Overview", "Analyze videos", "Master strategy", "Strategy library", "Backtesting",\n        "Stock optimizer", "Live scanner", "Paper journal", "Help & Glossary", "Setup & backups",\n    ]\n)\n'''
if tabs_replacement not in app:
    if tabs_anchor not in app:
        raise SystemExit("Could not find tabs definition")
    app = app.replace(tabs_anchor, tabs_replacement, 1)

# Add the tab body immediately before settings tab.
settings_anchor = '''\n\nwith settings_tab:\n'''
help_body = '''\n\nwith help_tab:\n    render_help_glossary_tab(openai_help_ready)\n'''
if help_body.strip() not in app:
    if settings_anchor not in app:
        raise SystemExit("Could not find settings tab body")
    app = app.replace(settings_anchor, help_body + settings_anchor, 1)

app_path.write_text(app, encoding="utf-8")
