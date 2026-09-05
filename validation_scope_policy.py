"""Prototype scope-aware validation policy for calibration only.

Production behavior remains unchanged. Broad validation delegates to the current
production global gate exactly. Non-broad scopes are accepted only when the
scope was explicitly locked before unseen validation; this prevents a failed
broad strategy from being relabeled after its outcomes are known.
"""

from __future__ import annotations

from typing import Any

from trading_auto_research import _global_validation_gate
from youtube_strategy_engine import safe_float


VALIDATION_SCOPES = {
    "broad",
    "matched_regime_cohort",
    "stock_specific",
}


def _walk_forward_reasons(
    walk_forward: dict[str, Any] | None,
    *,
    minimum_external_trades: int,
    require_all_folds_active: bool,
) -> list[str]:
    reasons: list[str] = []
    if not walk_forward:
        return ["Walk-forward validation is missing or failed; validation fails closed."]

    wf = walk_forward.get("summary") or {}
    fold_count = int(wf.get("fold_count") or 0)
    active_fold_count = int(wf.get("active_fold_count") or 0)
    profitable_fold_count = int(wf.get("profitable_fold_count") or 0)
    active_fold_pct = active_fold_count / fold_count * 100.0 if fold_count else 0.0
    profitable_scheduled_pct = (
        profitable_fold_count / fold_count * 100.0 if fold_count else 0.0
    )

    if fold_count < 3:
        reasons.append(
            "Fewer than three rolling walk-forward folds were available; temporal validation is too thin."
        )
    if active_fold_count < 2:
        reasons.append("Fewer than two walk-forward folds produced trades with the frozen setup.")
    if require_all_folds_active:
        if fold_count <= 0 or active_fold_count < fold_count:
            reasons.append(
                "Stock-specific validation requires the frozen setup to trade in every scheduled walk-forward fold."
            )
    elif active_fold_pct < 66.7:
        reasons.append(
            "The frozen setup traded in fewer than two-thirds of scheduled walk-forward folds."
        )
    if (safe_float(wf.get("profitable_fold_pct"), 0.0) or 0.0) < 50.0:
        reasons.append("Fewer than half of active rolling walk-forward folds were profitable.")
    if profitable_scheduled_pct < 50.0:
        reasons.append(
            "Fewer than half of all scheduled walk-forward folds were profitable once inactive folds are counted."
        )
    if int(wf.get("external_trade_count") or 0) < minimum_external_trades:
        reasons.append(
            f"Walk-forward unseen periods contain fewer than {minimum_external_trades} trades."
        )
    return reasons


def scope_aware_validation_gate(
    *,
    anchor_report: dict[str, Any],
    strength: dict[str, Any],
    generalization: dict[str, Any],
    walk_forward: dict[str, Any] | None,
    broad_universe: bool,
    validation_scope: str = "broad",
    scope_locked_before_validation: bool = False,
) -> tuple[str, list[str]]:
    """Evaluate a predeclared validation scope without relaxing core evidence.

    Broad scope is byte-for-byte policy-equivalent to the production gate because
    this function delegates directly to it. Scoped modes are deliberately stricter
    on temporal evidence while removing only breadth requirements that are not
    meaningful for the declared target population.
    """
    scope = str(validation_scope or "broad").strip().lower()
    if scope not in VALIDATION_SCOPES:
        return "research_only", [f"Unknown validation scope: {scope or 'empty'}. Validation fails closed."]

    if scope == "broad":
        return _global_validation_gate(
            anchor_report=anchor_report,
            strength=strength,
            generalization=generalization,
            walk_forward=walk_forward,
            broad_universe=broad_universe,
        )

    if not scope_locked_before_validation:
        return "research_only", [
            "Non-broad validation scope was not locked before unseen outcomes were examined; scope-shopping is prohibited."
        ]

    winner = anchor_report.get("winner") or {}
    summary = generalization.get("summary") or {}
    reasons: list[str] = []

    # These are the same foundational requirements as broad production
    # validation. Scope never excuses a weak anchor or weak unseen evidence.
    if winner.get("status") != "VALIDATED":
        reasons.append("Anchor optimization did not pass its validation/stress gate.")
    if not bool(strength.get("independently_positive")):
        reasons.append("Validation and untouched holdout were not independently positive.")
    if (safe_float(strength.get("score"), 0.0) or 0.0) < 70.0:
        reasons.append("Robustness score is below the autonomous 70/100 gate.")

    if scope == "matched_regime_cohort":
        # The cohort must still generalize beyond one ticker, but does not need
        # to behave like a universal strategy across unrelated stocks.
        if not broad_universe:
            reasons.append(
                "Only a current-screener fallback universe was available, so matched-cohort selection bias is too high."
            )
        if (safe_float(summary.get("score"), 0.0) or 0.0) < 65.0:
            reasons.append("Matched-cohort generalization score is below 65/100.")
        if int(summary.get("active_symbols") or 0) < 2:
            reasons.append("Fewer than two different matched-cohort stocks produced trades with the frozen rules.")
        if (safe_float(summary.get("profitable_symbol_pct"), 0.0) or 0.0) < 60.0:
            reasons.append("The frozen strategy was profitable on fewer than 60% of active matched-cohort stocks.")
        if int(summary.get("total_trades") or 0) < 12:
            reasons.append("Matched-cohort evidence contains fewer than 12 trades.")
        reasons.extend(
            _walk_forward_reasons(
                walk_forward,
                minimum_external_trades=9,
                require_all_folds_active=False,
            )
        )
    else:
        # Stock-specific validation substitutes stronger repeated temporal proof
        # for cross-stock proof. It does not claim portability to other tickers.
        if (safe_float(strength.get("score"), 0.0) or 0.0) < 75.0:
            reasons.append("Stock-specific robustness score is below the stricter 75/100 gate.")
        reasons.extend(
            _walk_forward_reasons(
                walk_forward,
                minimum_external_trades=12,
                require_all_folds_active=True,
            )
        )

    return ("validated" if not reasons else "research_only"), reasons
