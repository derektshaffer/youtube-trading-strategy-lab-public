from __future__ import annotations

from unittest.mock import patch

from hybrid_runtime.contracts import ExecutionTarget, JobRequest
from hybrid_runtime.research_ml_summary import build_research_ml_summary
from hybrid_runtime.router import RoutingPolicy


def fixture_library():
    return {
        "research_queue": [
            {
                "id": "rq-1",
                "type": "strategy_lab",
                "status": "running",
                "stage": "optimization",
                "progress": 0.64,
                "updated_at": "2026-09-02T20:00:00Z",
                "payload": {
                    "distributed_message": "Testing family 7 of 10",
                    "machine_rules": {"huge": "x" * 100_000},
                },
            },
            {
                "id": "rq-2",
                "type": "web_research",
                "status": "complete",
                "updated_at": "2026-09-02T19:00:00Z",
                "result": {"raw_text": "y" * 100_000},
            },
        ],
        "research_worker_runs": [
            {
                "id": "research-1",
                "type": "web_research",
                "status": "complete",
                "title": "Relative volume continuation",
                "model": "gemini-test",
                "generated_at": "2026-09-02T18:00:00Z",
                "hypotheses": [{"id": "h1"}],
                "sources": [{"id": "src1"}],
                "raw_grounding": "z" * 200_000,
            }
        ],
        "research_hypotheses": [
            {
                "id": "h1",
                "name": "Volume acceleration",
                "category": "momentum",
                "direction": "long",
                "status": "research",
                "confidence": 0.73,
                "generated_at": "2026-09-02T18:05:00Z",
                "machine_rules": {"giant": "r" * 100_000},
            }
        ],
        "experiment_registry": [
            {
                "id": "exp-1",
                "strategy_name": "Volume acceleration",
                "status": "complete",
                "current_stage": "paper_shadow_eligibility",
                "promotion_status": "research_only",
                "updated_at": "2026-09-02T18:06:00Z",
                "failure_reasons": ["Profitable neighborhood was too narrow."],
                "source_research": {"hypothesis_id": "h1"},
                "stages": [
                    {
                        "name": "paper_shadow_eligibility",
                        "status": "blocked",
                        "reason": "Profitable neighborhood was too narrow.",
                    }
                ],
                "full_evidence": "e" * 200_000,
            }
        ],
        "knowledge_sources": [
            {
                "id": "src1",
                "title": "Primary source",
                "source_type": "academic_peer_reviewed",
                "status": "saved",
                "url": "https://example.com/source",
                "created_at": "2026-09-02T17:00:00Z",
                "full_text": "s" * 200_000,
            }
        ],
        "predictive_ml_runs": [
            {
                "id": "ml-run-1",
                "status": "complete",
                "generated_at": "2026-09-02T16:00:00Z",
                "dataset_summary": {
                    "symbol_count": 12,
                    "row_count": 45678,
                    "market_data_integrity_contract": "raw-price-v3",
                },
                "probability_models": [
                    {
                        "id": "model-heavy",
                        "target": "continuation",
                        "coefficients": [1.0] * 100_000,
                    }
                ],
            }
        ],
        "research_system": {
            "status": "running",
            "last_cycle_at": "2026-09-02T20:01:00Z",
        },
    }


@patch("predictive_model_registry.ready_shadow_models")
def test_research_ml_summary_is_bounded_and_drops_large_artifacts(ready):
    ready.return_value = [
        {
            "id": "shadow-1",
            "target": "continuation",
            "session_mode": "regular",
            "model_type": "logistic",
            "shadow_scoring_enabled": True,
            "coefficients": [0.1] * 100_000,
        }
    ]
    result = build_research_ml_summary(fixture_library(), limit=10)
    assert result["bounded"] is True
    assert result["research_only"] is True
    assert result["affects_live_ranking"] is False
    assert result["affects_execution"] is False
    assert result["counts"]["active_cloud_jobs"] == 1
    assert result["counts"]["hypotheses"] == 1
    assert result["counts"]["experiments"] == 1
    assert result["counts"]["sources"] == 1
    assert result["counts"]["ready_shadow_models"] == 1
    assert result["queue"][0]["progress"] == 0.64
    assert result["queue"][0]["message"] == "Testing family 7 of 10"
    assert "payload" not in result["queue"][0]
    assert "raw_grounding" not in result["research_runs"][0]
    assert "machine_rules" not in result["hypotheses"][0]
    assert "full_evidence" not in result["experiments"][0]
    assert result["experiments"][0]["reason"] == "Profitable neighborhood was too narrow."
    assert "full_text" not in result["sources"][0]
    assert "probability_models" not in result["predictive_ml_runs"][0]
    assert "coefficients" not in result["ready_shadow_models"][0]
    assert len(str(result)) < 25_000


def test_research_ml_summary_honors_section_limit():
    library = fixture_library()
    library["research_hypotheses"] = [
        {
            "id": f"h-{index}",
            "name": f"Hypothesis {index}",
            "generated_at": f"2026-09-02T{index % 24:02d}:00:00Z",
        }
        for index in range(150)
    ]
    result = build_research_ml_summary(library, limit=7)
    assert len(result["hypotheses"]) == 7
    assert result["limit_per_section"] == 7


def test_research_ml_summary_route_stays_local_and_read_only():
    decision = RoutingPolicy().decide(
        JobRequest(
            "library.research_ml_summary",
            {"limit": 30},
            requested_target=ExecutionTarget.AUTO,
        )
    )
    assert decision.target == ExecutionTarget.LOCAL
    assert "immediate local response" in decision.reason
