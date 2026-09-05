"""Diagnostic calibration audit for Trading Intelligence validation.

The audit deliberately uses deterministic synthetic evidence so it can answer a
narrow question before more strategy research is trusted: can the production
validation gates distinguish durable edge from fake, overfit, sparse, or fragile
edge, and do the gates accidentally require every legitimate edge to be broad
across unrelated stocks?

This module does not relax production validation. It reports calibration gaps.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from trading_auto_research import _global_validation_gate
from trading_validation_core import validation_strength


def _metrics(
    *,
    trades: int,
    pnl: float,
    profit_factor: float,
    drawdown: float = 4.0,
    win_rate: float = 58.0,
) -> dict[str, Any]:
    return {
        "trade_count": trades,
        "net_pnl": pnl,
        "profit_factor": profit_factor,
        "max_drawdown_pct": drawdown,
        "win_rate_pct": win_rate,
        "return_pct": round(pnl / 100.0, 3),
    }


def _optimizer_report(
    *,
    status: str = "VALIDATED",
    training: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    holdout: dict[str, Any] | None = None,
    stress: dict[str, Any] | None = None,
    sensitivity_label: str = "ROBUST",
    sensitivity_score: float = 84.0,
    sensitivity_pass: bool = True,
) -> dict[str, Any]:
    training = training or _metrics(trades=60, pnl=1200.0, profit_factor=1.8)
    validation = validation or _metrics(trades=30, pnl=350.0, profit_factor=1.4)
    holdout = holdout or _metrics(trades=30, pnl=300.0, profit_factor=1.35)
    stress = stress or _metrics(trades=30, pnl=180.0, profit_factor=1.2)
    return {
        "winner": {
            "status": status,
            "training_metrics": training,
            "validation_metrics": validation,
            "holdout_metrics": holdout,
            "stress_metrics": stress,
            "holdout_execution_sensitivity": {
                "score": sensitivity_score,
                "label": sensitivity_label,
                "passes_validation_gate": sensitivity_pass,
            },
        },
        "optimization_settings": {
            "minimum_validation_trades": 2,
            "maximum_drawdown_pct": 15.0,
        },
    }


def _walk_forward(
    *,
    score: float = 82.0,
    folds: int = 3,
    active: int = 3,
    profitable: int = 2,
    trades: int = 18,
) -> dict[str, Any]:
    active_profitable_pct = profitable / active * 100.0 if active else 0.0
    return {
        "summary": {
            "score": score,
            "fold_count": folds,
            "active_fold_count": active,
            "profitable_fold_count": profitable,
            "profitable_fold_pct": round(active_profitable_pct, 1),
            "external_trade_count": trades,
        }
    }


def _generalization(
    *,
    score: float = 80.0,
    active_symbols: int = 5,
    profitable_symbol_pct: float = 80.0,
    total_trades: int = 60,
) -> dict[str, Any]:
    return {
        "summary": {
            "score": score,
            "active_symbols": active_symbols,
            "profitable_symbol_pct": profitable_symbol_pct,
            "total_trades": total_trades,
        }
    }


def calibration_scenarios() -> list[dict[str, Any]]:
    strong = _optimizer_report()
    return [
        {
            "name": "broad_durable_positive_control",
            "control_type": "hard_positive",
            "validation_scope": "broad",
            "desired_status": "validated",
            "anchor_report": strong,
            "walk_forward": _walk_forward(),
            "generalization": _generalization(),
            "broad_universe": True,
            "purpose": "A deliberately strong, sufficiently sampled, cost-robust broad edge must pass.",
        },
        {
            "name": "random_no_edge_negative_control",
            "control_type": "hard_negative",
            "validation_scope": "broad",
            "desired_status": "research_only",
            "anchor_report": _optimizer_report(
                status="NO VALIDATED EDGE",
                training=_metrics(trades=60, pnl=-80.0, profit_factor=0.92),
                validation=_metrics(trades=30, pnl=-45.0, profit_factor=0.82),
                holdout=_metrics(trades=30, pnl=-70.0, profit_factor=0.74),
                stress=_metrics(trades=30, pnl=-100.0, profit_factor=0.65),
                sensitivity_label="FRAGILE",
                sensitivity_score=25.0,
                sensitivity_pass=False,
            ),
            "walk_forward": _walk_forward(score=28.0, active=3, profitable=1, trades=18),
            "generalization": _generalization(
                score=30.0,
                active_symbols=5,
                profitable_symbol_pct=20.0,
                total_trades=55,
            ),
            "broad_universe": True,
            "purpose": "A no-edge control must fail closed.",
        },
        {
            "name": "training_overfit_negative_control",
            "control_type": "hard_negative",
            "validation_scope": "broad",
            "desired_status": "research_only",
            "anchor_report": _optimizer_report(
                status="NO VALIDATED EDGE",
                training=_metrics(trades=120, pnl=3500.0, profit_factor=2.4),
                validation=_metrics(trades=35, pnl=-120.0, profit_factor=0.72),
                holdout=_metrics(trades=35, pnl=-180.0, profit_factor=0.61),
                stress=_metrics(trades=35, pnl=-250.0, profit_factor=0.55),
                sensitivity_label="FRAGILE",
                sensitivity_score=20.0,
                sensitivity_pass=False,
            ),
            "walk_forward": _walk_forward(score=22.0, active=3, profitable=0, trades=20),
            "generalization": _generalization(
                score=35.0,
                active_symbols=5,
                profitable_symbol_pct=20.0,
                total_trades=65,
            ),
            "broad_universe": True,
            "purpose": "A spectacular training fit that dies unseen must be rejected.",
        },
        {
            "name": "sparse_lucky_positive_slice_negative_control",
            "control_type": "hard_negative",
            "validation_scope": "broad",
            "desired_status": "research_only",
            "anchor_report": _optimizer_report(
                validation=_metrics(trades=4, pnl=80.0, profit_factor=2.1),
                holdout=_metrics(trades=4, pnl=70.0, profit_factor=1.9),
            ),
            "walk_forward": _walk_forward(
                score=96.0,
                folds=3,
                active=1,
                profitable=1,
                trades=4,
            ),
            "generalization": _generalization(),
            "broad_universe": True,
            "purpose": "Tiny lucky unseen samples must not masquerade as verified edge.",
        },
        {
            "name": "execution_fragile_negative_control",
            "control_type": "hard_negative",
            "validation_scope": "broad",
            "desired_status": "research_only",
            "anchor_report": _optimizer_report(
                status="COST SENSITIVE",
                sensitivity_label="FRAGILE",
                sensitivity_score=28.0,
                sensitivity_pass=False,
            ),
            "walk_forward": _walk_forward(),
            "generalization": _generalization(),
            "broad_universe": True,
            "purpose": "An apparent edge that collapses under realistic execution costs must fail.",
        },
        {
            "name": "regime_scoped_positive_control",
            "control_type": "scope_policy",
            "validation_scope": "matched_regime_cohort",
            "desired_status": "validated",
            "anchor_report": strong,
            "walk_forward": _walk_forward(),
            "generalization": _generalization(
                score=82.0,
                active_symbols=2,
                profitable_symbol_pct=100.0,
                total_trades=16,
            ),
            "broad_universe": True,
            "purpose": (
                "A strong edge restricted to its declared regime/cohort should have a scoped "
                "validation path instead of being forced to satisfy universal breadth thresholds."
            ),
        },
        {
            "name": "stock_specific_positive_control",
            "control_type": "scope_policy",
            "validation_scope": "stock_specific",
            "desired_status": "validated",
            "anchor_report": strong,
            "walk_forward": _walk_forward(),
            "generalization": _generalization(
                score=0.0,
                active_symbols=0,
                profitable_symbol_pct=0.0,
                total_trades=0,
            ),
            "broad_universe": True,
            "purpose": (
                "A strong stock-specific edge should be eligible for an explicit stock-specific "
                "validation label rather than failing because unrelated stocks did not trade."
            ),
        },
    ]


def evaluate_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    walk_forward = scenario["walk_forward"]
    anchor_report = scenario["anchor_report"]
    strength = validation_strength(anchor_report, walk_forward)
    observed_status, gate_reasons = _global_validation_gate(
        anchor_report=anchor_report,
        strength=strength,
        generalization=scenario["generalization"],
        walk_forward=walk_forward,
        broad_universe=bool(scenario.get("broad_universe", True)),
    )
    desired_status = str(scenario["desired_status"])
    return {
        "name": scenario["name"],
        "control_type": scenario["control_type"],
        "validation_scope": scenario["validation_scope"],
        "purpose": scenario["purpose"],
        "desired_status": desired_status,
        "observed_status": observed_status,
        "calibration_match": observed_status == desired_status,
        "robustness_score": strength.get("score"),
        "robustness_label": strength.get("label"),
        "independently_positive": strength.get("independently_positive"),
        "gate_reasons": gate_reasons,
    }


def run_calibration_audit() -> dict[str, Any]:
    results = [evaluate_scenario(item) for item in calibration_scenarios()]
    hard_controls = [item for item in results if item["control_type"] != "scope_policy"]
    scope_controls = [item for item in results if item["control_type"] == "scope_policy"]
    hard_controls_pass = all(bool(item["calibration_match"]) for item in hard_controls)
    scope_controls_pass = all(bool(item["calibration_match"]) for item in scope_controls)

    findings: list[dict[str, Any]] = []
    if hard_controls_pass:
        findings.append(
            {
                "severity": "pass",
                "finding": (
                    "Core gates distinguish the broad durable positive control from no-edge, "
                    "overfit, sparse/lucky, and execution-fragile controls."
                ),
            }
        )
    else:
        failed = [item["name"] for item in hard_controls if not item["calibration_match"]]
        findings.append(
            {
                "severity": "critical",
                "finding": "One or more hard calibration controls behaved incorrectly.",
                "failed_controls": failed,
            }
        )

    if not scope_controls_pass:
        blocked = [
            {
                "name": item["name"],
                "scope": item["validation_scope"],
                "gate_reasons": item["gate_reasons"],
            }
            for item in scope_controls
            if not item["calibration_match"]
        ]
        findings.append(
            {
                "severity": "design_gap",
                "finding": (
                    "The final autonomous gate is not scope-aware: strong regime/cohort or "
                    "stock-specific controls are forced through universal cross-stock breadth gates."
                ),
                "blocked_controls": blocked,
                "recommended_action": (
                    "Introduce explicit validation scopes (broad, matched cohort/regime, stock-specific) "
                    "with scope-appropriate generalization requirements while preserving untouched "
                    "holdout, walk-forward, execution-cost, drawdown, and sample-size safeguards."
                ),
            }
        )

    return {
        "audit": "trading_lab_validation_calibration",
        "hard_controls_pass": hard_controls_pass,
        "scope_controls_pass": scope_controls_pass,
        "overall_calibrated": bool(hard_controls_pass and scope_controls_pass),
        "scenario_count": len(results),
        "results": results,
        "findings": findings,
        "note": (
            "This deterministic audit calibrates production scoring/gating logic. It does not prove "
            "that the market-data loader, strategy extraction, or backtest execution engine can "
            "discover an edge from raw prices; those remain separate calibration layers."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any calibration class has a mismatch.",
    )
    args = parser.parse_args()

    report = run_calibration_audit()
    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    return 1 if args.strict and not report["overall_calibrated"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
