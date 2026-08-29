from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import json

from predictive_ml_pipeline import (
    build_cross_stock_training_dataset,
    walk_forward_logistic_baseline,
)
from trading_app_runtime import market_client
from youtube_strategy_engine import safe_float, utc_now


SYMBOLS = ["SDOT", "RR", "KULR", "FCEL", "ACHR"]
TRADING_DAYS = 20
TARGET_HORIZON = 15
PROFIT_TARGET_PCT = 1.0
STOP_LOSS_PCT = 0.75


def _compact_evaluation(report: dict) -> dict:
    keys = (
        "status",
        "reason",
        "model_type",
        "target",
        "target_mode",
        "target_description",
        "target_horizon",
        "profit_target_pct",
        "stop_loss_pct",
        "feature_count",
        "numeric_feature_count",
        "categorical_feature_count",
        "session_count",
        "fold_count",
        "oos_rows",
        "oos_positive_rate",
        "roc_auc",
        "brier_score",
        "naive_brier_score",
        "brier_skill_vs_naive",
        "accuracy",
        "log_loss",
        "split_policy",
    )
    result = {key: report.get(key) for key in keys if key in report}
    result["folds"] = report.get("folds") or []
    return result


def _symbol_target_rates(dataset: dict, label: str) -> list[dict]:
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in dataset.get("records") or []:
        symbol = str(row.get("symbol") or "")
        value = row.get(label)
        if not symbol or value is None:
            continue
        counts[symbol][0] += 1
        counts[symbol][1] += int(bool(value))
    return [
        {
            "symbol": symbol,
            "labeled_rows": total,
            "positive_rows": positive,
            "positive_rate": positive / total if total else None,
        }
        for symbol, (total, positive) in sorted(counts.items())
    ]


def main() -> None:
    market = market_client()
    ml_end = utc_now()
    historical_feed = str(getattr(market, "historical_feed", "sip")).lower()
    if historical_feed == "sip":
        ml_end -= timedelta(minutes=16)
    else:
        ml_end -= timedelta(minutes=1)

    ml_start = ml_end - timedelta(days=max(20, TRADING_DAYS * 2 + 5))
    dataset = build_cross_stock_training_dataset(
        market,
        SYMBOLS,
        start=ml_start,
        end=ml_end,
        timeframe="1Min",
        horizons=(5, 15, 30),
        swing_radius=3,
        max_pages=120,
        require_full_horizon=True,
        session_limit=TRADING_DAYS,
        profit_target_pct=PROFIT_TARGET_PCT,
        stop_loss_pct=STOP_LOSS_PCT,
    )

    trade_quality = walk_forward_logistic_baseline(
        dataset,
        target_horizon=TARGET_HORIZON,
        target_mode="target_before_stop",
        min_train_sessions=8,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=250,
    )
    positive_return = walk_forward_logistic_baseline(
        dataset,
        target_horizon=TARGET_HORIZON,
        target_mode="positive_return",
        min_train_sessions=8,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=250,
    )

    target_label = f"label__target_before_stop_{TARGET_HORIZON}bar"
    result = {
        "symbols": SYMBOLS,
        "trading_days_requested": TRADING_DAYS,
        "target_horizon_minutes": TARGET_HORIZON,
        "profit_target_pct": PROFIT_TARGET_PCT,
        "stop_loss_pct": STOP_LOSS_PCT,
        "historical_feed": historical_feed,
        "window_start": str(ml_start),
        "window_end": str(ml_end),
        "dataset": {
            "row_count": dataset.get("row_count"),
            "bars_analyzed": dataset.get("bars_analyzed"),
            "symbols_with_data": dataset.get("symbols_with_data"),
            "sessions_analyzed": dataset.get("sessions_analyzed"),
            "market_sessions_observed": dataset.get("market_sessions_observed"),
            "market_session_dates": dataset.get("market_session_dates"),
            "feature_count": len(dataset.get("feature_columns") or []),
            "label_count": len(dataset.get("label_columns") or []),
            "by_symbol": dataset.get("by_symbol") or [],
            "trade_quality_rate_by_symbol": _symbol_target_rates(dataset, target_label),
        },
        "trade_quality_evaluation": _compact_evaluation(trade_quality),
        "positive_return_control": _compact_evaluation(positive_return),
    }
    with open("predictive_ml_experiment_result.json", "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    print("PREDICTIVE_ML_EXPERIMENT_RESULT")
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
