"""Cross-stock supervised dataset and leakage-safe baseline ML evaluation.

This module deliberately keeps model research separate from live scoring. It builds
point-in-time feature rows across symbols, persists them reproducibly, and evaluates
simple probabilistic classifiers with chronological expanding-window folds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import tempfile
from typing import Any, Callable

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from market_feature_validation import DEFAULT_HORIZONS, build_supervised_feature_rows, limit_rows_to_recent_market_sessions
from youtube_strategy_engine import parse_symbols


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


MARKET_SESSION_MODES = ("regular", "premarket", "afterhours")


def _normalize_market_session_mode(value: str) -> str:
    mode = str(value or "regular").strip().lower()
    aliases = {
        "regular": "regular",
        "regular_hours": "regular",
        "rth": "regular",
        "premarket": "premarket",
        "pre_market": "premarket",
        "afterhours": "afterhours",
        "after_hours": "afterhours",
        "postmarket": "afterhours",
    }
    normalized = aliases.get(mode)
    if normalized is None:
        raise ValueError("session_mode must be 'regular', 'premarket', or 'afterhours'.")
    return normalized


def _filter_rows_by_market_session(
    rows: list[dict[str, Any]],
    session_mode: str,
) -> list[dict[str, Any]]:
    """Keep exactly one continuous U.S. equity market-hours regime.

    Regular is 09:30-16:00 ET, premarket is 04:00-09:30 ET, and after-hours is
    16:00-20:00 ET. Premarket and after-hours are deliberately separate so a
    forward label can never jump across the regular-session gap.
    Rows without parseable timestamps are excluded because they cannot be assigned
    safely to a market-hours regime.
    """
    mode = _normalize_market_session_mode(session_mode)
    selected: list[dict[str, Any]] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        stamp = pd.to_datetime(
            raw.get("t", raw.get("timestamp", raw.get("time"))),
            utc=True,
            errors="coerce",
        )
        if pd.isna(stamp):
            continue
        local = stamp.tz_convert("America/New_York")
        minute = int(local.hour) * 60 + int(local.minute)
        is_regular = (9 * 60 + 30) <= minute < (16 * 60)
        is_premarket = (4 * 60) <= minute < (9 * 60 + 30)
        is_afterhours = (16 * 60) <= minute < (20 * 60)
        if (
            (mode == "regular" and is_regular)
            or (mode == "premarket" and is_premarket)
            or (mode == "afterhours" and is_afterhours)
        ):
            selected.append(dict(raw))
    return selected


CONTEXT_FEATURE_COLUMNS: tuple[str, ...] = (
    "feature__context_prior_session_count",
    "feature__context_typical_price",
    "feature__context_typical_range_pct",
    "feature__context_typical_dollar_volume",
    "feature__context_typical_bar_dollar_volume",
    "feature__context_rolling_5bar_dollar_volume",
    "feature__context_cumulative_dollar_volume",
    "feature__context_session_dollar_pace_ratio",
    "feature__context_price_band",
    "feature__context_range_band",
    "feature__context_liquidity_band",
    "feature__context_current_liquidity_band",
    "feature__context_volume_pace_band",
    "feature__context_volatility_band",
    "feature__context_volume_behavior",
    "feature__context_pattern_personality",
    "feature__context_archetype",
)


def _median_number(values: list[Any]) -> float | None:
    clean = sorted(value for value in (_number(item) for item in values) if value is not None)
    if not clean:
        return None
    midpoint = len(clean) // 2
    if len(clean) % 2:
        return float(clean[midpoint])
    return float((clean[midpoint - 1] + clean[midpoint]) / 2.0)


def _price_band(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "unknown"
    if number < 1:
        return "sub_1"
    if number < 5:
        return "1_to_5"
    if number < 20:
        return "5_to_20"
    if number < 100:
        return "20_to_100"
    return "100_plus"


def _range_band(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "unknown"
    if number < 3:
        return "calm_under_3pct"
    if number < 7:
        return "active_3_to_7pct"
    if number < 15:
        return "volatile_7_to_15pct"
    return "explosive_15pct_plus"


def _liquidity_band(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "unknown"
    if number < 2_000_000:
        return "thin_under_2m"
    if number < 10_000_000:
        return "light_2m_to_10m"
    if number < 50_000_000:
        return "moderate_10m_to_50m"
    return "liquid_50m_plus"


def _bar_liquidity_band(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "unknown"
    if number < 25_000:
        return "thin_under_25k"
    if number < 100_000:
        return "light_25k_to_100k"
    if number < 500_000:
        return "active_100k_to_500k"
    return "liquid_500k_plus"


def _pace_band(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "unknown"
    if number < 0.7:
        return "quiet"
    if number < 1.5:
        return "normal"
    if number < 3.0:
        return "hot"
    return "extreme"


def _volatility_band(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "unknown"
    if number < 0.5:
        return "low"
    if number < 1.5:
        return "moderate"
    if number < 3.0:
        return "high"
    return "extreme"


def _context_archetype(
    *,
    typical_price: Any,
    typical_range_pct: Any,
    typical_dollar_volume: Any,
) -> str:
    """Assign an explainable lagged stock family using prior completed sessions only."""
    price = _number(typical_price)
    range_pct = _number(typical_range_pct)
    dollar_volume = _number(typical_dollar_volume)
    if price is None or range_pct is None or dollar_volume is None:
        return "unknown"
    if price < 5 and range_pct >= 10:
        return "low_price_explosive"
    if price < 10 and range_pct >= 6:
        return "low_price_active"
    if dollar_volume >= 50_000_000 and range_pct >= 5:
        return "liquid_volatile"
    if dollar_volume >= 50_000_000:
        return "liquid_steady"
    if range_pct >= 8:
        return "thin_volatile"
    if dollar_volume < 10_000_000:
        return "thin_moderate"
    return "moderate_momentum"


def _session_expected_bars(session_mode: str) -> int:
    return {
        "regular": 390,
        "premarket": 330,
        "afterhours": 240,
    }[_normalize_market_session_mode(session_mode)]


def _causal_context_by_session_bar(
    rows: list[dict[str, Any]],
    *,
    session_mode: str,
    prior_session_lookback: int = 5,
) -> dict[tuple[str, int], dict[str, Any]]:
    """Build causal cross-stock context using only completed prior sessions plus current bars."""
    frame_rows: list[dict[str, Any]] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        stamp = pd.to_datetime(
            raw.get("t", raw.get("timestamp", raw.get("time"))),
            utc=True,
            errors="coerce",
        )
        if pd.isna(stamp):
            continue
        close = _number(raw.get("c", raw.get("close")))
        high = _number(raw.get("h", raw.get("high")))
        low = _number(raw.get("l", raw.get("low")))
        volume = _number(raw.get("v", raw.get("volume"))) or 0.0
        if close is None or high is None or low is None:
            continue
        frame_rows.append(
            {
                "timestamp": stamp,
                "session": stamp.tz_convert("America/New_York").date().isoformat(),
                "close": close,
                "high": high,
                "low": low,
                "volume": volume,
                "dollar_volume": close * volume,
            }
        )
    if not frame_rows:
        return {}

    frame = pd.DataFrame(frame_rows).sort_values("timestamp").reset_index(drop=True)
    summaries: list[dict[str, float]] = []
    context: dict[tuple[str, int], dict[str, Any]] = {}
    lookback = max(1, int(prior_session_lookback))
    expected_bars = _session_expected_bars(session_mode)

    for session, group in frame.groupby("session", sort=False):
        group = group.sort_values("timestamp").reset_index(drop=True)
        prior = summaries[-lookback:]
        prior_count = len(prior)
        typical_price = _median_number([item.get("typical_price") for item in prior])
        typical_range_pct = _median_number([item.get("range_pct") for item in prior])
        typical_dollar_volume = _median_number([item.get("dollar_volume") for item in prior])
        typical_bar_dollar_volume = _median_number(
            [item.get("median_bar_dollar_volume") for item in prior]
        )
        archetype = (
            _context_archetype(
                typical_price=typical_price,
                typical_range_pct=typical_range_pct,
                typical_dollar_volume=typical_dollar_volume,
            )
            if prior_count >= 2
            else "unknown"
        )

        rolling = group["dollar_volume"].rolling(5, min_periods=1).mean()
        cumulative = group["dollar_volume"].cumsum()
        for bar_index in range(len(group)):
            elapsed_fraction = min(1.0, max(1, bar_index + 1) / float(expected_bars))
            pace_ratio = None
            if (
                typical_dollar_volume is not None
                and typical_dollar_volume > 0
                and elapsed_fraction > 0
            ):
                expected_so_far = typical_dollar_volume * elapsed_fraction
                if expected_so_far > 0:
                    pace_ratio = float(cumulative.iloc[bar_index]) / expected_so_far
            current_price = float(group.at[bar_index, "close"])
            current_5bar_dollar = float(rolling.iloc[bar_index])
            context[(str(session), bar_index)] = {
                "feature__context_prior_session_count": prior_count,
                "feature__context_typical_price": typical_price,
                "feature__context_typical_range_pct": typical_range_pct,
                "feature__context_typical_dollar_volume": typical_dollar_volume,
                "feature__context_typical_bar_dollar_volume": typical_bar_dollar_volume,
                "feature__context_rolling_5bar_dollar_volume": current_5bar_dollar,
                "feature__context_cumulative_dollar_volume": float(cumulative.iloc[bar_index]),
                "feature__context_session_dollar_pace_ratio": pace_ratio,
                "feature__context_price_band": _price_band(current_price),
                "feature__context_range_band": _range_band(typical_range_pct),
                "feature__context_liquidity_band": _liquidity_band(typical_dollar_volume),
                "feature__context_current_liquidity_band": _bar_liquidity_band(current_5bar_dollar),
                "feature__context_volume_pace_band": _pace_band(pace_ratio),
                "feature__context_archetype": archetype,
            }

        first_close = _number(group.iloc[0].get("close"))
        session_high = _number(group["high"].max())
        session_low = _number(group["low"].min())
        range_pct = None
        if first_close is not None and first_close > 0 and session_high is not None and session_low is not None:
            range_pct = (session_high - session_low) / first_close * 100.0
        summaries.append(
            {
                "typical_price": float(group["close"].median()),
                "range_pct": range_pct,
                "dollar_volume": float(group["dollar_volume"].sum()),
                "median_bar_dollar_volume": float(group["dollar_volume"].median()),
            }
        )
    return context


def _pattern_personality(record: dict[str, Any]) -> str:
    if bool(record.get("feature__bounce_structural_strengthening")) or bool(
        record.get("feature__bounce_3_present")
    ):
        return "bounce_sequence"
    if str(record.get("feature__breakout_state") or "") in {"holding", "breakout"} or bool(
        record.get("feature__breakout_above_last_swing_high")
    ):
        return "breakout"
    if bool(record.get("feature__stair_step_up")):
        return "stair_step"
    if str(record.get("feature__pullback_quality") or "") == "strong":
        return "quality_pullback"
    if bool(record.get("feature__vwap_retest_recent")):
        return "vwap_retest"
    if bool(record.get("feature__uptrend_structure")):
        return "structured_uptrend"
    return "neutral_or_mixed"


def _attach_context_features(
    report: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    session_mode: str,
) -> None:
    context = _causal_context_by_session_bar(rows, session_mode=session_mode)
    for record in report.get("records") or []:
        key = (str(record.get("session") or ""), int(record.get("bar_index") or 0))
        values = dict(context.get(key) or {})
        atr_pct = _number(record.get("feature__atr_pct"))
        acceleration = _number(record.get("feature__volume_acceleration_ratio"))
        if acceleration is None:
            volume_behavior = "unknown"
        elif acceleration >= 1.5:
            volume_behavior = "accelerating"
        elif acceleration <= 0.7:
            volume_behavior = "contracting"
        else:
            volume_behavior = "normal"
        values["feature__context_volatility_band"] = _volatility_band(atr_pct)
        values["feature__context_volume_behavior"] = volume_behavior
        values["feature__context_pattern_personality"] = _pattern_personality(record)
        for name in CONTEXT_FEATURE_COLUMNS:
            record[name] = values.get(name)
    existing = set(report.get("feature_columns") or [])
    existing.update(CONTEXT_FEATURE_COLUMNS)
    report["feature_columns"] = sorted(existing)
    report["context_feature_columns"] = list(CONTEXT_FEATURE_COLUMNS)


def build_cross_stock_training_dataset(
    market: Any,
    symbols: list[str],
    *,
    start: Any,
    end: Any,
    timeframe: str = "1Min",
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    swing_radius: int = 3,
    max_pages: int = 80,
    require_full_horizon: bool = True,
    session_limit: int | None = None,
    profit_target_pct: float = 1.0,
    stop_loss_pct: float = 0.75,
    session_mode: str = "regular",
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Build one supervised dataset across many symbols from a single batched bar load.

    session_mode="regular" keeps 09:30-16:00 ET, "premarket" keeps 04:00-09:30 ET,
    and "afterhours" keeps 16:00-20:00 ET. Regimes are never mixed in one dataset.
    """
    clean = parse_symbols(symbols)
    clean_horizons = tuple(sorted({max(1, int(value)) for value in horizons}))
    clean_profit_target = _number(profit_target_pct)
    clean_stop_loss = _number(stop_loss_pct)
    clean_session_mode = _normalize_market_session_mode(session_mode)
    if clean_profit_target is None or clean_profit_target <= 0:
        raise ValueError("profit_target_pct must be greater than zero.")
    if clean_stop_loss is None or clean_stop_loss <= 0:
        raise ValueError("stop_loss_pct must be greater than zero.")

    if not clean:
        return {
            "causal_replay": True,
            "symbols_requested": 0,
            "symbols_with_data": 0,
            "bars_analyzed": 0,
            "row_count": 0,
            "feature_columns": [],
            "label_columns": [],
            "profit_target_pct": float(clean_profit_target),
            "stop_loss_pct": float(clean_stop_loss),
            "barrier_same_bar_policy": "stop_first_conservative",
            "session_mode": clean_session_mode,
            "session_window_et": {
                "regular": "09:30-16:00",
                "premarket": "04:00-09:30",
                "afterhours": "16:00-20:00",
            }[clean_session_mode],
            "records": [],
        }

    if progress:
        progress(f"Loading historical {timeframe} bars for {len(clean)} stocks…")
    rows_by_symbol = market.bars(
        clean,
        start=start,
        end=end,
        timeframe=timeframe,
        max_pages=max_pages,
    )

    records: list[dict[str, Any]] = []
    feature_columns: set[str] = set()
    label_columns: set[str] = set()
    by_symbol: list[dict[str, Any]] = []
    bars_loaded = 0
    bars_analyzed = 0
    sessions_analyzed = 0
    observed_market_sessions: set[str] = set()

    for index, symbol in enumerate(clean, start=1):
        raw_rows = list((rows_by_symbol or {}).get(symbol) or [])
        bars_loaded += len(raw_rows)
        rows = _filter_rows_by_market_session(raw_rows, clean_session_mode)
        rows, selected_sessions = limit_rows_to_recent_market_sessions(rows, session_limit)
        observed_market_sessions.update(
            session for session in selected_sessions if session != "session-0"
        )
        bars_analyzed += len(rows)
        if not rows:
            by_symbol.append(
                {
                    "symbol": symbol,
                    "raw_bars": len(raw_rows),
                    "bars": 0,
                    "sessions": 0,
                    "market_sessions": selected_sessions,
                    "rows": 0,
                }
            )
            continue
        if progress:
            progress(f"Building causal ML rows for {symbol} ({index}/{len(clean)})…")
        report = build_supervised_feature_rows(
            rows,
            horizons=clean_horizons,
            swing_radius=swing_radius,
            require_full_horizon=require_full_horizon,
            profit_target_pct=float(clean_profit_target),
            stop_loss_pct=float(clean_stop_loss),
        )
        _attach_context_features(
            report,
            rows,
            session_mode=clean_session_mode,
        )
        symbol_records = []
        for item in report.get("records") or []:
            row = dict(item)
            row["symbol"] = symbol
            symbol_records.append(row)
        records.extend(symbol_records)
        feature_columns.update(report.get("feature_columns") or [])
        label_columns.update(report.get("label_columns") or [])
        sessions = int(report.get("sessions_analyzed") or 0)
        sessions_analyzed += sessions
        by_symbol.append(
            {
                "symbol": symbol,
                "raw_bars": len(raw_rows),
                "bars": len(rows),
                "sessions": sessions,
                "market_sessions": selected_sessions,
                "rows": len(symbol_records),
            }
        )

    records.sort(
        key=lambda row: (
            str(row.get("session") or ""),
            str(row.get("timestamp") or ""),
            str(row.get("symbol") or ""),
        )
    )
    archetype_distribution: dict[str, dict[str, Any]] = {}
    for row in records:
        archetype = str(row.get("feature__context_archetype") or "unknown")
        item = archetype_distribution.setdefault(
            archetype,
            {"rows": 0, "symbols": set()},
        )
        item["rows"] += 1
        if row.get("symbol"):
            item["symbols"].add(str(row.get("symbol")))
    archetype_summary = [
        {
            "archetype": archetype,
            "rows": int(item["rows"]),
            "symbol_count": len(item["symbols"]),
            "symbols": sorted(item["symbols"]),
        }
        for archetype, item in sorted(archetype_distribution.items())
    ]
    return {
        "causal_replay": True,
        "symbols_requested": len(clean),
        "symbols_with_data": sum(1 for item in by_symbol if int(item.get("bars") or 0) > 0),
        "bars_loaded": bars_loaded,
        "bars_analyzed": bars_analyzed,
        "sessions_analyzed": sessions_analyzed,
        "market_sessions_requested": (
            max(1, int(session_limit)) if session_limit is not None else None
        ),
        "market_sessions_observed": len(observed_market_sessions),
        "market_session_dates": sorted(observed_market_sessions),
        "timeframe": timeframe,
        "horizons": list(clean_horizons),
        "require_full_horizon": bool(require_full_horizon),
        "profit_target_pct": float(clean_profit_target),
        "stop_loss_pct": float(clean_stop_loss),
        "barrier_same_bar_policy": "stop_first_conservative",
        "session_mode": clean_session_mode,
        "session_window_et": {
            "regular": "09:30-16:00",
            "premarket": "04:00-09:30",
            "afterhours": "16:00-20:00",
        }[clean_session_mode],
        "row_count": len(records),
        "feature_columns": sorted(feature_columns),
        "context_feature_columns": list(CONTEXT_FEATURE_COLUMNS),
        "archetype_column": "feature__context_archetype",
        "archetype_distribution": archetype_summary,
        "label_columns": sorted(label_columns),
        "records": records,
        "by_symbol": by_symbol,
        "note": (
            "Feature columns are point-in-time causal values. label__ columns use only later bars "
            "from the same market session and must never be supplied to a model as inputs. "
            "Trade-quality labels count an upside target only when it is reached before the "
            "downside barrier; same-candle target/stop ambiguity is scored conservatively as stop first. "
            f"Market-hours regime: {clean_session_mode}; regular, premarket, and after-hours rows are never mixed. "
            "Context features use current/past bars plus completed prior sessions only. Historical float and "
            "catalyst-profile fields are intentionally excluded until point-in-time coverage is trustworthy."
        ),
    }

def save_training_dataset(dataset: dict[str, Any], destination: str | Path) -> dict[str, str]:
    """Atomically persist records as JSONL plus a compact metadata sidecar."""
    path = Path(destination)
    if path.suffix.lower() != ".jsonl":
        path = path.with_suffix(".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path = path.with_suffix(".meta.json")

    records = list(dataset.get("records") or [])
    metadata = {key: value for key, value in dataset.items() if key != "records"}
    metadata["saved_at_utc"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    metadata["data_file"] = path.name

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent) as handle:
        data_temp = Path(handle.name)
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    data_temp.replace(path)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent) as handle:
        meta_temp = Path(handle.name)
        json.dump(metadata, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    meta_temp.replace(metadata_path)
    return {"data_path": str(path), "metadata_path": str(metadata_path)}


def load_training_dataset(source: str | Path) -> dict[str, Any]:
    """Load a persisted JSONL dataset and its metadata sidecar when present."""
    path = Path(source)
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    records.append(value)

    metadata_path = path.with_suffix(".meta.json")
    metadata: dict[str, Any] = {}
    if metadata_path.exists():
        with metadata_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
            if isinstance(loaded, dict):
                metadata = loaded
    metadata["records"] = records
    metadata["row_count"] = len(records)
    return metadata


def _feature_types(frame: pd.DataFrame, feature_columns: list[str]) -> tuple[list[str], list[str]]:
    numeric: list[str] = []
    categorical: list[str] = []
    for column in feature_columns:
        values = [value for value in frame[column].dropna().tolist()]
        if all(isinstance(value, (bool, int, float)) and not isinstance(value, complex) for value in values):
            numeric.append(column)
        else:
            categorical.append(column)
    return numeric, categorical


def _prepare_feature_frame(frame: pd.DataFrame, numeric: list[str], categorical: list[str]) -> pd.DataFrame:
    prepared = frame.copy()
    for column in numeric:
        prepared[column] = prepared[column].map(
            lambda value: float(value) if isinstance(value, bool) else _number(value)
        )
    for column in categorical:
        prepared[column] = prepared[column].map(
            lambda value: None if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)
        )
    return prepared


def _baseline_pipeline(numeric: list[str], categorical: list[str]) -> Pipeline:
    transformers = []
    if numeric:
        transformers.append(
            (
                "numeric",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                numeric,
            )
        )
    if categorical:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical,
            )
        )
    if not transformers:
        raise ValueError("No usable feature columns were available for the baseline model.")
    return Pipeline(
        steps=[
            ("features", ColumnTransformer(transformers=transformers, remainder="drop")),
            ("model", LogisticRegression(max_iter=1000, solver="liblinear")),
        ]
    )


def _safe_auc(y_true: list[int], probabilities: list[float]) -> float | None:
    if len(set(y_true)) < 2:
        return None
    return float(roc_auc_score(y_true, probabilities))


def walk_forward_logistic_baseline(
    dataset: dict[str, Any],
    *,
    target_horizon: int = 15,
    target_mode: str = "positive_return",
    min_train_sessions: int = 10,
    test_sessions_per_fold: int = 2,
    embargo_sessions: int = 1,
    min_train_rows: int = 100,
) -> dict[str, Any]:
    """Evaluate a probability baseline with expanding, session-level walk-forward folds.

    Entire market sessions are kept together. The optional embargo removes the
    sessions immediately preceding each test block from training, which is a
    conservative guard against adjacent-period dependence.

    target_mode="positive_return" predicts whether the horizon close is above the
    observation close. target_mode="target_before_stop" predicts whether the
    dataset's configured upside barrier is reached before its downside barrier.
    """
    records = [dict(row) for row in dataset.get("records") or [] if isinstance(row, dict)]
    normalized_target_mode = str(target_mode or "").strip().lower()
    if normalized_target_mode == "positive_return":
        target = f"label__positive_return_{int(target_horizon)}bar"
        target_description = (
            f"Price closes above the observation price after {int(target_horizon)} bars."
        )
    elif normalized_target_mode == "target_before_stop":
        target = f"label__target_before_stop_{int(target_horizon)}bar"
        profit_target_pct = _number(dataset.get("profit_target_pct"))
        stop_loss_pct = _number(dataset.get("stop_loss_pct"))
        target_description = (
            f"Price reaches +{profit_target_pct:g}% before -{stop_loss_pct:g}% "
            f"within {int(target_horizon)} bars."
            if profit_target_pct is not None and stop_loss_pct is not None
            else f"Configured upside barrier is reached before the downside barrier within {int(target_horizon)} bars."
        )
    else:
        raise ValueError(
            "target_mode must be 'positive_return' or 'target_before_stop'."
        )

    feature_columns = sorted(
        column for column in (dataset.get("feature_columns") or [])
        if str(column).startswith("feature__")
    )
    if not records or not feature_columns:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No supervised rows or feature columns are available.",
            "target_mode": normalized_target_mode,
            "target": target,
        }

    frame = pd.DataFrame(records)
    if target not in frame.columns:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": f"Target {target} is not present in the dataset.",
            "target_mode": normalized_target_mode,
            "target": target,
        }
    frame = frame[frame[target].notna() & frame["session"].notna()].copy()
    if frame.empty:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No rows have both a session and target label.",
            "target_mode": normalized_target_mode,
            "target": target,
        }

    frame[target] = frame[target].astype(bool).astype(int)
    frame["_session_key"] = frame["session"].astype(str)
    frame["_time_key"] = pd.to_datetime(frame.get("timestamp"), utc=True, errors="coerce")
    frame = frame.sort_values(["_session_key", "_time_key", "symbol"], na_position="last").reset_index(drop=True)

    sessions = sorted(frame["_session_key"].unique().tolist())
    min_train_sessions = max(2, int(min_train_sessions))
    test_sessions_per_fold = max(1, int(test_sessions_per_fold))
    embargo_sessions = max(0, int(embargo_sessions))
    if len(sessions) < min_train_sessions + embargo_sessions + test_sessions_per_fold:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Not enough distinct market sessions for the requested walk-forward split.",
            "session_count": len(sessions),
            "target_mode": normalized_target_mode,
            "target": target,
        }

    numeric, categorical = _feature_types(frame, feature_columns)
    prepared = _prepare_feature_frame(frame, numeric, categorical)

    folds: list[dict[str, Any]] = []
    all_actual: list[int] = []
    all_probability: list[float] = []
    all_naive_probability: list[float] = []
    all_prediction_rows: list[dict[str, Any]] = []

    test_start = min_train_sessions + embargo_sessions
    fold_number = 0
    while test_start < len(sessions):
        test_sessions = sessions[test_start : test_start + test_sessions_per_fold]
        if not test_sessions:
            break
        train_end = max(0, test_start - embargo_sessions)
        train_sessions = sessions[:train_end]
        if len(train_sessions) < min_train_sessions:
            test_start += test_sessions_per_fold
            continue

        train_mask = prepared["_session_key"].isin(train_sessions)
        test_mask = prepared["_session_key"].isin(test_sessions)
        train = prepared.loc[train_mask]
        test = prepared.loc[test_mask]
        if len(train) < min_train_rows or test.empty or train[target].nunique() < 2:
            test_start += test_sessions_per_fold
            continue

        pipeline = _baseline_pipeline(numeric, categorical)
        pipeline.fit(train[feature_columns], train[target])
        probability = pipeline.predict_proba(test[feature_columns])[:, 1]
        actual = test[target].astype(int).tolist()
        predicted = (probability >= 0.5).astype(int)
        naive_probability = float(train[target].mean())
        naive = [naive_probability] * len(actual)

        fold_number += 1
        model_brier = float(brier_score_loss(actual, probability))
        naive_brier = float(brier_score_loss(actual, naive))
        folds.append(
            {
                "fold": fold_number,
                "train_sessions": len(train_sessions),
                "train_rows": len(train),
                "test_sessions": test_sessions,
                "test_rows": len(test),
                "train_positive_rate": naive_probability,
                "test_positive_rate": float(sum(actual) / len(actual)),
                "roc_auc": _safe_auc(actual, probability.tolist()),
                "brier_score": model_brier,
                "naive_brier_score": naive_brier,
                "brier_skill_vs_naive": None if naive_brier <= 0 else 1.0 - (model_brier / naive_brier),
                "accuracy": float(accuracy_score(actual, predicted)),
                "log_loss": float(log_loss(actual, probability, labels=[0, 1])),
            }
        )

        all_actual.extend(actual)
        all_probability.extend(float(value) for value in probability)
        all_naive_probability.extend(naive)
        for (_, row), prob in zip(test.iterrows(), probability):
            all_prediction_rows.append(
                {
                    "symbol": row.get("symbol"),
                    "session": row.get("session"),
                    "timestamp": row.get("timestamp"),
                    "actual": bool(row[target]),
                    "probability": float(prob),
                }
            )
        test_start += test_sessions_per_fold

    if not folds or not all_actual:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No walk-forward fold met the minimum training requirements.",
            "session_count": len(sessions),
            "row_count": len(frame),
            "target_mode": normalized_target_mode,
            "target": target,
        }

    model_brier = float(brier_score_loss(all_actual, all_probability))
    naive_brier = float(brier_score_loss(all_actual, all_naive_probability))
    auc = _safe_auc(all_actual, all_probability)
    return {
        "status": "EVALUATED",
        "model_type": "logistic_regression",
        "target": target,
        "target_mode": normalized_target_mode,
        "target_description": target_description,
        "target_horizon": int(target_horizon),
        "profit_target_pct": _number(dataset.get("profit_target_pct")),
        "stop_loss_pct": _number(dataset.get("stop_loss_pct")),
        "barrier_same_bar_policy": dataset.get("barrier_same_bar_policy"),
        "feature_count": len(feature_columns),
        "numeric_feature_count": len(numeric),
        "categorical_feature_count": len(categorical),
        "session_count": len(sessions),
        "fold_count": len(folds),
        "oos_rows": len(all_actual),
        "oos_positive_rate": float(sum(all_actual) / len(all_actual)),
        "roc_auc": auc,
        "brier_score": model_brier,
        "naive_brier_score": naive_brier,
        "brier_skill_vs_naive": None if naive_brier <= 0 else 1.0 - (model_brier / naive_brier),
        "accuracy": float(accuracy_score(all_actual, [int(value >= 0.5) for value in all_probability])),
        "log_loss": float(log_loss(all_actual, all_probability, labels=[0, 1])),
        "folds": folds,
        "predictions": all_prediction_rows,
        "split_policy": {
            "type": "expanding_session_walk_forward",
            "min_train_sessions": min_train_sessions,
            "test_sessions_per_fold": test_sessions_per_fold,
            "embargo_sessions": embargo_sessions,
            "min_train_rows": min_train_rows,
        },
        "note": (
            "All reported model metrics are out-of-sample. The model is a research baseline only "
            "and is not connected to live rankings or trading decisions."
        ),
    }

def leave_one_symbol_out_walk_forward_logistic_baseline(
    dataset: dict[str, Any],
    *,
    target_horizon: int = 15,
    target_mode: str = "target_before_stop",
    min_train_sessions: int = 8,
    test_sessions_per_fold: int = 2,
    embargo_sessions: int = 1,
    min_train_rows: int = 250,
    min_test_rows: int = 25,
) -> dict[str, Any]:
    """Test cross-stock transfer while preserving chronological causality.

    Each symbol is held out completely from model training. For that held-out
    symbol, predictions are made only on later market sessions; training uses
    earlier sessions from the other symbols, with the requested embargo.
    """

    records = [dict(row) for row in dataset.get("records") or [] if isinstance(row, dict)]
    normalized_target_mode = str(target_mode or "").strip().lower()
    if normalized_target_mode == "positive_return":
        target = f"label__positive_return_{int(target_horizon)}bar"
        target_description = (
            f"Price closes above the observation price after {int(target_horizon)} bars."
        )
    elif normalized_target_mode == "target_before_stop":
        target = f"label__target_before_stop_{int(target_horizon)}bar"
        profit_target_pct = _number(dataset.get("profit_target_pct"))
        stop_loss_pct = _number(dataset.get("stop_loss_pct"))
        target_description = (
            f"Price reaches +{profit_target_pct:g}% before -{stop_loss_pct:g}% "
            f"within {int(target_horizon)} bars."
            if profit_target_pct is not None and stop_loss_pct is not None
            else f"Configured upside barrier is reached before the downside barrier within {int(target_horizon)} bars."
        )
    else:
        raise ValueError("target_mode must be 'positive_return' or 'target_before_stop'.")

    feature_columns = sorted(
        column for column in (dataset.get("feature_columns") or [])
        if str(column).startswith("feature__")
    )
    if not records or not feature_columns:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No supervised rows or feature columns are available.",
            "target_mode": normalized_target_mode,
            "target": target,
        }

    frame = pd.DataFrame(records)
    required_columns = {"symbol", "session", target}
    if not required_columns.issubset(frame.columns):
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Dataset is missing symbol, session, or requested target labels.",
            "target_mode": normalized_target_mode,
            "target": target,
        }
    frame = frame[
        frame[target].notna()
        & frame["session"].notna()
        & frame["symbol"].notna()
    ].copy()
    if frame.empty:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No rows have symbol, session, and target labels.",
            "target_mode": normalized_target_mode,
            "target": target,
        }

    frame[target] = frame[target].astype(bool).astype(int)
    frame["_session_key"] = frame["session"].astype(str)
    frame["_symbol_key"] = frame["symbol"].astype(str).str.upper()
    frame["_time_key"] = pd.to_datetime(frame.get("timestamp"), utc=True, errors="coerce")
    frame = frame.sort_values(
        ["_session_key", "_time_key", "_symbol_key"],
        na_position="last",
    ).reset_index(drop=True)

    sessions = sorted(frame["_session_key"].unique().tolist())
    symbols = sorted(frame["_symbol_key"].unique().tolist())
    min_train_sessions = max(2, int(min_train_sessions))
    test_sessions_per_fold = max(1, int(test_sessions_per_fold))
    embargo_sessions = max(0, int(embargo_sessions))
    min_train_rows = max(1, int(min_train_rows))
    min_test_rows = max(1, int(min_test_rows))

    if len(symbols) < 2:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "At least two symbols are required for held-out-stock validation.",
            "symbol_count": len(symbols),
            "target_mode": normalized_target_mode,
            "target": target,
        }
    if len(sessions) < min_train_sessions + embargo_sessions + test_sessions_per_fold:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Not enough market sessions for held-out-stock walk-forward validation.",
            "session_count": len(sessions),
            "target_mode": normalized_target_mode,
            "target": target,
        }

    numeric, categorical = _feature_types(frame, feature_columns)
    prepared = _prepare_feature_frame(frame, numeric, categorical)

    symbol_reports: list[dict[str, Any]] = []
    all_actual: list[int] = []
    all_probability: list[float] = []
    all_naive_probability: list[float] = []
    all_prediction_rows: list[dict[str, Any]] = []

    for held_out_symbol in symbols:
        folds: list[dict[str, Any]] = []
        symbol_actual: list[int] = []
        symbol_probability: list[float] = []
        symbol_naive: list[float] = []
        test_start = min_train_sessions + embargo_sessions
        fold_number = 0

        while test_start < len(sessions):
            test_sessions = sessions[test_start : test_start + test_sessions_per_fold]
            if not test_sessions:
                break
            train_end = max(0, test_start - embargo_sessions)
            train_sessions = sessions[:train_end]
            if len(train_sessions) < min_train_sessions:
                test_start += test_sessions_per_fold
                continue

            train_mask = (
                prepared["_session_key"].isin(train_sessions)
                & prepared["_symbol_key"].ne(held_out_symbol)
            )
            test_mask = (
                prepared["_session_key"].isin(test_sessions)
                & prepared["_symbol_key"].eq(held_out_symbol)
            )
            train = prepared.loc[train_mask]
            test = prepared.loc[test_mask]
            if (
                len(train) < min_train_rows
                or len(test) < min_test_rows
                or train[target].nunique() < 2
            ):
                test_start += test_sessions_per_fold
                continue

            pipeline = _baseline_pipeline(numeric, categorical)
            pipeline.fit(train[feature_columns], train[target])
            probability = pipeline.predict_proba(test[feature_columns])[:, 1]
            actual = test[target].astype(int).tolist()
            predicted = (probability >= 0.5).astype(int)
            naive_probability = float(train[target].mean())
            naive = [naive_probability] * len(actual)
            model_brier = float(brier_score_loss(actual, probability))
            naive_brier = float(brier_score_loss(actual, naive))

            fold_number += 1
            folds.append(
                {
                    "fold": fold_number,
                    "held_out_symbol": held_out_symbol,
                    "train_symbols": sorted(
                        symbol for symbol in symbols if symbol != held_out_symbol
                    ),
                    "train_sessions": len(train_sessions),
                    "train_rows": len(train),
                    "test_sessions": test_sessions,
                    "test_rows": len(test),
                    "train_positive_rate": naive_probability,
                    "test_positive_rate": float(sum(actual) / len(actual)),
                    "roc_auc": _safe_auc(actual, probability.tolist()),
                    "brier_score": model_brier,
                    "naive_brier_score": naive_brier,
                    "brier_skill_vs_naive": (
                        None if naive_brier <= 0 else 1.0 - (model_brier / naive_brier)
                    ),
                    "accuracy": float(accuracy_score(actual, predicted)),
                    "log_loss": float(log_loss(actual, probability, labels=[0, 1])),
                }
            )

            symbol_actual.extend(actual)
            symbol_probability.extend(float(value) for value in probability)
            symbol_naive.extend(naive)
            all_actual.extend(actual)
            all_probability.extend(float(value) for value in probability)
            all_naive_probability.extend(naive)
            for (_, row), prob in zip(test.iterrows(), probability):
                prediction = {
                    "held_out_symbol": held_out_symbol,
                    "symbol": row.get("symbol"),
                    "session": row.get("session"),
                    "timestamp": row.get("timestamp"),
                    "actual": bool(row[target]),
                    "probability": float(prob),
                }
                all_prediction_rows.append(prediction)
            test_start += test_sessions_per_fold

        if symbol_actual:
            symbol_brier = float(brier_score_loss(symbol_actual, symbol_probability))
            symbol_naive_brier = float(brier_score_loss(symbol_actual, symbol_naive))
            symbol_reports.append(
                {
                    "symbol": held_out_symbol,
                    "status": "EVALUATED",
                    "fold_count": len(folds),
                    "oos_rows": len(symbol_actual),
                    "oos_positive_rate": float(sum(symbol_actual) / len(symbol_actual)),
                    "roc_auc": _safe_auc(symbol_actual, symbol_probability),
                    "brier_score": symbol_brier,
                    "naive_brier_score": symbol_naive_brier,
                    "brier_skill_vs_naive": (
                        None
                        if symbol_naive_brier <= 0
                        else 1.0 - (symbol_brier / symbol_naive_brier)
                    ),
                    "accuracy": float(
                        accuracy_score(
                            symbol_actual,
                            [int(value >= 0.5) for value in symbol_probability],
                        )
                    ),
                    "folds": folds,
                }
            )
        else:
            symbol_reports.append(
                {
                    "symbol": held_out_symbol,
                    "status": "INSUFFICIENT_DATA",
                    "fold_count": 0,
                    "oos_rows": 0,
                    "roc_auc": None,
                    "brier_skill_vs_naive": None,
                    "folds": [],
                }
            )

    if not all_actual:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No held-out-stock fold met the minimum train/test requirements.",
            "symbol_count": len(symbols),
            "session_count": len(sessions),
            "target_mode": normalized_target_mode,
            "target": target,
            "by_symbol": symbol_reports,
        }

    model_brier = float(brier_score_loss(all_actual, all_probability))
    naive_brier = float(brier_score_loss(all_actual, all_naive_probability))
    return {
        "status": "EVALUATED",
        "validation_type": "leave_one_symbol_out_walk_forward",
        "model_type": "logistic_regression",
        "target": target,
        "target_mode": normalized_target_mode,
        "target_description": target_description,
        "target_horizon": int(target_horizon),
        "profit_target_pct": _number(dataset.get("profit_target_pct")),
        "stop_loss_pct": _number(dataset.get("stop_loss_pct")),
        "session_mode": dataset.get("session_mode"),
        "symbol_count": len(symbols),
        "held_out_symbols": symbols,
        "session_count": len(sessions),
        "feature_count": len(feature_columns),
        "oos_rows": len(all_actual),
        "oos_positive_rate": float(sum(all_actual) / len(all_actual)),
        "roc_auc": _safe_auc(all_actual, all_probability),
        "brier_score": model_brier,
        "naive_brier_score": naive_brier,
        "brier_skill_vs_naive": (
            None if naive_brier <= 0 else 1.0 - (model_brier / naive_brier)
        ),
        "accuracy": float(
            accuracy_score(
                all_actual,
                [int(value >= 0.5) for value in all_probability],
            )
        ),
        "by_symbol": symbol_reports,
        "predictions": all_prediction_rows,
        "split_policy": {
            "type": "leave_one_symbol_out_plus_expanding_session_walk_forward",
            "held_out_symbol_never_in_training": True,
            "min_train_sessions": min_train_sessions,
            "test_sessions_per_fold": test_sessions_per_fold,
            "embargo_sessions": embargo_sessions,
            "min_train_rows": min_train_rows,
            "min_test_rows": min_test_rows,
        },
        "note": (
            "Each symbol is excluded from all training rows for its evaluation. "
            "Its predictions are also chronological: only earlier sessions from the "
            "other symbols are used for training. This is a stricter cross-stock "
            "generalization test than the ordinary walk-forward baseline."
        ),
    }

def archetype_transfer_walk_forward_logistic_baseline(
    dataset: dict[str, Any],
    *,
    target_horizon: int = 15,
    target_mode: str = "target_before_stop",
    archetype_column: str = "feature__context_archetype",
    min_train_sessions: int = 8,
    test_sessions_per_fold: int = 2,
    embargo_sessions: int = 1,
    min_train_rows: int = 200,
    min_test_rows: int = 20,
) -> dict[str, Any]:
    """Compare same-archetype vs different-archetype transfer on identical held-out rows.

    Each tested stock is absent from all model training. Training is also restricted
    to earlier market sessions. For each held-out stock/session/archetype slice, one
    model trains on other stocks from the same lagged archetype and a paired control
    trains on other archetypes. Only slices where both models have enough data are
    included in the direct comparison.
    """
    records = [dict(row) for row in dataset.get("records") or [] if isinstance(row, dict)]
    normalized_target_mode = str(target_mode or "").strip().lower()
    if normalized_target_mode == "positive_return":
        target = f"label__positive_return_{int(target_horizon)}bar"
        target_description = (
            f"Price closes above the observation price after {int(target_horizon)} bars."
        )
    elif normalized_target_mode == "target_before_stop":
        target = f"label__target_before_stop_{int(target_horizon)}bar"
        profit_target_pct = _number(dataset.get("profit_target_pct"))
        stop_loss_pct = _number(dataset.get("stop_loss_pct"))
        target_description = (
            f"Price reaches +{profit_target_pct:g}% before -{stop_loss_pct:g}% "
            f"within {int(target_horizon)} bars."
            if profit_target_pct is not None and stop_loss_pct is not None
            else f"Configured upside barrier is reached before the downside barrier within {int(target_horizon)} bars."
        )
    else:
        raise ValueError("target_mode must be 'positive_return' or 'target_before_stop'.")

    feature_columns = sorted(
        column for column in (dataset.get("feature_columns") or [])
        if str(column).startswith("feature__")
    )
    model_features = [column for column in feature_columns if column != archetype_column]
    if not records or not model_features:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No supervised rows or usable feature columns are available.",
            "target_mode": normalized_target_mode,
            "target": target,
        }

    frame = pd.DataFrame(records)
    required = {"symbol", "session", target, archetype_column}
    if not required.issubset(frame.columns):
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "Dataset is missing symbol, session, target, or archetype context.",
            "target_mode": normalized_target_mode,
            "target": target,
        }
    frame = frame[
        frame[target].notna()
        & frame["session"].notna()
        & frame["symbol"].notna()
        & frame[archetype_column].notna()
    ].copy()
    frame["_archetype_key"] = frame[archetype_column].astype(str)
    frame = frame[frame["_archetype_key"].ne("unknown")].copy()
    if frame.empty:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No rows have a usable lagged archetype yet.",
            "target_mode": normalized_target_mode,
            "target": target,
        }

    frame[target] = frame[target].astype(bool).astype(int)
    frame["_session_key"] = frame["session"].astype(str)
    frame["_symbol_key"] = frame["symbol"].astype(str).str.upper()
    frame["_time_key"] = pd.to_datetime(frame.get("timestamp"), utc=True, errors="coerce")
    frame = frame.sort_values(
        ["_session_key", "_time_key", "_symbol_key"],
        na_position="last",
    ).reset_index(drop=True)

    sessions = sorted(frame["_session_key"].unique().tolist())
    symbols = sorted(frame["_symbol_key"].unique().tolist())
    archetypes = sorted(frame["_archetype_key"].unique().tolist())
    min_train_sessions = max(2, int(min_train_sessions))
    test_sessions_per_fold = max(1, int(test_sessions_per_fold))
    embargo_sessions = max(0, int(embargo_sessions))
    min_train_rows = max(1, int(min_train_rows))
    min_test_rows = max(1, int(min_test_rows))
    if len(symbols) < 3:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "At least three symbols are required for archetype transfer validation.",
            "symbol_count": len(symbols),
            "target_mode": normalized_target_mode,
            "target": target,
        }
    if len(archetypes) < 2:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "At least two populated archetypes are required for within/across comparison.",
            "archetype_count": len(archetypes),
            "target_mode": normalized_target_mode,
            "target": target,
        }

    numeric, categorical = _feature_types(frame, model_features)
    prepared = _prepare_feature_frame(frame, numeric, categorical)

    paired_rows: list[dict[str, Any]] = []
    slice_reports: list[dict[str, Any]] = []
    test_start_initial = min_train_sessions + embargo_sessions

    for held_out_symbol in symbols:
        test_start = test_start_initial
        while test_start < len(sessions):
            test_sessions = sessions[test_start : test_start + test_sessions_per_fold]
            if not test_sessions:
                break
            train_end = max(0, test_start - embargo_sessions)
            train_sessions = sessions[:train_end]
            if len(train_sessions) < min_train_sessions:
                test_start += test_sessions_per_fold
                continue

            base_train = prepared[
                prepared["_session_key"].isin(train_sessions)
                & prepared["_symbol_key"].ne(held_out_symbol)
            ]
            held_out_test = prepared[
                prepared["_session_key"].isin(test_sessions)
                & prepared["_symbol_key"].eq(held_out_symbol)
            ]
            for archetype in sorted(held_out_test["_archetype_key"].unique().tolist()):
                test = held_out_test[held_out_test["_archetype_key"].eq(archetype)]
                within_train = base_train[base_train["_archetype_key"].eq(archetype)]
                across_train = base_train[base_train["_archetype_key"].ne(archetype)]
                if (
                    len(test) < min_test_rows
                    or len(within_train) < min_train_rows
                    or len(across_train) < min_train_rows
                    or within_train[target].nunique() < 2
                    or across_train[target].nunique() < 2
                ):
                    continue

                within_pipeline = _baseline_pipeline(numeric, categorical)
                across_pipeline = _baseline_pipeline(numeric, categorical)
                within_pipeline.fit(within_train[model_features], within_train[target])
                across_pipeline.fit(across_train[model_features], across_train[target])
                within_probability = within_pipeline.predict_proba(test[model_features])[:, 1]
                across_probability = across_pipeline.predict_proba(test[model_features])[:, 1]
                actual = test[target].astype(int).tolist()
                within_naive_probability = float(within_train[target].mean())
                across_naive_probability = float(across_train[target].mean())
                within_naive = [within_naive_probability] * len(actual)
                across_naive = [across_naive_probability] * len(actual)
                within_brier = float(brier_score_loss(actual, within_probability))
                across_brier = float(brier_score_loss(actual, across_probability))
                within_naive_brier = float(brier_score_loss(actual, within_naive))
                across_naive_brier = float(brier_score_loss(actual, across_naive))

                slice_reports.append(
                    {
                        "held_out_symbol": held_out_symbol,
                        "archetype": archetype,
                        "train_sessions": len(train_sessions),
                        "test_sessions": test_sessions,
                        "test_rows": len(test),
                        "within_train_rows": len(within_train),
                        "across_train_rows": len(across_train),
                        "within_train_symbols": sorted(
                            within_train["_symbol_key"].unique().tolist()
                        ),
                        "across_train_symbols": sorted(
                            across_train["_symbol_key"].unique().tolist()
                        ),
                        "within_roc_auc": _safe_auc(actual, within_probability.tolist()),
                        "across_roc_auc": _safe_auc(actual, across_probability.tolist()),
                        "within_brier_score": within_brier,
                        "across_brier_score": across_brier,
                        "within_brier_skill_vs_naive": (
                            None
                            if within_naive_brier <= 0
                            else 1.0 - (within_brier / within_naive_brier)
                        ),
                        "across_brier_skill_vs_naive": (
                            None
                            if across_naive_brier <= 0
                            else 1.0 - (across_brier / across_naive_brier)
                        ),
                    }
                )
                for (_, row), within_prob, across_prob, within_nv, across_nv in zip(
                    test.iterrows(),
                    within_probability,
                    across_probability,
                    within_naive,
                    across_naive,
                ):
                    paired_rows.append(
                        {
                            "held_out_symbol": held_out_symbol,
                            "archetype": archetype,
                            "session": row.get("session"),
                            "timestamp": row.get("timestamp"),
                            "actual": int(row[target]),
                            "within_probability": float(within_prob),
                            "across_probability": float(across_prob),
                            "within_naive_probability": float(within_nv),
                            "across_naive_probability": float(across_nv),
                        }
                    )
            test_start += test_sessions_per_fold

    if not paired_rows:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "No paired same-archetype/across-archetype slice met the minimum requirements.",
            "symbol_count": len(symbols),
            "archetype_count": len(archetypes),
            "target_mode": normalized_target_mode,
            "target": target,
        }

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        actual = [int(row["actual"]) for row in rows]
        within_probability = [float(row["within_probability"]) for row in rows]
        across_probability = [float(row["across_probability"]) for row in rows]
        within_naive = [float(row["within_naive_probability"]) for row in rows]
        across_naive = [float(row["across_naive_probability"]) for row in rows]
        within_brier = float(brier_score_loss(actual, within_probability))
        across_brier = float(brier_score_loss(actual, across_probability))
        within_naive_brier = float(brier_score_loss(actual, within_naive))
        across_naive_brier = float(brier_score_loss(actual, across_naive))
        within_auc = _safe_auc(actual, within_probability)
        across_auc = _safe_auc(actual, across_probability)
        return {
            "oos_rows": len(rows),
            "positive_rate": float(sum(actual) / len(actual)),
            "within_roc_auc": within_auc,
            "across_roc_auc": across_auc,
            "within_minus_across_auc": (
                None if within_auc is None or across_auc is None else within_auc - across_auc
            ),
            "within_brier_score": within_brier,
            "across_brier_score": across_brier,
            "within_minus_across_brier": within_brier - across_brier,
            "within_brier_skill_vs_naive": (
                None
                if within_naive_brier <= 0
                else 1.0 - (within_brier / within_naive_brier)
            ),
            "across_brier_skill_vs_naive": (
                None
                if across_naive_brier <= 0
                else 1.0 - (across_brier / across_naive_brier)
            ),
        }

    by_symbol: list[dict[str, Any]] = []
    for symbol in sorted({str(row["held_out_symbol"]) for row in paired_rows}):
        subset = [row for row in paired_rows if row["held_out_symbol"] == symbol]
        item = {"symbol": symbol}
        item.update(summarize(subset))
        by_symbol.append(item)

    by_archetype: list[dict[str, Any]] = []
    for archetype in sorted({str(row["archetype"]) for row in paired_rows}):
        subset = [row for row in paired_rows if row["archetype"] == archetype]
        item = {"archetype": archetype}
        item.update(summarize(subset))
        by_archetype.append(item)

    overall = summarize(paired_rows)
    return {
        "status": "EVALUATED",
        "validation_type": "held_out_symbol_within_vs_across_archetype_walk_forward",
        "model_type": "logistic_regression",
        "target": target,
        "target_mode": normalized_target_mode,
        "target_description": target_description,
        "target_horizon": int(target_horizon),
        "archetype_column": archetype_column,
        "archetypes": archetypes,
        "symbol_count": len(symbols),
        "session_count": len(sessions),
        "feature_count": len(model_features),
        "paired_oos_rows": overall["oos_rows"],
        "within_roc_auc": overall["within_roc_auc"],
        "across_roc_auc": overall["across_roc_auc"],
        "within_minus_across_auc": overall["within_minus_across_auc"],
        "within_brier_score": overall["within_brier_score"],
        "across_brier_score": overall["across_brier_score"],
        "within_minus_across_brier": overall["within_minus_across_brier"],
        "within_brier_skill_vs_naive": overall["within_brier_skill_vs_naive"],
        "across_brier_skill_vs_naive": overall["across_brier_skill_vs_naive"],
        "by_symbol": by_symbol,
        "by_archetype": by_archetype,
        "slices": slice_reports,
        "predictions": paired_rows,
        "split_policy": {
            "type": "held_out_symbol_plus_time_forward_within_vs_across_archetype",
            "held_out_symbol_never_in_training": True,
            "same_test_rows_for_within_and_across": True,
            "archetype_uses_completed_prior_sessions": True,
            "min_train_sessions": min_train_sessions,
            "test_sessions_per_fold": test_sessions_per_fold,
            "embargo_sessions": embargo_sessions,
            "min_train_rows_per_side": min_train_rows,
            "min_test_rows_per_slice": min_test_rows,
        },
        "note": (
            "Same-archetype and across-archetype models are evaluated on identical held-out-stock rows. "
            "The archetype family itself is removed from model inputs for this paired comparison; it is "
            "used only to choose the training cohort. Context subfeatures remain available to the model."
        ),
    }

