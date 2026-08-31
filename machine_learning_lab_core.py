"""Machine-learning research page for YouTube Trading Strategy Lab."""

from __future__ import annotations

from datetime import timedelta
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline

from app_access import require_app_access
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_progress_ui import LongTaskMonitor, session_task_profiles
from ml_session_features import add_session_aware_ml_features, add_session_outcome_labels
from youtube_strategy_engine import (
    DEFAULT_GITHUB_BACKUP_PATH,
    AlpacaMarketData,
    AppError,
    GitHubCloudBackup,
    StrategyStore,
    add_indicators,
    bars_to_frame,
    evaluate_signal,
    normalize_machine_rules,
    parse_symbols,
    safe_float,
    split_safe_raw_research_rows,
    utc_now,
)


st.set_page_config(
    page_title="Machine Learning Lab",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="locked",
)
require_app_access(st)

with st.sidebar:
    st.divider()
    if st.button("← Trading Dashboard", key="ml_back_dashboard", width="stretch"):
        st.switch_page("youtube_strategy_app.py")
    if st.button("Open Full Trading Lab", key="ml_open_full_lab", width="stretch"):
        st.switch_page("pages/Full_Trading_Lab.py")


FEATURE_LABELS = {
    "return_1": "1-bar return",
    "return_3": "3-bar return",
    "return_12": "12-bar return",
    "range_pct": "Bar range %",
    "body_pct": "Candle body %",
    "atr_14_pct": "ATR 14 %",
    "rsi_14": "RSI 14",
    "ema_8_gap_pct": "Price vs EMA 8",
    "ema_21_gap_pct": "Price vs EMA 21",
    "ema_8_21_spread_pct": "EMA 8 vs EMA 21",
    "rolling_volatility_20": "20-bar volatility",
    "relative_volume": "Relative volume",
    "volume_surge": "Volume surge",
    "volume_z20": "Volume z-score",
    "overnight_gap_pct": "Prior-session gap %",
    "day_change_pct": "Day change %",
    "vwap_distance_signed_pct": "Signed VWAP distance %",
    "green_streak": "Green-bar streak",
    "session_progress": "Session progress",
    "log_cum_dollar_volume": "Cumulative dollar volume",
    "breakout_gap_pct": "Distance to breakout level",
    "opening_range_gap_pct": "Distance to opening-range high",
    "strategy_match": "Strategy rules matched",
}

BASE_FEATURE_COLUMNS = list(FEATURE_LABELS)


def setting(name: str, default: str = "") -> str:
    try:
        if name in st.secrets and str(st.secrets[name]).strip():
            return str(st.secrets[name]).strip()
    except (FileNotFoundError, KeyError, RuntimeError, AttributeError):
        pass
    return str(os.environ.get(name, default)).strip()


def percent(value: Any, digits: int = 1) -> str:
    number = safe_float(value)
    return f"{number:.{digits}f}%" if number is not None else "—"


def selected_strategy_options(strategies: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in strategies:
        creator = str(item.get("creator") or "Unknown creator")
        symbol = str(item.get("optimized_for_symbol") or "").strip().upper()
        optimized = f" · {symbol} optimized" if symbol else ""
        label = f'{item.get("name", "Unnamed strategy")} — {creator}{optimized}'
        if label in result:
            label = f'{label} [{str(item.get("id", ""))[:6]}]'
        result[label] = item
    return result


def build_store() -> StrategyStore:
    backup_repository = setting("GITHUB_BACKUP_REPOSITORY")
    backup_token = setting("GITHUB_BACKUP_TOKEN")
    cloud_backup = None
    if backup_repository and backup_token:
        cloud_backup = GitHubCloudBackup(
            backup_repository,
            backup_token,
            branch=setting("GITHUB_BACKUP_BRANCH"),
            path=setting("GITHUB_BACKUP_PATH", DEFAULT_GITHUB_BACKUP_PATH),
        )
    return StrategyStore(cloud_backup=cloud_backup)


def market_client() -> AlpacaMarketData:
    return AlpacaMarketData(
        setting("ALPACA_API_KEY"),
        setting("ALPACA_SECRET_KEY"),
        setting("ALPACA_LIVE_FEED", "iex"),
        setting("ALPACA_HISTORICAL_FEED", "sip"),
    )


def add_ml_features(frame: pd.DataFrame, strategy: dict[str, Any]) -> pd.DataFrame:
    data = add_indicators(frame, strategy).copy()
    data = add_session_aware_ml_features(data)
    close = pd.to_numeric(data["close"], errors="coerce")

    if "vwap" in data:
        data["vwap_distance_signed_pct"] = (
            close
            / pd.to_numeric(data["vwap"], errors="coerce").replace(0, np.nan)
            - 1.0
        ) * 100.0
    else:
        data["vwap_distance_signed_pct"] = np.nan

    data["session_progress"] = (
        pd.to_numeric(data["session_minute"], errors="coerce")
        .clip(lower=0, upper=389)
        / 389.0
    )
    cumulative_dollars = pd.to_numeric(
        data.get("cum_dollar_volume"), errors="coerce"
    )
    data["log_cum_dollar_volume"] = np.log1p(cumulative_dollars.clip(lower=0))

    prior_breakout = pd.to_numeric(
        data.get("prior_breakout_high"), errors="coerce"
    )
    opening_high = pd.to_numeric(
        data.get("opening_range_high"), errors="coerce"
    )
    data["breakout_gap_pct"] = (
        close / prior_breakout.replace(0, np.nan) - 1.0
    ) * 100.0
    data["opening_range_gap_pct"] = (
        close / opening_high.replace(0, np.nan) - 1.0
    ) * 100.0

    rules = normalize_machine_rules(strategy.get("machine_rules"))
    data["strategy_match"] = [
        1.0 if evaluate_signal(row, rules) else 0.0
        for _, row in data.iterrows()
    ]
    return data


def add_outcome_labels(
    frame: pd.DataFrame,
    *,
    stop_pct: float,
    reward_risk: float,
    horizon_bars: int,
    same_bar_policy: str = "ambiguous_exclude",
    require_full_horizon: bool = True,
) -> pd.DataFrame:
    """Label predictive outcomes without crossing session boundaries."""
    return add_session_outcome_labels(
        frame,
        stop_pct=stop_pct,
        reward_risk=reward_risk,
        horizon_bars=horizon_bars,
        same_bar_policy=same_bar_policy,
        require_full_horizon=require_full_horizon,
    )


def build_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                RandomForestClassifier(
                    n_estimators=260,
                    max_depth=10,
                    min_samples_leaf=8,
                    max_features="sqrt",
                    class_weight="balanced_subsample",
                    random_state=42,
                    n_jobs=-1,
                ),
            ),
        ]
    )


def walk_forward_predictions(
    model_data: pd.DataFrame,
    feature_columns: list[str],
    *,
    threshold: float,
    folds: int = 4,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    sessions = list(dict.fromkeys(model_data["session"].astype(str).tolist()))
    if len(sessions) < 10:
        raise AppError("Machine learning needs at least 10 completed trading sessions. Increase Historical calendar days.")

    initial_train = max(6, int(len(sessions) * 0.50))
    remaining = len(sessions) - initial_train
    if remaining < folds:
        raise AppError("Not enough completed sessions for walk-forward testing. Increase the historical range.")
    fold_size = max(1, remaining // folds)

    predictions: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []

    for fold in range(folds):
        test_start = initial_train + fold * fold_size
        test_end = len(sessions) if fold == folds - 1 else min(len(sessions), test_start + fold_size)
        if test_start >= len(sessions):
            break

        # Keep one whole session between train and test to avoid horizon overlap/leakage.
        train_sessions = sessions[: max(0, test_start - 1)]
        test_sessions = sessions[test_start:test_end]
        train = model_data[model_data["session"].astype(str).isin(train_sessions)]
        test = model_data[model_data["session"].astype(str).isin(test_sessions)]
        if len(train) < 150 or len(test) < 20 or train["profitable_outcome"].nunique() < 2:
            continue

        pipeline = build_pipeline()
        pipeline.fit(train[feature_columns], train["profitable_outcome"].astype(int))
        probability = pipeline.predict_proba(test[feature_columns])[:, 1]
        predicted = probability >= threshold
        actual = test["profitable_outcome"].astype(int).to_numpy()

        auc = None
        if len(np.unique(actual)) == 2:
            auc = float(roc_auc_score(actual, probability))

        fold_rows.append(
            {
                "Fold": fold + 1,
                "Train sessions": len(train_sessions),
                "Test sessions": len(test_sessions),
                "Test rows": len(test),
                "Accuracy": float(accuracy_score(actual, predicted)),
                "Precision": float(precision_score(actual, predicted, zero_division=0)),
                "Recall": float(recall_score(actual, predicted, zero_division=0)),
                "F1": float(f1_score(actual, predicted, zero_division=0)),
                "ROC AUC": auc,
                "Brier": float(brier_score_loss(actual, probability)),
            }
        )
        out = test[["timestamp", "session", "strategy_match", "profitable_outcome", "outcome_return_pct"]].copy()
        out["ml_probability"] = probability
        out["fold"] = fold + 1
        predictions.append(out)

    if not predictions:
        raise AppError(
            "The available history could not produce a valid walk-forward fold with both winning and losing outcomes. "
            "Increase the history or try a different ticker/timeframe."
        )
    return pd.concat(predictions, ignore_index=True), fold_rows


def feature_importance_table(pipeline: Pipeline, feature_columns: list[str]) -> pd.DataFrame:
    importance = pipeline.named_steps["model"].feature_importances_
    table = pd.DataFrame(
        {
            "Feature": [FEATURE_LABELS.get(name, name) for name in feature_columns],
            "Importance": importance,
        }
    )
    return table.sort_values("Importance", ascending=False).reset_index(drop=True)


st.markdown(
    """
    <style>
    .ml-hero {padding:22px 24px;border:1px solid #30415d;border-radius:18px;
      background:linear-gradient(125deg,#17243b,#101827 70%,#1d263f);margin-bottom:16px}
    .ml-title {font-size:31px;font-weight:900;letter-spacing:-.03em}
    .ml-sub {color:#adbbcf;margin-top:7px;max-width:1000px}
    .ml-callout {padding:13px 15px;border:1px solid #33445d;border-radius:12px;background:#111a29}
    </style>
    <div class="ml-hero">
      <div class="ml-title">🧠 Machine Learning Lab</div>
      <div class="ml-sub">
        Train a model on historical Alpaca candles, test it chronologically, and measure whether an ML filter
        improves the saved strategy's out-of-sample trigger quality. This is a research tool; it never submits orders.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

try:
    store = build_store()
    library = store.load()
except AppError as error:
    st.error(str(error))
    st.stop()

approved = [item for item in library.get("strategies", []) if item.get("approved")]
market_ready = bool(setting("ALPACA_API_KEY") and setting("ALPACA_SECRET_KEY"))

with st.sidebar:
    st.markdown("### ML Lab connection")
    if market_ready:
        st.success("Alpaca market data connected")
    else:
        st.error("Alpaca credentials needed")
    st.caption("Training uses historical bars only. No brokerage order endpoint is called.")
    st.divider()
    st.markdown("### What the score means")
    st.caption(
        "The model estimates whether a hypothetical next-bar entry reaches the strategy-style profit outcome "
        "before/within the selected holding horizon. It is not a guaranteed probability of future profit."
    )

if not approved:
    st.warning("Approve at least one saved strategy in the main Trading Lab before training an ML model.")
    st.stop()

if not market_ready:
    st.error("Add ALPACA_API_KEY and ALPACA_SECRET_KEY to Streamlit Secrets before using Machine Learning Lab.")
    st.stop()

options = selected_strategy_options(approved)
selected_label = st.selectbox("Approved strategy", list(options.keys()))
strategy = options[selected_label]
rules = normalize_machine_rules(strategy.get("machine_rules"))

optimized_symbol = str(strategy.get("optimized_for_symbol") or "").strip().upper()
symbol_default = optimized_symbol or ""
symbol = st.text_input(
    "Ticker",
    value=symbol_default,
    placeholder="Example: AAPL",
    help="Stock-specific optimized strategies are locked to their saved ticker.",
).strip().upper()

if optimized_symbol and symbol and symbol != optimized_symbol:
    st.error(f"This optimized strategy is locked to {optimized_symbol}.")
    st.stop()

row1 = st.columns(4)
with row1[0]:
    timeframe = st.selectbox("Candle interval", ["1Min", "5Min", "15Min"], index=1)
with row1[1]:
    history_days = st.slider("Historical calendar days", 7, 180, 60, 1)
with row1[2]:
    threshold_pct = st.slider("ML qualification threshold", 50, 90, 65, 1)
with row1[3]:
    strategy_hold = safe_float(rules.get("max_hold_minutes"))
    if strategy_hold is not None:
        st.number_input("Outcome horizon (minutes)", value=int(strategy_hold), disabled=True)
        horizon_minutes = max(1, int(strategy_hold))
    else:
        horizon_minutes = st.select_slider(
            "Outcome horizon (minutes)",
            options=[15, 30, 45, 60, 90, 120, 180, 240],
            value=60,
        )

row2 = st.columns(3)
saved_stop = safe_float(rules.get("stop_loss_pct"))
saved_rr = safe_float(rules.get("reward_risk"))
with row2[0]:
    if saved_stop is not None:
        st.number_input("Stop loss %", value=float(saved_stop), disabled=True, format="%.2f")
        stop_pct = float(saved_stop)
    else:
        stop_pct = float(st.number_input("Research stop loss %", min_value=0.1, max_value=30.0, value=2.0, step=0.1))
with row2[1]:
    if saved_rr is not None:
        st.number_input("Reward / risk", value=float(saved_rr), disabled=True, format="%.2f")
        reward_risk = float(saved_rr)
    else:
        reward_risk = float(st.number_input("Research reward / risk", min_value=0.25, max_value=10.0, value=2.0, step=0.25))
with row2[2]:
    timeframe_minutes = {"1Min": 1, "5Min": 5, "15Min": 15}[timeframe]
    horizon_bars = max(1, int(np.ceil(horizon_minutes / timeframe_minutes)))
    st.metric("Bars in outcome window", horizon_bars)

st.caption(
    "Walk-forward testing uses only earlier completed trading sessions to predict later sessions, "
    "with a one-session safety gap between training and test periods."
)

ml_button_slot = st.empty()
run = ml_button_slot.button(
    "🧠 Train + walk-forward test",
    type="primary",
    width="stretch",
    key="ml_train_walk_forward",
)

if run:
    ml_button_slot.button(
        "🧠 Training…",
        type="primary",
        width="stretch",
        disabled=True,
        key="ml_train_walk_forward_busy",
    )
    ml_monitor = LongTaskMonitor(
        "machine_learning_train_walk_forward",
        session_task_profiles(st.session_state, "machine_learning_train_walk_forward"),
    )
    ml_bar = st.progress(
        0.03,
        text=ml_monitor.text(0.03, "Preparing machine-learning research…"),
    )
    clean = parse_symbols(symbol)
    if len(clean) != 1:
        st.error("Enter exactly one valid ticker.")
        st.stop()
    ticker = clean[0]

    try:
        with st.status("Building ML dataset…", expanded=True) as status:
            market = market_client()
            historical_end = utc_now() - timedelta(
                minutes=16 if market.historical_feed == "sip" and market.live_feed != "sip" else 1
            )
            historical_start = historical_end - timedelta(days=int(history_days))
            st.write(f"Downloading {ticker} {timeframe} bars…")
            ml_bar.progress(0.10, text=ml_monitor.text(0.10, f"Downloading {ticker} {timeframe} bars"))
            bars = market.bars(
                [ticker],
                start=historical_start,
                end=historical_end,
                timeframe=timeframe,
                adjustment="raw",
                max_pages=30,
            ).get(ticker, [])
            split_actions = market.split_actions(
                [ticker],
                start=historical_start,
                end=historical_end,
            )
            bars, market_data_integrity = split_safe_raw_research_rows(
                list(bars or []),
                split_actions,
                ticker,
            )
            if market_data_integrity.get("split_detected"):
                st.write(
                    "Corporate-action integrity guard: raw historical prices retained; "
                    f"ML training restarted at {market_data_integrity.get('latest_split_date')} "
                    "to avoid learning a stock split as a price/volume pattern."
                )
            if len(bars) < 300:
                raise AppError(
                    f"Only {len(bars)} bars were returned. Increase the historical range or use a smaller candle interval."
                )

            ml_bar.progress(0.30, text=ml_monitor.text(0.30, f"Downloaded {len(bars):,} bars · engineering features"))
            st.write(f"Engineering features from {len(bars):,} bars…")
            base = bars_to_frame(bars)
            featured = add_ml_features(base, strategy)
            latest_row = featured.iloc[[-1]].copy()
            latest_session = str(featured.iloc[-1]["session"])

            ml_bar.progress(0.45, text=ml_monitor.text(0.45, "Creating forward profit/stop outcome labels"))
            st.write("Creating forward profit/stop outcome labels…")
            labeled = add_outcome_labels(
                featured,
                stop_pct=stop_pct,
                reward_risk=reward_risk,
                horizon_bars=horizon_bars,
            )
            # Never train on the current/incomplete trading session.
            model_data = labeled[
                (labeled["session"].astype(str) != latest_session)
                & labeled["profitable_outcome"].notna()
            ].copy()

            if len(model_data) < 300:
                raise AppError(
                    f"Only {len(model_data)} fully labeled historical rows are available. "
                    "Increase Historical calendar days before training."
                )
            if model_data["profitable_outcome"].nunique() < 2:
                raise AppError("The training set contains only one outcome class, so a classifier cannot be trained.")

            usable_features = [
                name
                for name in BASE_FEATURE_COLUMNS
                if name in model_data.columns and model_data[name].replace([np.inf, -np.inf], np.nan).notna().any()
            ]
            if len(usable_features) < 8:
                raise AppError("Too few usable market features were available for a reliable ML run.")

            model_data[usable_features] = model_data[usable_features].replace([np.inf, -np.inf], np.nan)
            latest_row[usable_features] = latest_row[usable_features].replace([np.inf, -np.inf], np.nan)

            threshold = threshold_pct / 100.0
            ml_bar.progress(0.62, text=ml_monitor.text(0.62, "Running chronological walk-forward tests"))
            st.write("Running chronological walk-forward tests…")
            oos, fold_rows = walk_forward_predictions(
                model_data,
                usable_features,
                threshold=threshold,
                folds=4,
            )

            ml_bar.progress(0.84, text=ml_monitor.text(0.84, "Fitting final model on completed historical sessions"))
            st.write("Fitting final model on all completed historical sessions…")
            final_model = build_pipeline()
            final_model.fit(model_data[usable_features], model_data["profitable_outcome"].astype(int))
            latest_probability = float(final_model.predict_proba(latest_row[usable_features])[:, 1][0])
            latest_strategy_match = bool(float(latest_row.iloc[0]["strategy_match"]) >= 0.5)
            importance = feature_importance_table(final_model, usable_features)
            status.update(label="Machine-learning run complete", state="complete", expanded=False)
            ml_monitor.finish(st.session_state)
            ml_bar.progress(1.0, text="Machine-learning run complete · 100%")

        actual = oos["profitable_outcome"].astype(int).to_numpy()
        probability = oos["ml_probability"].to_numpy(dtype=float)
        predicted = probability >= threshold
        auc = roc_auc_score(actual, probability) if len(np.unique(actual)) == 2 else None

        baseline = oos[oos["strategy_match"] >= 0.5]
        qualified = baseline[baseline["ml_probability"] >= threshold]
        baseline_win = float(baseline["profitable_outcome"].mean() * 100.0) if len(baseline) else None
        qualified_win = float(qualified["profitable_outcome"].mean() * 100.0) if len(qualified) else None
        lift = (
            qualified_win - baseline_win
            if baseline_win is not None and qualified_win is not None
            else None
        )

        st.markdown("## Walk-forward results")
        metrics = st.columns(5)
        metrics[0].metric("OOS rows", f"{len(oos):,}")
        metrics[1].metric("Accuracy", percent(accuracy_score(actual, predicted) * 100.0))
        metrics[2].metric("Precision", percent(precision_score(actual, predicted, zero_division=0) * 100.0))
        metrics[3].metric("ROC AUC", f"{auc:.3f}" if auc is not None else "—")
        metrics[4].metric("Brier score", f"{brier_score_loss(actual, probability):.3f}")

        st.caption(
            "ROC AUC: 0.50 is roughly random ranking; higher is better. "
            "Brier score measures probability error; lower is better."
        )

        st.markdown("### Does ML improve this strategy?")
        comparison = st.columns(4)
        comparison[0].metric("Raw strategy OOS triggers", len(baseline))
        comparison[1].metric("Raw trigger win rate", percent(baseline_win))
        comparison[2].metric(f"ML-qualified triggers ≥ {threshold_pct}%", len(qualified))
        comparison[3].metric(
            "Qualified win-rate lift",
            f"{lift:+.1f} pts" if lift is not None else "—",
            help="Qualified trigger win rate minus raw strategy trigger win rate.",
        )

        if len(baseline) < 10:
            st.warning(
                "This strategy produced fewer than 10 out-of-sample triggers. Treat the strategy-vs-ML comparison as preliminary."
            )
        elif len(qualified) < 5:
            st.warning(
                "The ML threshold leaves fewer than 5 qualified out-of-sample strategy triggers. "
                "That is too small a sample to trust."
            )
        elif qualified_win is not None and baseline_win is not None:
            if qualified_win > baseline_win:
                st.success(
                    f"At this threshold, ML improved historical OOS trigger win rate from "
                    f"{baseline_win:.1f}% to {qualified_win:.1f}%."
                )
            else:
                st.warning(
                    f"At this threshold, ML did not improve historical OOS trigger win rate "
                    f"({baseline_win:.1f}% raw vs {qualified_win:.1f}% qualified)."
                )

        with st.expander("Walk-forward fold details"):
            fold_table = pd.DataFrame(fold_rows)
            percent_cols = ["Accuracy", "Precision", "Recall", "F1"]
            for col in percent_cols:
                if col in fold_table:
                    fold_table[col] = (fold_table[col] * 100.0).round(1)
            if "ROC AUC" in fold_table:
                fold_table["ROC AUC"] = fold_table["ROC AUC"].round(3)
            if "Brier" in fold_table:
                fold_table["Brier"] = fold_table["Brier"].round(3)
            st.dataframe(fold_table, width="stretch", hide_index=True)

        st.markdown("## Latest historical setup")
        latest_cols = st.columns(3)
        latest_cols[0].metric("ML profitable-outcome score", percent(latest_probability * 100.0))
        latest_cols[1].metric("Strategy rules now", "TRIGGER" if latest_strategy_match else "NO TRIGGER")

        if latest_strategy_match and latest_probability >= threshold:
            combined = "ML-QUALIFIED SETUP"
            st.success(
                f"{combined}: the saved strategy is triggered on the latest available bar and the ML score "
                f"({latest_probability * 100:.1f}%) is above the {threshold_pct}% filter."
            )
        elif latest_strategy_match:
            combined = "ML FILTERED OUT"
            st.warning(
                f"{combined}: the strategy is triggered, but the ML score "
                f"({latest_probability * 100:.1f}%) is below the {threshold_pct}% filter."
            )
        else:
            combined = "NO STRATEGY TRIGGER"
            st.info(
                f"{combined}: the model score is {latest_probability * 100:.1f}%, but the saved strategy's rules "
                "are not currently satisfied, so the combined system does not qualify the setup."
            )
        latest_cols[2].metric("Combined research signal", combined)

        st.markdown("## What the model learned")
        st.dataframe(
            importance.head(12).assign(Importance=lambda df: (df["Importance"] * 100.0).round(2)),
            width="stretch",
            hide_index=True,
        )

        with st.expander("Out-of-sample prediction sample"):
            preview = oos.tail(250).copy()
            preview["ML score"] = (preview["ml_probability"] * 100.0).round(1)
            preview["Actual profitable"] = preview["profitable_outcome"].astype(int).map({1: "Yes", 0: "No"})
            preview["Strategy trigger"] = preview["strategy_match"].astype(int).map({1: "Yes", 0: "No"})
            preview["Outcome return %"] = preview["outcome_return_pct"].round(2)
            st.dataframe(
                preview[
                    ["timestamp", "session", "ML score", "Actual profitable", "Strategy trigger", "Outcome return %", "fold"]
                ].rename(columns={"timestamp": "Timestamp", "session": "Session", "fold": "Fold"}),
                width="stretch",
                hide_index=True,
            )

        st.markdown("### Important limitations")
        st.caption(
            "The model is trained on one ticker and one saved strategy at a time. Historical bars use actual raw prices "
            "and restart at the latest split boundary so the model does not learn split-adjusted price/liquidity context. "
            "Labels use historical OHLC bars, "
            "not exact bid/ask fills, queue position, halts, or news context. Random-forest probability scores are model "
            "scores rather than guaranteed or perfectly calibrated probabilities. Walk-forward results can still degrade "
            "in a different market regime, so this should remain a research/paper-trading filter until it proves itself "
            "on genuinely unseen live data."
        )

    except AppError as error:
        st.error(str(error))
    except Exception as error:
        st.exception(error)
