from __future__ import annotations

from datetime import datetime, timezone
import unittest

import trading_research_orchestrator as research


class ResearchQueueTests(unittest.TestCase):
    def test_stock_finder_is_a_supported_durable_job_type(self):
        library, job = research.enqueue_research_job(
            {},
            "stock_finder",
            {"symbol": "SDOT", "profile": "Very Deep"},
        )
        self.assertIn("stock_finder", research.SUPPORTED_RESEARCH_JOB_TYPES)
        self.assertEqual(job["type"], "stock_finder")
        self.assertEqual(library["research_queue"][0]["status"], "queued")

    def test_seed_cycle_is_deduplicated_per_day(self):
        library = {"strategies": []}
        library, first = research.seed_continuous_research_cycle(
            library,
            topics=["Topic A", "Topic B"],
            cycle_date="2026-08-28",
        )
        self.assertEqual(first, 2)
        library, second = research.seed_continuous_research_cycle(
            library,
            topics=["Topic A", "Topic B"],
            cycle_date="2026-08-28",
        )
        self.assertEqual(second, 0)
        self.assertEqual(research.research_queue_status(library)["queued"], 2)

    def test_uploaded_source_is_challenged_not_treated_as_truth(self):
        library = {
            "strategies": [
                {
                    "id": "book-hypothesis-1",
                    "name": "Book pullback setup",
                    "source_type": "book_or_document",
                    "source_title": "Example trading book",
                    "source_author": "Example Author",
                    "validation_status": "unvalidated",
                    "summary": "Claims high relative volume helps pullback continuation.",
                    "machine_rules": {
                        "min_relative_volume": 3.0,
                        "breakout_lookback_bars": 20,
                    },
                }
            ]
        }
        library, added = research.seed_continuous_research_cycle(
            library,
            topics=[],
            cycle_date="2026-08-28",
            maximum_topics=1,
            source_challenge_limit=1,
        )
        self.assertEqual(added, 1)
        job = library["research_queue"][0]
        self.assertEqual(job["payload"]["origin"], "source_challenge")
        self.assertEqual(job["payload"]["source_strategy_id"], "book-hypothesis-1")
        self.assertIn("unverified claim", job["payload"]["existing_context"])
        self.assertIn("Challenge it independently", job["payload"]["existing_context"])

    def test_retrospective_teacher_observations_feed_independent_research(self):
        library = {
            "strategies": [],
            "retrospective_learning_runs": [
                {
                    "generated_at": "2026-08-28T10:00:00Z",
                    "symbol": "SDOT",
                    "timeframe": "5Min",
                    "start": "2026-08-20T13:30:00Z",
                    "end": "2026-08-28T20:00:00Z",
                    "label_counts": {
                        "upper_exhaustion_reversal": 7,
                        "avwap_pinch_upside_expansion": 4,
                    },
                    "precursor_feature_medians": {
                        "upper_exhaustion_reversal": {
                            "volume_climax_ratio": 3.2,
                            "vp_distance_to_poc_pct": 4.1,
                        }
                    },
                    "feature_layers": {
                        "volume_profile": "causal",
                        "multi_anchor_avwap": "causal",
                    },
                    "causality_policy": {
                        "future_data_allowed_for": "labels only",
                        "future_data_forbidden_for": "features",
                    },
                }
            ],
        }
        library, added = research.seed_continuous_research_cycle(
            library,
            topics=[],
            cycle_date="2026-08-28",
            maximum_topics=1,
            source_challenge_limit=1,
            retrospective_challenge_limit=1,
        )
        self.assertEqual(added, 1)
        job = library["research_queue"][0]
        self.assertEqual(job["payload"]["origin"], "retrospective_teacher")
        self.assertEqual(job["payload"]["teacher_symbol"], "SDOT")
        self.assertIn("descriptive observations", job["payload"]["existing_context"])
        self.assertIn("not proof", job["payload"]["topic"].lower())

    def test_stale_running_job_returns_to_retry_queue(self):
        library, job = research.enqueue_research_job(
            {},
            "web_research",
            {"topic": "stale"},
            priority=50,
            dedupe_key="stale",
            max_attempts=3,
        )
        library["research_queue"][0].update(
            {
                "status": "running",
                "attempts": 1,
                "worker_id": "dead-worker",
                "started_at": "2026-08-28T00:00:00Z",
                "updated_at": "2026-08-28T00:00:00Z",
            }
        )
        library, recovered = research.recover_stale_research_jobs(
            library,
            now=datetime(2026, 8, 28, 7, 0, tzinfo=timezone.utc),
            stale_after_minutes=360,
        )
        self.assertEqual(recovered, 1)
        saved = library["research_queue"][0]
        self.assertEqual(saved["status"], "retry")
        self.assertIsNone(saved["worker_id"])
        self.assertIn("Retrying from durable state", saved["last_error"])

    def test_claim_prefers_priority_and_retry_can_fail_terminally(self):
        library = {}
        library, low = research.enqueue_research_job(
            library,
            "web_research",
            {"topic": "low"},
            priority=10,
            dedupe_key="low",
            max_attempts=1,
        )
        library, high = research.enqueue_research_job(
            library,
            "web_research",
            {"topic": "high"},
            priority=90,
            dedupe_key="high",
            max_attempts=1,
        )
        library, claimed = research.claim_next_research_job(
            library,
            "test-worker",
            now=datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(claimed["id"], high["id"])
        self.assertEqual(claimed["status"], "running")
        library = research.fail_research_job(library, claimed["id"], "boom")
        failed = next(item for item in library["research_queue"] if item["id"] == claimed["id"])
        self.assertEqual(failed["status"], "failed")

    def test_failure_records_the_exact_step_and_completion_clears_it(self):
        library, job = research.enqueue_research_job(
            {},
            "stock_finder",
            {"symbol": "SDOT", "profile": "Very Deep"},
            max_attempts=2,
        )
        library, claimed = research.claim_research_job_by_id(
            library,
            "finder-worker",
            job["id"],
        )
        library = research.fail_research_job(
            library,
            claimed["id"],
            "stability worker stopped",
            failure_step="parameter_stability",
            retry_delay_minutes=1,
        )
        saved = library["research_queue"][0]
        self.assertEqual(saved["status"], "retry")
        self.assertEqual(saved["failure_step"], "parameter_stability")
        self.assertIn("Parameter Stability failed", saved["status_message"])
        library = research.finish_research_job(
            library,
            claimed["id"],
            result_ref="distributed-finder:SDOT:Very Deep:done",
        )
        saved = library["research_queue"][0]
        self.assertEqual(saved["status"], "complete")
        self.assertIsNone(saved["failure_step"])
        self.assertIsNone(saved["last_error"])


class SourceQualityTests(unittest.TestCase):
    def test_primary_and_social_sources_are_not_treated_equally(self):
        primary = research.source_quality_score(
            {
                "source_type": "unknown",
                "url": "https://www.sec.gov/example",
            }
        )
        social = research.source_quality_score(
            {
                "source_type": "institutional_research",
                "url": "https://www.reddit.com/r/stocks/example",
            }
        )
        self.assertGreater(primary, social)
        self.assertGreaterEqual(primary, 90)
        self.assertLessEqual(social, 30)


class ResearchMergeTests(unittest.TestCase):
    def sample_research(self):
        return {
            "generated_at": "2026-08-28T12:00:00Z",
            "interaction_id": "interaction-1",
            "title": "Momentum evidence",
            "summary": "A grounded review.",
            "model": research.DEFAULT_GEMINI_BULK_RESEARCH_MODEL,
            "model_role": "bulk_research",
            "paid_fallback_used": False,
            "retrieved_sources": [],
            "contradictions": ["Evidence varies by liquidity."],
            "follow_up_questions": ["Does this hold in low-liquidity stocks?"],
            "sources": [
                {
                    "id": "s1",
                    "title": "Exchange research",
                    "url": "https://example.exchange/research",
                    "source_type": "exchange_official",
                    "published_at": "2026-01-01",
                    "support_summary": "Documents execution behavior.",
                    "source_quality_score": 92,
                },
                {
                    "id": "s2",
                    "title": "Academic paper",
                    "url": "https://doi.org/10.0000/example",
                    "source_type": "academic_peer_reviewed",
                    "published_at": "2025-01-01",
                    "support_summary": "Finds volume continuation.",
                    "source_quality_score": 90,
                },
            ],
            "hypotheses": [
                {
                    "name": "High RVOL continuation",
                    "category": "momentum",
                    "direction": "long",
                    "statement": "High relative volume may improve breakout continuation.",
                    "why_it_might_work": "Attention and liquidity concentrate.",
                    "market_scope": "Intraday liquid U.S. equities",
                    "machine_rules": {
                        "min_relative_volume": 3.0,
                        "breakout_lookback_bars": 20,
                    },
                    "unresolved_rules": [],
                    "supporting_source_ids": ["s1", "s2"],
                    "contradicting_source_ids": [],
                    "confidence": 75,
                    "novelty": 45,
                }
            ],
        }

    def test_grounded_research_creates_specialist_job(self):
        library, run_id, hypothesis_ids = research.merge_grounded_research(
            {},
            self.sample_research(),
            topic="momentum",
            origin_job_id="job-1",
        )
        self.assertTrue(run_id.startswith("web-"))
        self.assertEqual(len(hypothesis_ids), 1)
        hypothesis = research.find_research_hypothesis(library, hypothesis_ids[0])
        self.assertEqual(hypothesis["status"], "awaiting_specialist")
        self.assertGreaterEqual(hypothesis["source_quality_score"], 80)
        specialist_jobs = [
            item for item in library["research_queue"]
            if item["type"] == "specialist_review"
        ]
        self.assertEqual(len(specialist_jobs), 1)

    def test_specialist_promotion_materializes_unvalidated_strategy(self):
        library, _, hypothesis_ids = research.merge_grounded_research(
            {},
            self.sample_research(),
            topic="momentum",
        )
        hypothesis = research.find_research_hypothesis(library, hypothesis_ids[0])
        review = {
            "decision": "promote_to_validation",
            "reason": "Testable and supported enough to falsify.",
            "confidence": 82,
            "revised_hypothesis": {
                "name": hypothesis["name"],
                "category": hypothesis["category"],
                "direction": "long",
                "statement": hypothesis["statement"],
                "why_it_might_work": hypothesis["why_it_might_work"],
                "market_scope": hypothesis["market_scope"],
                "machine_rules": hypothesis["machine_rules"],
                "unresolved_rules": [],
                "supporting_source_ids": ["s1", "s2"],
                "contradicting_source_ids": [],
                "confidence": 80,
                "novelty": 45,
            },
            "risk_flags": ["Execution costs still need stress testing."],
            "follow_up_questions": [],
            "generated_at": "2026-08-28T12:30:00Z",
        }
        library, strategy_id = research.apply_specialist_review(
            library,
            hypothesis_ids[0],
            review,
        )
        self.assertTrue(strategy_id.startswith("webresearch-"))
        strategy = next(item for item in library["strategies"] if item["id"] == strategy_id)
        self.assertEqual(strategy["validation_status"], "unvalidated")
        self.assertFalse(strategy["approved"])
        self.assertEqual(strategy["paper_validation_status"], "not_ready")
        validation_jobs = [
            item for item in library["research_queue"]
            if item["type"] == "autonomous_validation"
        ]
        self.assertEqual(len(validation_jobs), 1)

    def test_validation_result_syncs_back_to_hypothesis(self):
        library, _, hypothesis_ids = research.merge_grounded_research(
            {},
            self.sample_research(),
            topic="momentum",
        )
        hypothesis = research.find_research_hypothesis(library, hypothesis_ids[0])
        review = {
            "decision": "promote_to_validation",
            "reason": "Testable enough to falsify.",
            "confidence": 82,
            "revised_hypothesis": {
                "name": hypothesis["name"],
                "category": hypothesis["category"],
                "direction": "long",
                "statement": hypothesis["statement"],
                "why_it_might_work": hypothesis["why_it_might_work"],
                "market_scope": hypothesis["market_scope"],
                "machine_rules": hypothesis["machine_rules"],
                "unresolved_rules": [],
                "supporting_source_ids": ["s1", "s2"],
                "contradicting_source_ids": [],
                "confidence": 80,
                "novelty": 45,
            },
            "risk_flags": [],
            "follow_up_questions": [],
            "generated_at": "2026-08-28T12:30:00Z",
        }
        library, strategy_id = research.apply_specialist_review(
            library,
            hypothesis_ids[0],
            review,
        )
        report = {
            "generated_at": "2026-08-28T13:00:00Z",
            "results": [
                {
                    "strategy_id": strategy_id,
                    "validation_status": "validated",
                    "global_score": 81.5,
                    "anchor_symbol": "TEST",
                    "candidate_symbols": ["TEST", "TWO"],
                    "gate_reasons": [],
                }
            ],
        }
        library = research.sync_hypothesis_validation_results(library, report)
        saved = research.find_research_hypothesis(library, hypothesis_ids[0])
        self.assertEqual(saved["status"], "validated")
        self.assertEqual(saved["validation_summary"]["global_score"], 81.5)

    def test_pro_cannot_promote_without_machine_testable_rules(self):
        research_payload = self.sample_research()
        research_payload["hypotheses"][0]["machine_rules"] = {}
        library, _, hypothesis_ids = research.merge_grounded_research(
            {},
            research_payload,
            topic="momentum",
        )
        hypothesis = research.find_research_hypothesis(library, hypothesis_ids[0])
        review = {
            "decision": "promote_to_validation",
            "reason": "Interesting but still qualitative.",
            "confidence": 70,
            "revised_hypothesis": {
                **hypothesis,
                "machine_rules": {},
            },
            "risk_flags": [],
            "follow_up_questions": [],
            "generated_at": "2026-08-28T12:30:00Z",
        }
        library, strategy_id = research.apply_specialist_review(
            library,
            hypothesis_ids[0],
            review,
        )
        self.assertIsNone(strategy_id)
        saved = research.find_research_hypothesis(library, hypothesis_ids[0])
        self.assertEqual(saved["status"], "needs_more_research")


if __name__ == "__main__":
    unittest.main()



class PredictiveMlBackfillQueueTests(unittest.TestCase):
    def test_predictive_ml_backfill_is_supported_and_high_priority(self):
        now = datetime(2026, 8, 29, 22, 0, tzinfo=timezone.utc)
        library, job = research.ensure_predictive_ml_backfill_job(
            {},
            now=now,
        )
        self.assertIn(
            "predictive_ml_backfill",
            research.SUPPORTED_RESEARCH_JOB_TYPES,
        )
        self.assertIsNotNone(job)
        self.assertEqual(job["type"], "predictive_ml_backfill")
        self.assertEqual(job["priority"], 95)
        self.assertEqual(
            library["research_system"]["predictive_ml_backfill_status"]["status"],
            "queued",
        )

    def test_predictive_ml_backfill_dedupes_same_day(self):
        now = datetime(2026, 8, 29, 22, 0, tzinfo=timezone.utc)
        library, first = research.ensure_predictive_ml_backfill_job({}, now=now)
        library, second = research.ensure_predictive_ml_backfill_job(
            library,
            now=now,
        )
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        jobs = [
            item
            for item in library["research_queue"]
            if item["type"] == "predictive_ml_backfill"
        ]
        self.assertEqual(len(jobs), 1)

    def test_recent_completed_backfill_suppresses_retraining(self):
        now = datetime(2026, 8, 29, 22, 0, tzinfo=timezone.utc)
        library = {
            "research_system": {
                "predictive_ml_backfill_status": {
                    "status": "complete",
                    "completed_at": "2026-08-29T12:00:00Z",
                    "model_suite_version": research.PREDICTIVE_ML_BACKFILL_SUITE_VERSION,
                }
            }
        }
        library, job = research.ensure_predictive_ml_backfill_job(
            library,
            now=now,
            freshness_hours=20,
        )
        self.assertIsNone(job)
        self.assertEqual(
            research.research_queue_status(library)["active"],
            0,
        )



def test_older_model_suite_forces_same_day_backfill_upgrade():
    now = datetime(2026, 8, 29, 22, 0, tzinfo=timezone.utc)
    library = {
        "research_system": {
            "predictive_ml_backfill_status": {
                "status": "complete",
                "completed_at": "2026-08-29T21:00:00Z",
                "model_suite_version": research.PREDICTIVE_ML_BACKFILL_SUITE_VERSION - 1,
            }
        }
    }
    library, job = research.ensure_predictive_ml_backfill_job(
        library,
        now=now,
        freshness_hours=20,
    )
    assert job is not None
    assert job["payload"]["model_suite_version"] == research.PREDICTIVE_ML_BACKFILL_SUITE_VERSION
    assert f"v{research.PREDICTIVE_ML_BACKFILL_SUITE_VERSION}" in job["dedupe_key"]


class QueueReliabilityRegressionTests(unittest.TestCase):
    def test_equal_priority_claims_oldest_job_first(self):
        now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
        library, older = research.enqueue_research_job(
            {}, "web_research", {"topic": "older"}, priority=50, dedupe_key="older"
        )
        library, newer = research.enqueue_research_job(
            library, "web_research", {"topic": "newer"}, priority=50, dedupe_key="newer"
        )
        for item in library["research_queue"]:
            if item["id"] == older["id"]:
                item["created_at"] = "2026-08-30T10:00:00Z"
            elif item["id"] == newer["id"]:
                item["created_at"] = "2026-08-30T11:00:00Z"
        library, claimed = research.claim_next_research_job(library, "worker", now=now)
        self.assertEqual(claimed["id"], older["id"])

    def test_validation_precedes_fresh_followup_research(self):
        now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
        library, followup = research.enqueue_research_job(
            {}, "web_research", {"topic": "followup"}, priority=60, dedupe_key="followup"
        )
        library, validation = research.enqueue_research_job(
            library,
            "autonomous_validation",
            {},
            priority=45,
            dedupe_key="validation",
        )
        library, claimed = research.claim_next_research_job(library, "worker", now=now)
        self.assertEqual(claimed["id"], validation["id"])

    def test_very_old_work_ages_ahead_of_fresh_high_priority_work(self):
        now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
        library, old = research.enqueue_research_job(
            {}, "specialist_review", {}, priority=30, dedupe_key="old"
        )
        library, fresh = research.enqueue_research_job(
            library, "specialist_review", {}, priority=90, dedupe_key="fresh"
        )
        for item in library["research_queue"]:
            if item["id"] == old["id"]:
                item["created_at"] = "2026-08-28T19:00:00Z"
            elif item["id"] == fresh["id"]:
                item["created_at"] = "2026-08-30T19:59:00Z"
        library, claimed = research.claim_next_research_job(library, "worker", now=now)
        self.assertEqual(claimed["id"], old["id"])

    def test_retention_never_drops_active_jobs(self):
        terminal = [
            {
                "id": f"complete-{index}",
                "type": "web_research",
                "status": "complete",
                "priority": 1,
                "created_at": f"2026-08-01T00:{index % 60:02d}:00Z",
            }
            for index in range(305)
        ]
        active = [
            {
                "id": "old-active",
                "type": "specialist_review",
                "status": "queued",
                "priority": 10,
                "created_at": "2026-08-01T00:00:00Z",
            }
        ]
        library, new_job = research.enqueue_research_job(
            {"research_queue": active + terminal},
            "web_research",
            {"topic": "new"},
            dedupe_key="new-active",
        )
        active_ids = {
            item["id"]
            for item in library["research_queue"]
            if item.get("status") in research.ACTIVE_RESEARCH_JOB_STATUSES
        }
        self.assertIn("old-active", active_ids)
        self.assertIn(new_job["id"], active_ids)
        self.assertEqual(
            len(
                [
                    item
                    for item in library["research_queue"]
                    if item.get("status") not in research.ACTIVE_RESEARCH_JOB_STATUSES
                ]
            ),
            research.MAX_TERMINAL_QUEUE_HISTORY,
        )

    def test_completed_job_is_not_requeued_by_failure_handler(self):
        library = {
            "research_queue": [
                {
                    "id": "done",
                    "type": "web_research",
                    "status": "complete",
                    "attempts": 1,
                    "max_attempts": 3,
                }
            ]
        }
        updated = research.fail_research_job(library, "done", "cloud write failed")
        self.assertEqual(updated["research_queue"][0]["status"], "complete")

    def test_specialist_followup_does_not_branch_recursively(self):
        library = {
            "research_hypotheses": [
                {
                    "id": "h-child",
                    "research_run_id": "run-child",
                    "status": "awaiting_specialist",
                }
            ],
            "external_research_runs": [
                {"id": "run-child", "origin_job_id": "followup-job"}
            ],
            "research_queue": [
                {
                    "id": "followup-job",
                    "type": "web_research",
                    "status": "complete",
                    "payload": {
                        "origin": "specialist_follow_up",
                        "followup_depth": 1,
                    },
                }
            ],
        }
        review = {
            "decision": "keep_researching",
            "reason": "More evidence needed.",
            "confidence": 60,
            "follow_up_questions": ["Should this branch again?"],
            "generated_at": "2026-08-30T20:00:00Z",
        }
        updated, _ = research.apply_specialist_review(library, "h-child", review)
        new_web_jobs = [
            item
            for item in updated["research_queue"]
            if item.get("type") == "web_research" and item.get("id") != "followup-job"
        ]
        self.assertEqual(new_web_jobs, [])


class ResearchMemoryRegressionTests(unittest.TestCase):
    def _packet(self, generated_at: str, interaction_id: str, quality_type: str = "academic_peer_reviewed"):
        return {
            "generated_at": generated_at,
            "interaction_id": interaction_id,
            "title": "Repeated concept",
            "summary": "Repeated concept research.",
            "model": research.DEFAULT_GEMINI_BULK_RESEARCH_MODEL,
            "model_role": "bulk_research",
            "paid_fallback_used": False,
            "retrieved_sources": [],
            "contradictions": [],
            "follow_up_questions": [],
            "sources": [
                {
                    "id": "src",
                    "title": "Underlying study",
                    "url": "https://doi.org/10.1000/repeated",
                    "source_type": quality_type,
                    "published_at": "2025-01-01",
                    "support_summary": "Same evidence base.",
                    "source_quality_score": research.SOURCE_TYPE_SCORES[quality_type],
                }
            ],
            "hypotheses": [
                {
                    "name": "Repeated RVOL concept",
                    "category": "momentum",
                    "direction": "long",
                    "statement": "High RVOL may improve continuation.",
                    "why_it_might_work": "Liquidity concentration.",
                    "market_scope": "US equities",
                    "machine_rules": {"min_relative_volume": 3.0},
                    "unresolved_rules": [],
                    "supporting_source_ids": ["src"],
                    "contradicting_source_ids": [],
                    "confidence": 70,
                    "novelty": 30,
                }
            ],
        }

    def test_prior_rejected_concept_is_suppressed_without_materially_better_evidence(self):
        library, _, first_ids = research.merge_grounded_research(
            {}, self._packet("2026-08-29T10:00:00Z", "one"), topic="rvol"
        )
        for item in library["research_hypotheses"]:
            if item["id"] == first_ids[0]:
                item["status"] = "rejected"
        before_jobs = len(
            [item for item in library["research_queue"] if item.get("type") == "specialist_review"]
        )
        library, _, second_ids = research.merge_grounded_research(
            library, self._packet("2026-08-30T10:00:00Z", "two"), topic="rvol"
        )
        second = research.find_research_hypothesis(library, second_ids[0])
        self.assertEqual(second["status"], "duplicate_prior_outcome")
        after_jobs = len(
            [item for item in library["research_queue"] if item.get("type") == "specialist_review"]
        )
        self.assertEqual(after_jobs, before_jobs)

    def test_fake_sec_reference_in_query_string_gets_no_government_boost(self):
        score = research.source_quality_score(
            {
                "source_type": "unknown",
                "url": "https://example.invalid/?next=sec.gov/report",
            }
        )
        self.assertEqual(score, research.SOURCE_TYPE_SCORES["unknown"])
