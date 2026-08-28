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
