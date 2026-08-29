from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import json

from predictive_ml_pipeline import (
    build_cross_stock_training_dataset,
    leave_one_symbol_out_walk_forward_logistic_baseline,
    similarity_weighted_leave_one_symbol_out_walk_forward_logistic_baseline,
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
OBSERVATION_STRIDE_BARS = 5


def compact_generalization(report: dict) -> dict:
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


def compact_similarity(report: dict) -> dict:
    output = {
        key: report.get(key)
        for key in (
            "status", "reason", "validation_type", "target_description",
            "symbol_count", "session_count", "feature_count", "paired_oos_rows",
            "baseline_roc_auc", "similarity_roc_auc",
            "similarity_minus_baseline_auc", "baseline_brier_score",
            "similarity_brier_score", "similarity_minus_baseline_brier",
            "naive_brier_score", "baseline_brier_skill_vs_naive",
            "similarity_brier_skill_vs_naive", "similarity_columns",
            "similarity_bandwidth", "minimum_similarity_weight", "split_policy",
        )
        if key in report
    }
    output["by_symbol"] = report.get("by_symbol") or []

    peer_accumulator: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for item in report.get("slices") or []:
        held_out = str(item.get("held_out_symbol") or "")
        if not held_out:
            continue
        for peer in item.get("top_similar_symbols") or []:
            symbol = str(peer.get("symbol") or "")
            weight = peer.get("mean_training_weight")
            if symbol and isinstance(weight, (int, float)):
                peer_accumulator[held_out][symbol].append(float(weight))

    peer_summary = {}
    for held_out, peers in peer_accumulator.items():
        ranked = sorted(
            (
                {
                    "symbol": symbol,
                    "mean_weight": sum(weights) / len(weights),
                    "appearances": len(weights),
                }
                for symbol, weights in peers.items()
            ),
            key=lambda item: (-item["mean_weight"], -item["appearances"], item["symbol"]),
        )
        peer_summary[held_out] = ranked[:8]
    output["top_similar_peers_by_held_out_symbol"] = peer_summary
    output["slice_count"] = len(report.get("slices") or [])
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
        horizons=(TARGET_HORIZON,),
        swing_radius=3,
        max_pages=160,
        require_full_horizon=True,
        session_limit=TRADING_DAYS,
        profit_target_pct=PROFIT_TARGET_PCT,
        stop_loss_pct=STOP_LOSS_PCT,
        session_mode=SESSION_MODE,
        observation_stride_bars=OBSERVATION_STRIDE_BARS,
        progress=progress,
    )

    held_out = leave_one_symbol_out_walk_forward_logistic_baseline(
        dataset,
        target_horizon=TARGET_HORIZON,
        target_mode="target_before_stop",
        min_train_sessions=8,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=250,
        min_test_rows=20,
    )

    similarity = similarity_weighted_leave_one_symbol_out_walk_forward_logistic_baseline(
        dataset,
        target_horizon=TARGET_HORIZON,
        target_mode="target_before_stop",
        similarity_bandwidth=0.75,
        minimum_similarity_weight=0.03,
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
        "observation_stride_bars": OBSERVATION_STRIDE_BARS,
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
            "by_symbol": dataset.get("by_symbol") or [],
        },
        "held_out_stock_walk_forward": compact_generalization(held_out),
        "continuous_similarity_transfer": compact_similarity(similarity),
    }

    with open("continuous_similarity_experiment_result.json", "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")

    print("CONTINUOUS_SIMILARITY_EXPERIMENT_RESULT", flush=True)
    print(json.dumps(result, indent=2, sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
