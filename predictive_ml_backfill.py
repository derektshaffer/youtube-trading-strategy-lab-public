"""Automatic historical backfill and probability-model training.

This module reuses the Trading Lab's causal historical replay and validation stack
so the cloud worker can bootstrap/retrain probabilistic models without a browser
session. Results stay research-only; passing models are only eligible for shadow
probability display.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from time import perf_counter
from typing import Any, Callable

from predictive_ml_pipeline import (
    build_cross_stock_training_dataset,
    leave_one_symbol_out_walk_forward_logistic_baseline,
    similarity_weighted_leave_one_symbol_out_walk_forward_logistic_baseline,
    ticker_specific_walk_forward_logistic_baseline,
    walk_forward_logistic_baseline,
)
from predictive_probability_model import build_portable_probability_model
from predictive_boosted_probability_model import build_boosted_probability_model
from predictive_model_head_to_head import build_historical_model_head_to_head
from predictive_learning_router import build_stock_learning_router


DEFAULT_BOOTSTRAP_SYMBOLS: tuple[str, ...] = (
    "SDOT",
    "RR",
    "KULR",
    "FCEL",
    "ACHR",
    "REAX",
    "LUCY",
    "SOUN",
    "BBAI",
    "RCAT",
    "SERV",
    "QBTS",
    "IONQ",
    "RGTI",
    "JOBY",
    "ASTS",
    "RKLB",
    "OPEN",
    "APLD",
    "CLSK",
    "MARA",
    "MVST",
    "OPTT",
    "LAES",
)
DEFAULT_TRADING_DAYS = 45
DEFAULT_HORIZON_MINUTES = 15
DEFAULT_HORIZONS_MINUTES: tuple[int, ...] = (5, 15, 30, 60)
DEFAULT_PROFIT_TARGET_PCT = 1.0
DEFAULT_STOP_LOSS_PCT = 0.75
DEFAULT_SESSION_MODE = "regular"
DEFAULT_OBSERVATION_STRIDE_BARS = 5
DEFAULT_MAX_SYMBOLS = 24
DEFAULT_SIMILARITY_SYMBOLS = 10
DEFAULT_TICKER_SPECIFIC_SYMBOLS = 6
DEFAULT_FEATURE_WORKERS = 4
DEFAULT_VALIDATION_WORKERS = 4
MAX_AUTOMATIC_ML_RUN_HISTORY = 12
MODEL_SUITE_VERSION = 6
ML_CHECKPOINT_VERSION = 1


def _clean_symbols(values: Any) -> list[str]:
    if isinstance(values, str):
        raw = values.replace(",", " ").split()
    elif isinstance(values, (list, tuple, set)):
        raw = list(values)
    else:
        raw = []
    output: list[str] = []
    for value in raw:
        symbol = str(value or "").strip().upper()
        if symbol and symbol not in output:
            output.append(symbol)
    return output


def _clean_horizons(values: Any) -> list[int]:
    if isinstance(values, str):
        raw = values.replace(",", " ").split()
    elif isinstance(values, (list, tuple, set)):
        raw = list(values)
    else:
        raw = []
    output: list[int] = []
    for value in raw:
        try:
            horizon = max(1, min(120, int(value)))
        except (TypeError, ValueError):
            continue
        if horizon not in output:
            output.append(horizon)
    return sorted(output)


def _spread_symbol_subset(
    symbols: list[str],
    maximum: int = DEFAULT_SIMILARITY_SYMBOLS,
) -> list[str]:
    """Choose a deterministic spread across the full training universe."""
    clean = _clean_symbols(symbols)
    limit = max(3, min(len(clean), int(maximum))) if clean else 0
    if limit >= len(clean):
        return clean
    if limit <= 1:
        return clean[:limit]
    indexes = {
        round(index * (len(clean) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [clean[index] for index in sorted(indexes)][:limit]


def _priority_spread_symbol_subset(
    symbols: list[str],
    *,
    maximum: int = DEFAULT_TICKER_SPECIFIC_SYMBOLS,
    priority: Any = ("SDOT",),
) -> list[str]:
    """Keep priority tickers, then spread remaining slots across the universe."""
    clean = _clean_symbols(symbols)
    if not clean:
        return []
    limit = max(1, min(len(clean), int(maximum)))
    priority_symbols = [
        symbol for symbol in _clean_symbols(priority)
        if symbol in clean
    ]
    selected = priority_symbols[:limit]
    remaining = [symbol for symbol in clean if symbol not in selected]
    slots = limit - len(selected)
    if slots <= 0:
        return selected
    if slots >= len(remaining):
        return [*selected, *remaining]

    if slots == 1:
        indexes = {round((len(remaining) - 1) / 2)}
    else:
        indexes = {
            round(index * (len(remaining) - 1) / (slots - 1))
            for index in range(slots)
        }
    selected.extend(remaining[index] for index in sorted(indexes))
    return selected[:limit]


def _dataset_for_symbols(
    dataset: dict[str, Any],
    symbols: list[str],
) -> dict[str, Any]:
    allowed = set(_clean_symbols(symbols))
    # Validation functions construct their own DataFrames/copies, so this bounded
    # view can safely share immutable metadata and row dictionaries.
    subset = {
        key: value
        for key, value in dataset.items()
        if key != "records"
    }
    subset["records"] = [
        row
        for row in dataset.get("records") or []
        if isinstance(row, dict)
        and str(row.get("symbol") or "").strip().upper() in allowed
    ]
    subset["row_count"] = len(subset["records"])
    subset["symbols_requested"] = len(allowed)
    subset["symbols_with_data"] = len(
        {
            str(row.get("symbol") or "").strip().upper()
            for row in subset["records"]
            if str(row.get("symbol") or "").strip()
        }
    )
    return subset


def choose_backfill_symbols(
    library: dict[str, Any],
    *,
    configured_symbols: Any = None,
    maximum: int = DEFAULT_MAX_SYMBOLS,
) -> list[str]:
    """Choose a deterministic, bounded bootstrap universe.

    Recent real scanner/analyzer observations get first priority, then symbols from
    prior ML runs, then stable bootstrap anchors. This avoids waiting for months of
    live data while still letting the universe migrate toward stocks the Lab is
    actually seeing.
    """
    limit = max(5, int(maximum))
    selected: list[str] = []

    research_system = (
        library.get("research_system")
        if isinstance(library.get("research_system"), dict)
        else {}
    )
    observations = [
        item
        for item in research_system.get("live_learning_observations") or []
        if isinstance(item, dict)
    ]
    observations.sort(
        key=lambda item: str(item.get("observed_at") or ""),
        reverse=True,
    )
    recent_seen: list[str] = []
    for item in observations:
        symbol = str(item.get("symbol") or "").strip().upper()
        if symbol and symbol not in recent_seen:
            recent_seen.append(symbol)

    prior_symbols: list[str] = []
    runs = [
        item
        for item in library.get("predictive_ml_runs") or []
        if isinstance(item, dict)
    ]
    runs.sort(key=lambda item: str(item.get("completed_at") or ""), reverse=True)
    for run in runs[:4]:
        for symbol in _clean_symbols(run.get("symbols") or []):
            if symbol not in prior_symbols:
                prior_symbols.append(symbol)

    configured = _clean_symbols(configured_symbols)
    # Explicit configuration defines the anchor pool but recent real observations
    # still lead, so the model adapts toward what the scanner is actually seeing.
    anchors = configured or list(DEFAULT_BOOTSTRAP_SYMBOLS)
    for symbol in [*recent_seen, *prior_symbols, *anchors]:
        if symbol not in selected:
            selected.append(symbol)
        if len(selected) >= limit:
            break
    return selected


def build_backfill_configuration(
    library: dict[str, Any],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(payload or {})
    symbols = choose_backfill_symbols(
        library,
        configured_symbols=payload.get("symbols"),
        maximum=int(payload.get("max_symbols") or DEFAULT_MAX_SYMBOLS),
    )
    primary_horizon = max(
        5,
        min(60, int(payload.get("horizon") or DEFAULT_HORIZON_MINUTES)),
    )
    horizons = _clean_horizons(payload.get("horizons"))
    if not horizons:
        horizons = list(DEFAULT_HORIZONS_MINUTES)
    if primary_horizon not in horizons:
        horizons.append(primary_horizon)
        horizons.sort()
    return {
        "symbols": symbols,
        "trading_days": max(
            12,
            min(90, int(payload.get("trading_days") or DEFAULT_TRADING_DAYS)),
        ),
        "horizon": primary_horizon,
        "horizons": horizons,
        "profit_target_pct": max(
            0.1,
            float(payload.get("profit_target_pct") or DEFAULT_PROFIT_TARGET_PCT),
        ),
        "stop_loss_pct": max(
            0.1,
            float(payload.get("stop_loss_pct") or DEFAULT_STOP_LOSS_PCT),
        ),
        "session_mode": str(
            payload.get("session_mode") or DEFAULT_SESSION_MODE
        ).strip().lower(),
        "observation_stride_bars": max(
            1,
            min(
                30,
                int(
                    payload.get("observation_stride_bars")
                    or DEFAULT_OBSERVATION_STRIDE_BARS
                ),
            ),
        ),
        "feature_workers": max(
            1,
            min(
                int(payload.get("feature_workers") or DEFAULT_FEATURE_WORKERS),
                max(1, int(os.cpu_count() or 1)),
            ),
        ),
        "validation_workers": max(
            1,
            min(
                int(payload.get("validation_workers") or DEFAULT_VALIDATION_WORKERS),
                max(1, int(os.cpu_count() or 1)),
            ),
        ),
    }


def _compact(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in (report or {}).items()
        if key != "predictions"
    }


def _dataset_fingerprint(dataset: dict[str, Any]) -> str:
    """Hash the exact supervised dataset so stale checkpoints cannot be reused."""
    digest = hashlib.sha256()
    metadata = {key: value for key, value in dataset.items() if key != "records"}
    digest.update(
        json.dumps(
            metadata,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            allow_nan=False,
        ).encode("utf-8")
    )
    digest.update(b"\n")
    for record in dataset.get("records") or []:
        digest.update(
            json.dumps(
                record,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
                allow_nan=False,
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _checkpoint_stages(
    checkpoint: dict[str, Any] | None,
    *,
    dataset_fingerprint: str,
    model_suite_version: int,
    code_fingerprint: str,
) -> dict[str, Any]:
    """Return reusable stages only when data, suite, and code all match exactly."""
    if not isinstance(checkpoint, dict):
        return {}
    if int(checkpoint.get("checkpoint_version") or 0) != ML_CHECKPOINT_VERSION:
        return {}
    if str(checkpoint.get("dataset_fingerprint") or "") != str(dataset_fingerprint):
        return {}
    if int(checkpoint.get("model_suite_version") or 0) != int(model_suite_version):
        return {}
    expected_code = str(code_fingerprint or "").strip()
    checkpoint_code = str(checkpoint.get("code_fingerprint") or "").strip()
    # No source fingerprint means no durable reuse: conservative by default.
    if not expected_code or checkpoint_code != expected_code:
        return {}
    stages = checkpoint.get("stages")
    return deepcopy(stages) if isinstance(stages, dict) else {}


def run_predictive_ml_backfill(
    market: Any,
    library: dict[str, Any],
    *,
    payload: dict[str, Any] | None = None,
    now: datetime | None = None,
    progress: Callable[[str], None] | None = None,
    checkpoint: dict[str, Any] | None = None,
    checkpoint_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run one batched historical replay -> validation -> portable-model cycle."""
    payload = dict(payload or {})
    config = build_backfill_configuration(library, payload)
    symbols = list(config["symbols"])
    if len(symbols) < 3:
        raise ValueError("Automatic ML backfill needs at least three symbols.")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    end = current
    if str(getattr(market, "historical_feed", "sip")).lower() == "sip":
        end -= timedelta(minutes=16)
    else:
        end -= timedelta(minutes=1)

    trading_days = int(config["trading_days"])
    calendar_lookback_days = max(35, trading_days * 2 + 10)
    start = end - timedelta(days=calendar_lookback_days)

    def notify(message: str) -> None:
        if progress:
            progress(message)

    run_started = perf_counter()
    notify(
        f"Backfilling {len(symbols)} stocks across {trading_days} trading days "
        f"for a {int(config['horizon'])}-minute probability target "
        f"with {int(config['feature_workers'])} feature worker(s)."
    )
    dataset_started = perf_counter()
    dataset = build_cross_stock_training_dataset(
        market,
        symbols,
        start=start,
        end=end,
        timeframe="1Min",
        horizons=tuple(int(value) for value in config["horizons"]),
        swing_radius=3,
        max_pages=300,
        require_full_horizon=True,
        session_limit=trading_days,
        profit_target_pct=float(config["profit_target_pct"]),
        stop_loss_pct=float(config["stop_loss_pct"]),
        session_mode=str(config["session_mode"]),
        observation_stride_bars=int(config["observation_stride_bars"]),
        feature_workers=int(config["feature_workers"]),
        progress=progress,
    )
    notify(
        f"Historical dataset ready in {perf_counter() - dataset_started:.1f}s "
        f"with {int(dataset.get('row_count') or 0):,} labeled rows."
    )

    model_suite_version = int(payload.get("model_suite_version") or MODEL_SUITE_VERSION)
    code_fingerprint = str(payload.get("code_fingerprint") or "").strip()
    fingerprint_started = perf_counter()
    dataset_fingerprint = _dataset_fingerprint(dataset)
    notify(
        f"Verified dataset fingerprint {dataset_fingerprint[:12]}… "
        f"in {perf_counter() - fingerprint_started:.1f}s."
    )
    reusable_stages = _checkpoint_stages(
        checkpoint,
        dataset_fingerprint=dataset_fingerprint,
        model_suite_version=model_suite_version,
        code_fingerprint=code_fingerprint,
    )
    checkpoint_state: dict[str, Any] = {
        "checkpoint_version": ML_CHECKPOINT_VERSION,
        "model_suite_version": model_suite_version,
        "code_fingerprint": code_fingerprint,
        "dataset_fingerprint": dataset_fingerprint,
        "dataset_summary": {
            key: deepcopy(value)
            for key, value in dataset.items()
            if key != "records"
        },
        "research_start": start.isoformat(),
        "research_end": end.isoformat(),
        "stages": deepcopy(reusable_stages),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    def persist_stage(stage: str, value: Any) -> None:
        checkpoint_state["stages"][stage] = deepcopy(value)
        checkpoint_state["last_completed_stage"] = stage
        checkpoint_state["updated_at"] = datetime.now(timezone.utc).isoformat()
        if checkpoint_callback:
            checkpoint_callback(deepcopy(checkpoint_state))

    if checkpoint_callback:
        checkpoint_callback(deepcopy(checkpoint_state))
    if reusable_stages:
        notify(
            "Matched a durable checkpoint to the exact rebuilt dataset; "
            "completed core stages will be reused."
        )

    def evaluate_horizon(horizon: int) -> tuple[str, dict[str, Any]]:
        report = walk_forward_logistic_baseline(
            dataset,
            target_horizon=int(horizon),
            target_mode="target_before_stop",
            min_train_sessions=8,
            test_sessions_per_fold=2,
            embargo_sessions=1,
            min_train_rows=250,
        )
        return str(int(horizon)), _compact(report)

    horizon_evaluations: dict[str, dict[str, Any]] = {}
    validation_worker_count = max(
        1, min(int(config["validation_workers"]), len(config["horizons"]))
    )
    horizons_started = perf_counter()
    notify(
        f"Running {len(config['horizons'])} chronological horizon validations "
        f"with {validation_worker_count} independent worker(s)."
    )
    if validation_worker_count == 1:
        for horizon in config["horizons"]:
            key, report = evaluate_horizon(int(horizon))
            horizon_evaluations[key] = report
    else:
        with ThreadPoolExecutor(max_workers=validation_worker_count) as executor:
            futures = {
                executor.submit(evaluate_horizon, int(horizon)): int(horizon)
                for horizon in config["horizons"]
            }
            for future in as_completed(futures):
                key, report = future.result()
                horizon_evaluations[key] = report
                notify(f"Completed {key}-minute chronological validation.")
    horizon_evaluations = {
        str(int(horizon)): horizon_evaluations[str(int(horizon))]
        for horizon in config["horizons"]
    }
    notify(
        f"All horizon validations finished in {perf_counter() - horizons_started:.1f}s."
    )
    evaluation = deepcopy(
        horizon_evaluations.get(str(int(config["horizon"]))) or {}
    )

    notify("Running held-out-stock generalization validation.")
    generalization = leave_one_symbol_out_walk_forward_logistic_baseline(
        dataset,
        target_horizon=int(config["horizon"]),
        target_mode="target_before_stop",
        min_train_sessions=8,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=250,
        min_test_rows=25,
    )

    notify("Training gated portable logistic shadow-probability model.")
    probability_model = build_portable_probability_model(
        dataset,
        target_horizon=int(config["horizon"]),
        target_mode="target_before_stop",
        generalization=generalization,
        min_train_sessions=8,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=250,
    )

    notify("Training nonlinear gradient-boosted challenger with its own validation gates.")
    boosted_probability_model = build_boosted_probability_model(
        dataset,
        target_horizon=int(config["horizon"]),
        target_mode="target_before_stop",
        min_train_sessions=8,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=250,
    )
    probability_models = [
        model
        for model in (probability_model, boosted_probability_model)
        if isinstance(model, dict)
    ]

    ticker_specific_symbols = _priority_spread_symbol_subset(
        symbols,
        maximum=int(
            (payload or {}).get("ticker_specific_max_symbols")
            or DEFAULT_TICKER_SPECIFIC_SYMBOLS
        ),
        priority=(payload or {}).get("ticker_specific_priority_symbols") or ("SDOT",),
    )
    notify(
        "Running bounded ticker-specific validation on "
        f"{len(ticker_specific_symbols)} stocks, prioritizing "
        + ", ".join(ticker_specific_symbols[:1])
        + "."
    )
    ticker_specific_dataset = _dataset_for_symbols(dataset, ticker_specific_symbols)
    ticker_specific = ticker_specific_walk_forward_logistic_baseline(
        ticker_specific_dataset,
        target_horizon=int(config["horizon"]),
        target_mode="target_before_stop",
        min_train_sessions=8,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=150,
    )

    similarity_symbols = _priority_spread_symbol_subset(
        symbols,
        maximum=int((payload or {}).get("similarity_max_symbols") or DEFAULT_SIMILARITY_SYMBOLS),
        priority=ticker_specific_symbols,
    )
    notify(
        "Running bounded continuous stock-similarity validation on "
        f"{len(similarity_symbols)} representative stocks."
    )
    similarity_dataset = _dataset_for_symbols(dataset, similarity_symbols)
    similarity_validation = similarity_weighted_leave_one_symbol_out_walk_forward_logistic_baseline(
        similarity_dataset,
        target_horizon=int(config["horizon"]),
        target_mode="target_before_stop",
        min_train_sessions=8,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=250,
        min_test_rows=20,
    )

    notify(
        "Comparing same-ticker, similarity-weighted, and broad cross-stock learning "
        "on exactly aligned unseen rows."
    )
    stock_learning_router = build_stock_learning_router(
        ticker_specific,
        similarity_validation,
    )

    ticker_specific = _compact(ticker_specific)
    ticker_specific["automatic_subset_symbols"] = ticker_specific_symbols
    ticker_specific["automatic_subset_reason"] = (
        "Priority tickers are tested first, then a deterministic spread across the "
        "remaining universe keeps same-stock historical validation useful without "
        "making every automatic cloud retrain substantially slower."
    )
    similarity_validation = _compact(similarity_validation)
    similarity_validation["automatic_subset_symbols"] = similarity_symbols
    similarity_validation["automatic_subset_reason"] = (
        "The similarity subset always contains the bounded ticker-specific symbols, "
        "then fills remaining slots with a deterministic spread across the universe. "
        "That preserves broad similarity validation while enabling exact per-stock "
        "learning-route comparisons without another market-data replay."
    )

    historical_head_to_head = build_historical_model_head_to_head(probability_models)

    completed_at = datetime.now(timezone.utc).isoformat()
    identity = "|".join(
        [
            completed_at,
            " ".join(symbols),
            str(config["trading_days"]),
            str(config["horizon"]),
            str(config["profit_target_pct"]),
            str(config["stop_loss_pct"]),
        ]
    )
    return {
        "id": "auto-ml-" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16],
        "origin": "automatic_cloud_backfill",
        "symbols": symbols,
        "days": int(config["trading_days"]),
        "trading_days": int(config["trading_days"]),
        "horizon": int(config["horizon"]),
        "horizons": [int(value) for value in config["horizons"]],
        "target_mode": "target_before_stop",
        "profit_target_pct": float(config["profit_target_pct"]),
        "stop_loss_pct": float(config["stop_loss_pct"]),
        "session_mode": str(config["session_mode"]),
        "observation_stride_bars": int(config["observation_stride_bars"]),
        "feature_workers": int(config["feature_workers"]),
        "validation_workers": int(config["validation_workers"]),
        "runtime_seconds": round(perf_counter() - run_started, 3),
        "model_suite_version": int((payload or {}).get("model_suite_version") or MODEL_SUITE_VERSION),
        "dataset_summary": {
            key: deepcopy(value)
            for key, value in dataset.items()
            if key != "records"
        },
        "evaluation": _compact(evaluation),
        "horizon_evaluations": deepcopy(horizon_evaluations),
        "generalization": _compact(generalization),
        "probability_model": deepcopy(probability_model),
        "probability_models": deepcopy(probability_models),
        "boosted_probability_model": deepcopy(boosted_probability_model),
        "ticker_specific": deepcopy(ticker_specific),
        "similarity_validation": deepcopy(similarity_validation),
        "stock_learning_router": deepcopy(stock_learning_router),
        "historical_head_to_head": deepcopy(historical_head_to_head),
        "completed_at": completed_at,
        "checkpoint_stage": "automatic_backfill_complete",
        "research_only": True,
        "affects_live_ranking": False,
        "affects_execution": False,
    }


def merge_backfill_result_into_library(
    library: dict[str, Any],
    result: dict[str, Any],
    *,
    maximum_runs: int = MAX_AUTOMATIC_ML_RUN_HISTORY,
) -> dict[str, Any]:
    """Save a compact automatic ML result and visible worker status."""
    data = deepcopy(library or {})
    record = deepcopy(result or {})
    run_id = str(record.get("id") or "").strip()
    if not run_id:
        raise ValueError("Automatic ML backfill result is missing an id.")

    previous = [
        item
        for item in data.get("predictive_ml_runs") or []
        if isinstance(item, dict) and str(item.get("id") or "") != run_id
    ]
    data["predictive_ml_runs"] = [
        record,
        *previous,
    ][: max(1, int(maximum_runs))]

    research_system = (
        dict(data.get("research_system") or {})
        if isinstance(data.get("research_system"), dict)
        else {}
    )
    model = (
        record.get("probability_model")
        if isinstance(record.get("probability_model"), dict)
        else {}
    )
    probability_models = [
        item
        for item in record.get("probability_models") or []
        if isinstance(item, dict)
    ]
    if model and not any(
        str(item.get("id") or "") == str(model.get("id") or "")
        for item in probability_models
    ):
        probability_models.insert(0, model)
    ready_models = [
        item for item in probability_models
        if item.get("shadow_scoring_enabled")
    ]
    dataset_summary = (
        record.get("dataset_summary")
        if isinstance(record.get("dataset_summary"), dict)
        else {}
    )
    research_system["predictive_ml_backfill_status"] = {
        "status": "complete",
        "completed_at": record.get("completed_at"),
        "run_id": run_id,
        "symbols": list(record.get("symbols") or []),
        "trading_days": int(record.get("trading_days") or 0),
        "horizon": int(record.get("horizon") or 0),
        "horizons": [
            int(value)
            for value in record.get("horizons") or [record.get("horizon")]
            if value is not None
        ],
        "labeled_rows": int(dataset_summary.get("row_count") or 0),
        "symbols_with_data": int(dataset_summary.get("symbols_with_data") or 0),
        "model_status": model.get("status"),
        "model_suite_version": int(record.get("model_suite_version") or 1),
        "ready_model_count": len(ready_models),
        "ready_model_types": [
            str(item.get("model_type") or "unknown")
            for item in ready_models
        ],
        "similarity_status": str(
            (record.get("similarity_validation") or {}).get("status")
            if isinstance(record.get("similarity_validation"), dict)
            else ""
        ),
        "similarity_symbols": list(
            (record.get("similarity_validation") or {}).get("automatic_subset_symbols") or []
        ) if isinstance(record.get("similarity_validation"), dict) else [],
        "ticker_specific_status": str(
            (record.get("ticker_specific") or {}).get("status")
            if isinstance(record.get("ticker_specific"), dict)
            else ""
        ),
        "ticker_specific_symbols": list(
            (record.get("ticker_specific") or {}).get("automatic_subset_symbols") or []
        ) if isinstance(record.get("ticker_specific"), dict) else [],
        "learning_router_status": str(
            (record.get("stock_learning_router") or {}).get("status")
            if isinstance(record.get("stock_learning_router"), dict)
            else ""
        ),
        "learning_router_symbols_compared": int(
            (record.get("stock_learning_router") or {}).get("symbols_compared") or 0
        ) if isinstance(record.get("stock_learning_router"), dict) else 0,
        "learning_router_clear_routes": int(
            (record.get("stock_learning_router") or {}).get("symbols_with_clear_route") or 0
        ) if isinstance(record.get("stock_learning_router"), dict) else 0,
        "learning_router_route_counts": dict(
            (record.get("stock_learning_router") or {}).get("route_counts") or {}
        ) if isinstance(record.get("stock_learning_router"), dict) else {},
        "historical_leader_model_id": (
            (record.get("historical_head_to_head") or {}).get("leader_model_id")
            if isinstance(record.get("historical_head_to_head"), dict)
            else None
        ),
        "historical_leader_model_family": (
            (record.get("historical_head_to_head") or {}).get("leader_model_family")
            if isinstance(record.get("historical_head_to_head"), dict)
            else None
        ),
        "shadow_scoring_enabled": bool(ready_models),
        "research_only": True,
        "affects_live_ranking": False,
        "affects_execution": False,
    }
    data["research_system"] = research_system
    return data
