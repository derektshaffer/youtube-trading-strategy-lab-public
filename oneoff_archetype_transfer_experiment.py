from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
import json

from predictive_ml_pipeline import (
    archetype_transfer_walk_forward_logistic_baseline,
    build_cross_stock_training_dataset,
    leave_one_symbol_out_walk_forward_logistic_baseline,
)
from trading_app_runtime import market_client
from youtube_strategy_engine import utc_now


SYMBOLS = [
    "SDOT", "RR", "KULR", "FCEL", "ACHR",
    "JOBY", "PLUG", "SOUN", "RGTI", "QBTS",
    "LUNR", "SPCE", "BBAI", "IONQ", "RKLB",
    "OPEN", "MARA", "RIOT", "BTBT", "ONDS",
]
TRADING_DAYS = 15
TARGET_HORIZON = 15
PROFIT_TARGET_PCT = 1.0
STOP_LOSS_PCT = 0.75
SESSION_MODE = "regular"


def compact_held_out(report: dict) -> dict:
    return {
        key: report.get(key)
        for key in (
            "status", "reason", "validation_type", "target_description",
            "symbol_count", "session_count", "feature_count", "oos_rows",
            "oos_positive_rate", "roc_auc", "brier_score", "naive_brier_score",
            "brier_skill_vs_naive", "accuracy", "split_policy",
        )
        if key in report
    } | {
        "by_symbol": [
            {
                key: item.get(key)
                for key in (
                    "symbol", "status", "fold_count", "oos_rows",
                    "oos_positive_rate", "roc_auc", "brier_skill_vs_naive",
                    "accuracy",
                )
            }
            for item in report.get("by_symbol") or []
        ]
    }


def compact_archetype(report: dict) -> dict:
    return {
        key: report.get(key)
        for key in (
            "status", "reason", "validation_type", "target_description",
            "symbol_count", "session_count", "feature_count", "paired_oos_rows",
            "within_roc_auc", "across_roc_auc", "within_minus_across_auc",
            "within_brier_score", "across_brier_score", "within_minus_across_brier",
            "within_brier_skill_vs_naive", "across_brier_skill_vs_naive",
            "archetypes", "split_policy",
        )
        if key in report
    } | {
        "by_symbol": report.get("by_symbol") or [],
        "by_archetype": report.get("by_archetype") or [],
        "slice_count": len(report.get("slices") or []),
    }


def symbol_archetypes(dataset: dict) -> list[dict]:
    counts: dict[str, Counter] = defaultdict(Counter)
    for row in dataset.get("records") or []:
        symbol = str(row.get("symbol") or "")
        archetype = str(row.get("feature__context_archetype") or "unknown")
        if symbol:
            counts[symbol][archetype] += 1
    output = []
    for symbol in sorted(counts):
        populated = [(name, count) for name, count in counts[symbol].items() if name != "unknown"]
        populated.sort(key=lambda item: (-item[1], item[0]))
        output.append({
            "symbol": symbol,
            "dominant_archetype": populated[0][0] if populated else "unknown",
            "dominant_rows": populated[0][1] if populated else 0,
            "archetype_rows": dict(counts[symbol]),
        })
    return output


def main() -> None:
    market = market_client()
    end = utc_now()
    if str(getattr(market, "historical_feed", "sip")).lower() == "sip":
        end -= timedelta(minutes=16)
    else:
        end -= timedelta(minutes=1)
    start = end - timedelta(days=max(30, TRADING_DAYS * 2 + 7))

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
        max_pages=160,
        require_full_horizon=True,
        session_limit=TRADING_DAYS,
        profit_target_pct=PROFIT_TARGET_PCT,
        stop_loss_pct=STOP_LOSS_PCT,
        session_mode=SESSION_MODE,
        progress=progress,
    )

    held_out = leave_one_symbol_out_walk_forward_logistic_baseline(
        dataset,
        target_horizon=TARGET_HORIZON,
        target_mode="target_before_stop",
        min_train_sessions=8,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=500,
        min_test_rows=25,
    )
    archetype = archetype_transfer_walk_forward_logistic_baseline(
        dataset,
        target_horizon=TARGET_HORIZON,
        target_mode="target_before_stop",
        min_train_sessions=8,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=250,
        min_test_rows=20,
    )

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
            "feature_count": len(dataset.get("feature_columns") or []),
            "context_feature_count": len(dataset.get("context_feature_columns") or []),
            "archetype_distribution": dataset.get("archetype_distribution") or [],
            "dominant_archetype_by_symbol": symbol_archetypes(dataset),
            "by_symbol": dataset.get("by_symbol") or [],
        },
        "held_out_stock_walk_forward": compact_held_out(held_out),
        "archetype_transfer": compact_archetype(archetype),
    }

    with open("archetype_transfer_experiment_result.json", "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")

    print("ARCHETYPE_TRANSFER_EXPERIMENT_RESULT", flush=True)
    print(json.dumps(result, indent=2, sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
