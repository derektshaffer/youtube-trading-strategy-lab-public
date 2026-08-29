from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import json

from predictive_ml_pipeline import (
    build_cross_stock_training_dataset,
    leave_one_symbol_out_walk_forward_logistic_baseline,
    walk_forward_logistic_baseline,
)
from trading_app_runtime import market_client
from youtube_strategy_engine import utc_now


SYMBOLS = ["SDOT", "RR", "KULR", "FCEL", "ACHR"]
TRADING_DAYS = 20
TARGET_HORIZON = 15
PROFIT_TARGET_PCT = 1.0
STOP_LOSS_PCT = 0.75
SESSION_MODE = "regular"


def compact(report: dict) -> dict:
    keep = (
        "status", "reason", "validation_type", "model_type", "target",
        "target_mode", "target_description", "target_horizon",
        "profit_target_pct", "stop_loss_pct", "session_mode",
        "symbol_count", "held_out_symbols", "session_count", "feature_count",
        "numeric_feature_count", "categorical_feature_count", "fold_count",
        "oos_rows", "oos_positive_rate", "roc_auc", "brier_score",
        "naive_brier_score", "brier_skill_vs_naive", "accuracy", "log_loss",
        "split_policy",
    )
    out = {key: report.get(key) for key in keep if key in report}
    if "by_symbol" in report:
        out["by_symbol"] = [
            {
                key: item.get(key)
                for key in (
                    "symbol", "status", "fold_count", "oos_rows",
                    "oos_positive_rate", "roc_auc", "brier_score",
                    "naive_brier_score", "brier_skill_vs_naive", "accuracy",
                )
            }
            for item in report.get("by_symbol") or []
        ]
    if "folds" in report:
        out["folds"] = report.get("folds") or []
    return out


def target_rates(dataset: dict, label: str) -> list[dict]:
    counts = defaultdict(lambda: [0, 0])
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
    end = utc_now()
    if str(getattr(market, "historical_feed", "sip")).lower() == "sip":
        end -= timedelta(minutes=16)
    else:
        end -= timedelta(minutes=1)
    start = end - timedelta(days=max(20, TRADING_DAYS * 2 + 5))

    def progress(message: str) -> None:
        print("PROGRESS", message, flush=True)

    dataset = build_cross_stock_training_dataset(
        market,
        SYMBOLS,
        start=start,
        end=end,
        timeframe="1Min",
        horizons=(5, 15, 30),
        swing_radius=3,
        max_pages=120,
        require_full_horizon=True,
        session_limit=TRADING_DAYS,
        profit_target_pct=PROFIT_TARGET_PCT,
        stop_loss_pct=STOP_LOSS_PCT,
        session_mode=SESSION_MODE,
        progress=progress,
    )

    standard = walk_forward_logistic_baseline(
        dataset,
        target_horizon=TARGET_HORIZON,
        target_mode="target_before_stop",
        min_train_sessions=8,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=250,
    )
    held_out = leave_one_symbol_out_walk_forward_logistic_baseline(
        dataset,
        target_horizon=TARGET_HORIZON,
        target_mode="target_before_stop",
        min_train_sessions=8,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=250,
        min_test_rows=25,
    )

    target_label = f"label__target_before_stop_{TARGET_HORIZON}bar"
    result = {
        "symbols": SYMBOLS,
        "trading_days_requested": TRADING_DAYS,
        "target_horizon_minutes": TARGET_HORIZON,
        "profit_target_pct": PROFIT_TARGET_PCT,
        "stop_loss_pct": STOP_LOSS_PCT,
        "session_mode": SESSION_MODE,
        "window_start": str(start),
        "window_end": str(end),
        "dataset": {
            "bars_loaded": dataset.get("bars_loaded"),
            "bars_analyzed": dataset.get("bars_analyzed"),
            "row_count": dataset.get("row_count"),
            "symbols_with_data": dataset.get("symbols_with_data"),
            "market_sessions_observed": dataset.get("market_sessions_observed"),
            "market_session_dates": dataset.get("market_session_dates"),
            "feature_count": len(dataset.get("feature_columns") or []),
            "session_window_et": dataset.get("session_window_et"),
            "by_symbol": dataset.get("by_symbol") or [],
            "trade_quality_rate_by_symbol": target_rates(dataset, target_label),
        },
        "standard_walk_forward": compact(standard),
        "held_out_stock_walk_forward": compact(held_out),
    }

    with open("regular_heldout_ml_result.json", "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")

    print("REGULAR_HELDOUT_ML_RESULT", flush=True)
    print(json.dumps(result, indent=2, sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
