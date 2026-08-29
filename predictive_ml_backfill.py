"""Automatic historical backfill and probability-model training.

This module reuses the Trading Lab's causal historical replay and validation stack
so the cloud worker can bootstrap/retrain probabilistic models without a browser
session. Results stay research-only; passing models are only eligible for shadow
probability display.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Callable

from predictive_ml_pipeline import (
    build_cross_stock_training_dataset,
    leave_one_symbol_out_walk_forward_logistic_baseline,
    walk_forward_logistic_baseline,
)
from predictive_probability_model import build_portable_probability_model


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
)
DEFAULT_TRADING_DAYS = 30
DEFAULT_HORIZON_MINUTES = 15
DEFAULT_PROFIT_TARGET_PCT = 1.0
DEFAULT_STOP_LOSS_PCT = 0.75
DEFAULT_SESSION_MODE = "regular"
DEFAULT_OBSERVATION_STRIDE_BARS = 5
DEFAULT_MAX_SYMBOLS = 12
MAX_AUTOMATIC_ML_RUN_HISTORY = 12


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
    return {
        "symbols": symbols,
        "trading_days": max(
            12,
            min(90, int(payload.get("trading_days") or DEFAULT_TRADING_DAYS)),
        ),
        "horizon": max(
            5,
            min(60, int(payload.get("horizon") or DEFAULT_HORIZON_MINUTES)),
        ),
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
    }


def _compact(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in (report or {}).items()
        if key != "predictions"
    }


def run_predictive_ml_backfill(
    market: Any,
    library: dict[str, Any],
    *,
    payload: dict[str, Any] | None = None,
    now: datetime | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run one batched historical replay -> validation -> portable-model cycle."""
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

    notify(
        f"Backfilling {len(symbols)} stocks across {trading_days} trading days "
        f"for a {int(config['horizon'])}-minute probability target."
    )
    dataset = build_cross_stock_training_dataset(
        market,
        symbols,
        start=start,
        end=end,
        timeframe="1Min",
        horizons=(int(config["horizon"]),),
        swing_radius=3,
        max_pages=300,
        require_full_horizon=True,
        session_limit=trading_days,
        profit_target_pct=float(config["profit_target_pct"]),
        stop_loss_pct=float(config["stop_loss_pct"]),
        session_mode=str(config["session_mode"]),
        observation_stride_bars=int(config["observation_stride_bars"]),
        progress=progress,
    )

    notify("Running chronological walk-forward probability validation.")
    evaluation = walk_forward_logistic_baseline(
        dataset,
        target_horizon=int(config["horizon"]),
        target_mode="target_before_stop",
        min_train_sessions=8,
        test_sessions_per_fold=2,
        embargo_sessions=1,
        min_train_rows=250,
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

    notify("Training gated portable shadow-probability model.")
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
        "target_mode": "target_before_stop",
        "profit_target_pct": float(config["profit_target_pct"]),
        "stop_loss_pct": float(config["stop_loss_pct"]),
        "session_mode": str(config["session_mode"]),
        "observation_stride_bars": int(config["observation_stride_bars"]),
        "dataset_summary": {
            key: deepcopy(value)
            for key, value in dataset.items()
            if key != "records"
        },
        "evaluation": _compact(evaluation),
        "generalization": _compact(generalization),
        "probability_model": deepcopy(probability_model),
        "ticker_specific": {
            "status": "SKIPPED_FOR_SPEED",
            "reason": (
                "Automatic bootstrap prioritizes the cross-stock baseline, held-out-stock "
                "generalization, and portable probability gate. Ticker-specific research "
                "remains available in the interactive ML lab."
            ),
        },
        "similarity_validation": {
            "status": "DEFERRED_FOR_SPEED",
            "reason": (
                "Continuous behavioral-similarity validation remains available in the "
                "interactive research workflow; the cloud bootstrap omits it to produce "
                "a validated first model faster."
            ),
        },
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
        "labeled_rows": int(dataset_summary.get("row_count") or 0),
        "symbols_with_data": int(dataset_summary.get("symbols_with_data") or 0),
        "model_status": model.get("status"),
        "shadow_scoring_enabled": bool(model.get("shadow_scoring_enabled")),
        "research_only": True,
        "affects_live_ranking": False,
        "affects_execution": False,
    }
    data["research_system"] = research_system
    return data
