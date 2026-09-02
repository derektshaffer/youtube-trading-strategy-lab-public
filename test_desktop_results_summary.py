from __future__ import annotations

from hybrid_runtime.contracts import ExecutionTarget, JobRequest
from hybrid_runtime.results_summary import build_results_summary
from hybrid_runtime.router import RoutingPolicy


def fixture_library():
    return {
        "stock_strategy_finder_runs": [
            {
                "id": "finder-1",
                "generated_at": "2026-09-02T17:00:00Z",
                "symbol": "SDOT",
                "profile": "Deep",
                "winner_strategy_name": "VWAP continuation",
                "winner_source_strategy_id": "s1",
                "timeframe": "5Min",
                "unique_configurations_tested": 4200,
                "verdict": {"code": "research_only"},
                "holdout_metrics": {"net_pnl": 120.5, "trade_count": 9},
                "validation_metrics": {"net_pnl": 95.0, "trade_count": 12},
                "large_blob": "x" * 200_000,
            }
        ],
        "validation_runs": [
            {
                "id": "lab-1",
                "record_type": "strategy_lab_checkpoint",
                "status": "complete",
                "ticker": "AAPL",
                "saved_at": "2026-09-02T18:00:00Z",
                "result": {
                    "ticker": "AAPL",
                    "timeframe": "5Min",
                    "history_days": 30,
                    "report": {"winner": {"strategy_name": "EMA pullback", "source_strategy_id": "s2"}},
                    "strength": {"score": 82.5},
                    "evidence_verdict": {"code": "insufficient_robustness"},
                    "heavy": ["x"] * 10_000,
                },
            },
            {
                "id": "validation-1",
                "strategy_id": "s1",
                "strategy_name": "VWAP continuation",
                "generated_at": "2026-09-02T16:00:00Z",
                "validation_status": "research_only",
                "evidence_verdict": {"code": "insufficient_robustness"},
                "validation_method_version": "v7",
            },
        ],
        "strategies": [
            {
                "id": "s1",
                "name": "VWAP continuation",
                "category": "momentum",
                "validation_status": "research_only",
            },
            {
                "id": "s2",
                "name": "Validated EMA",
                "category": "pullback",
                "validation_status": "validated",
            },
            {
                "id": "s3",
                "name": "Rejected idea",
                "validation_status": "rejected",
            },
        ],
    }


def test_results_summary_is_bounded_and_drops_heavy_payloads():
    result = build_results_summary(fixture_library(), limit=5)
    assert result["bounded"] is True
    assert result["counts"]["finder_runs"] == 1
    assert result["counts"]["strategy_lab_runs"] == 1
    assert result["counts"]["validation_runs"] == 1
    assert "large_blob" not in result["finder_runs"][0]
    assert "result" not in result["strategy_lab_runs"][0]
    assert len(str(result)) < 20_000


def test_validated_count_is_fail_closed_and_requires_explicit_validated_status():
    result = build_results_summary(fixture_library(), limit=10)
    assert result["counts"]["validated_strategies"] == 1
    assert [item["id"] for item in result["validated_strategies"]] == ["s2"]


def test_results_summary_route_stays_local():
    decision = RoutingPolicy().decide(
        JobRequest(
            "library.results_summary",
            {"limit": 30},
            requested_target=ExecutionTarget.AUTO,
        )
    )
    assert decision.target == ExecutionTarget.LOCAL
    assert "immediate local response" in decision.reason
