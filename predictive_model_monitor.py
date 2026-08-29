"""Compact live performance monitoring for research-only shadow probability models."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
from typing import Any, Iterable


DEFAULT_MONITOR_BUCKET_MINUTES = 30
DEFAULT_MIN_BREADTH_SYMBOLS = 5
DEFAULT_MIN_BREADTH_SESSIONS = 5
DEFAULT_MIN_EVALUATED = 50
DEFAULT_DRIFT_ALERT_EVALUATED = 100


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bucket_key(symbol: str, observed_at: datetime, minutes: int) -> str:
    width = max(5, int(minutes))
    floored_minute = observed_at.minute - (observed_at.minute % width)
    bucket = observed_at.replace(minute=floored_minute, second=0, microsecond=0)
    return f"{symbol}|{bucket.isoformat()}"


def _target_actual(context: dict[str, Any], outcomes: dict[str, Any]) -> int | None:
    target = str(context.get("ml_target") or "")
    if not target:
        return None

    horizon = None
    marker = "bar"
    try:
        left = target.rsplit("_", 1)[-1]
        if left.endswith(marker):
            horizon = int(left[:-len(marker)])
    except (TypeError, ValueError):
        horizon = None
    if horizon is None:
        return None

    outcome = outcomes.get(str(horizon))
    if not isinstance(outcome, dict) or outcome.get("status") != "EVALUATED":
        return None

    if "target_before_stop" in target:
        value = outcome.get("target_before_stop")
        return int(value) if isinstance(value, bool) else None

    if "positive_return" in target:
        forward_return = _number(outcome.get("forward_return_pct"))
        return None if forward_return is None else int(forward_return > 0)

    return None


def _log_loss(actual: list[int], probabilities: list[float]) -> float:
    eps = 1e-9
    values = []
    for y, p in zip(actual, probabilities):
        p = min(1.0 - eps, max(eps, float(p)))
        values.append(-(y * math.log(p) + (1 - y) * math.log(1.0 - p)))
    return float(sum(values) / len(values))


def _reliability_bins(
    actual: list[int],
    probabilities: list[float],
    bins: int = 5,
) -> list[dict[str, Any]]:
    groups: list[list[tuple[int, float]]] = [[] for _ in range(max(2, int(bins)))]
    for y, p in zip(actual, probabilities):
        index = min(len(groups) - 1, int(max(0.0, min(0.999999, p)) * len(groups)))
        groups[index].append((y, p))
    rows = []
    total = len(actual)
    for i, group in enumerate(groups):
        if not group:
            continue
        mean_p = sum(p for _, p in group) / len(group)
        rate = sum(y for y, _ in group) / len(group)
        rows.append(
            {
                "bin_low": i / len(groups),
                "bin_high": (i + 1) / len(groups),
                "rows": len(group),
                "weight": len(group) / total if total else 0.0,
                "mean_probability": float(mean_p),
                "observed_rate": float(rate),
                "absolute_gap": float(abs(mean_p - rate)),
            }
        )
    return rows


def _ece(rows: list[dict[str, Any]]) -> float:
    return float(
        sum(float(item["weight"]) * float(item["absolute_gap"]) for item in rows)
    )


def build_shadow_model_monitor(
    observations: Iterable[dict[str, Any]],
    *,
    model_lookup: dict[str, dict[str, Any]] | None = None,
    bucket_minutes: int = DEFAULT_MONITOR_BUCKET_MINUTES,
) -> dict[str, Any]:
    """Evaluate matured shadow probabilities by model using deduplicated decision points."""
    model_lookup = dict(model_lookup or {})
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    raw_counts: dict[str, int] = defaultdict(int)

    for raw in observations or []:
        if not isinstance(raw, dict):
            continue
        context = raw.get("context") if isinstance(raw.get("context"), dict) else {}
        symbol = str(raw.get("symbol") or "").strip().upper()
        observed_at = _parse_time(raw.get("observed_at"))
        outcomes = raw.get("outcomes") if isinstance(raw.get("outcomes"), dict) else {}
        if not symbol or observed_at is None:
            continue

        predictions = [
            dict(item)
            for item in context.get("ml_predictions") or []
            if isinstance(item, dict)
        ]
        primary_model_id = str(context.get("ml_model_id") or "").strip()
        if primary_model_id and not any(
            str(item.get("model_id") or "").strip() == primary_model_id
            for item in predictions
        ):
            predictions.append(
                {
                    "model_id": primary_model_id,
                    "probability": context.get("ml_probability"),
                    "target": context.get("ml_target"),
                    "target_description": context.get("ml_target_description"),
                }
            )

        for prediction in predictions:
            model_id = str(prediction.get("model_id") or "").strip()
            probability = _number(prediction.get("probability"))
            if not model_id or probability is None or not (0.0 <= probability <= 1.0):
                continue
            prediction_context = {
                **context,
                "ml_model_id": model_id,
                "ml_probability": probability,
                "ml_target": prediction.get("target") or context.get("ml_target"),
                "ml_target_description": (
                    prediction.get("target_description")
                    or context.get("ml_target_description")
                ),
            }
            actual = _target_actual(prediction_context, outcomes)
            if actual is None:
                continue
            raw_counts[model_id] += 1

            key = (model_id, _bucket_key(symbol, observed_at, bucket_minutes))
            existing = deduped.get(key)
            candidate = {
                "model_id": model_id,
                "symbol": symbol,
                "session": str(raw.get("session") or observed_at.date().isoformat()),
                "observed_at": observed_at,
                "probability": float(probability),
                "actual": int(actual),
                "target": prediction_context.get("ml_target"),
                "target_description": prediction_context.get("ml_target_description"),
            }
            # Keep the earliest observation in each stock/time bucket to avoid cherry-picking.
            if existing is None or observed_at < existing["observed_at"]:
                deduped[key] = candidate

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in deduped.values():
        grouped[item["model_id"]].append(item)

    models: list[dict[str, Any]] = []
    for model_id, rows in grouped.items():
        rows.sort(key=lambda item: item["observed_at"])
        actual = [int(item["actual"]) for item in rows]
        probabilities = [float(item["probability"]) for item in rows]
        positive_rate = sum(actual) / len(actual)
        mean_probability = sum(probabilities) / len(probabilities)
        brier = sum((p - y) ** 2 for y, p in zip(actual, probabilities)) / len(actual)
        naive_brier = sum((positive_rate - y) ** 2 for y in actual) / len(actual)
        brier_skill = None if naive_brier <= 0 else 1.0 - (brier / naive_brier)
        reliability = _reliability_bins(actual, probabilities)
        ece = _ece(reliability)
        accuracy = sum(int((p >= 0.5) == bool(y)) for y, p in zip(actual, probabilities)) / len(actual)
        symbols = sorted({item["symbol"] for item in rows})
        sessions = sorted({item["session"] for item in rows})

        historical = model_lookup.get(model_id) or {}
        hist_validation = historical.get("validation") if isinstance(historical.get("validation"), dict) else {}
        hist_brier = _number(hist_validation.get("brier_score"))
        brier_delta = None if hist_brier is None else float(brier - hist_brier)

        enough_breadth = (
            len(rows) >= DEFAULT_MIN_EVALUATED
            and len(symbols) >= DEFAULT_MIN_BREADTH_SYMBOLS
            and len(sessions) >= DEFAULT_MIN_BREADTH_SESSIONS
        )

        reasons: list[str] = []
        if not enough_breadth:
            status = "COLLECTING"
            if len(rows) < DEFAULT_MIN_EVALUATED:
                reasons.append(f"Need {DEFAULT_MIN_EVALUATED} deduplicated outcomes; have {len(rows)}.")
            if len(symbols) < DEFAULT_MIN_BREADTH_SYMBOLS:
                reasons.append(f"Need {DEFAULT_MIN_BREADTH_SYMBOLS} stocks; have {len(symbols)}.")
            if len(sessions) < DEFAULT_MIN_BREADTH_SESSIONS:
                reasons.append(f"Need {DEFAULT_MIN_BREADTH_SESSIONS} sessions; have {len(sessions)}.")
        else:
            severe = (
                len(rows) >= DEFAULT_DRIFT_ALERT_EVALUATED
                and (
                    (brier_skill is not None and brier_skill < -0.05)
                    or ece > 0.15
                    or (brier_delta is not None and brier_delta > 0.05)
                )
            )
            watch = (
                (brier_skill is not None and brier_skill <= 0)
                or ece > 0.10
                or (brier_delta is not None and brier_delta > 0.03)
            )
            if severe:
                status = "DRIFT_ALERT"
                reasons.append("Live shadow performance materially deteriorated versus baseline expectations.")
            elif watch:
                status = "WATCH"
                reasons.append("Live shadow calibration or Brier skill is weaker than desired.")
            else:
                status = "HEALTHY"
                reasons.append("Live shadow calibration and Brier skill remain within monitoring tolerances.")

        models.append(
            {
                "model_id": model_id,
                "status": status,
                "evaluated_decisions": len(rows),
                "raw_shadow_observations": int(raw_counts.get(model_id) or 0),
                "symbols": symbols,
                "symbol_count": len(symbols),
                "sessions": sessions,
                "session_count": len(sessions),
                "positive_rate": float(positive_rate),
                "mean_probability": float(mean_probability),
                "calibration_gap": float(abs(mean_probability - positive_rate)),
                "expected_calibration_error": float(ece),
                "brier_score": float(brier),
                "naive_brier_score": float(naive_brier),
                "brier_skill_vs_naive": brier_skill,
                "log_loss": _log_loss(actual, probabilities),
                "accuracy_at_50pct": float(accuracy),
                "historical_oos_brier_score": hist_brier,
                "brier_delta_vs_historical": brier_delta,
                "target": rows[-1].get("target"),
                "target_description": rows[-1].get("target_description"),
                "first_observed_at": rows[0]["observed_at"].isoformat(),
                "last_observed_at": rows[-1]["observed_at"].isoformat(),
                "reliability_bins": reliability,
                "reasons": reasons,
                "research_only": True,
                "affects_live_ranking": False,
                "affects_execution": False,
            }
        )

    models.sort(key=lambda item: str(item.get("last_observed_at") or ""), reverse=True)
    latest = models[0] if models else None
    return {
        "status": "EVALUATED" if models else "NO_MATURED_PREDICTIONS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bucket_minutes": max(5, int(bucket_minutes)),
        "model_count": len(models),
        "models": models,
        "latest_model": latest,
        "research_only": True,
        "affects_live_ranking": False,
        "affects_execution": False,
        "note": (
            "Repeated scans are collapsed to one stock/time decision point before scoring "
            "to reduce false confidence from highly correlated refreshes."
        ),
    }
