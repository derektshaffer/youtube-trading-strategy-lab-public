"""Plain-language trust/readiness summaries for Trading Intelligence Lab."""

from __future__ import annotations

from typing import Any


def build_trust_readiness_summary(
    strategies: list[dict[str, Any]],
    library: dict[str, Any],
) -> dict[str, Any]:
    """Separate historical research progress from production-ready evidence."""
    historical_strategies = [
        item
        for item in strategies
        if str(item.get("validation_status") or "").strip().lower() == "validated"
    ]
    paper_ready_strategies = [
        item
        for item in historical_strategies
        if str(item.get("paper_validation_status") or "").strip().lower() == "ready"
    ]

    predictive_runs = [
        item
        for item in library.get("predictive_ml_runs") or []
        if isinstance(item, dict)
    ]
    models: list[dict[str, Any]] = []
    for run in predictive_runs:
        candidates = list(run.get("probability_models") or [])
        legacy = run.get("probability_model")
        if isinstance(legacy, dict):
            candidates.append(legacy)
        models.extend(item for item in candidates if isinstance(item, dict))

    shadow_models = [item for item in models if item.get("shadow_scoring_enabled")]
    production_models = [
        item
        for item in models
        if not item.get("research_only", True)
        and bool(item.get("production_enabled") or item.get("affects_live_ranking"))
    ]

    return {
        "historically_validated_strategies": len(historical_strategies),
        "paper_ready_strategies": len(paper_ready_strategies),
        "shadow_probability_models": len(shadow_models),
        "production_probability_models": len(production_models),
        "production_probability_ready": bool(production_models),
        "message": (
            "No production-validated probability is available yet."
            if not production_models
            else "At least one probability model is eligible for production use."
        ),
    }
