"""Tests for unified Trading Intelligence strategy semantics."""

import os
import tempfile
import unittest
from unittest.mock import patch

from trading_intelligence_core import (
    DEFAULT_GEMINI_BOOK_MODEL,
    DEFAULT_GEMINI_BOOK_SPECIALIST_MODEL,
    GeminiBookAnalyzer,
    apply_compiler_suggestions,
    merge_ingestion_checkpoint_strategies,
    paper_execution_fidelity,
    effective_strategy_for_live,
    effective_strategy_for_research,
    prepare_strategies_with_ai,
    reconcile_knowledge_sources,
    research_readiness,
    strategy_integrity_report,
    upgrade_native_strategy_rules,
)
from youtube_strategy_engine import AppError


class KnowledgeSourceReconciliationTests(unittest.TestCase):
    def test_repairs_generic_book_title_and_author_from_saved_filename(self):
        library = {
            "knowledge_sources": [
                {
                    "id": "book-1",
                    "source_type": "book_or_document",
                    "title": "Uploaded source",
                    "author": "",
                    "filename": (
                        "How to Day Trade A Detailed Guide to Day Trading Strategies "
                        "(Ross Cameron) (z-library.sk, 1lib.sk, z-lib.sk).pdf"
                    ),
                }
            ],
            "strategies": [
                {
                    "id": "strategy-1",
                    "source_id": "book-1",
                    "source_type": "book_or_document",
                    "source_title": "Uploaded source",
                    "source_author": "",
                }
            ],
        }

        reconciled, changed = reconcile_knowledge_sources(library)

        self.assertTrue(changed)
        source = reconciled["knowledge_sources"][0]
        self.assertEqual(
            source["title"],
            "How to Day Trade A Detailed Guide to Day Trading Strategies",
        )
        self.assertEqual(source["author"], "Ross Cameron")
        self.assertEqual(
            reconciled["strategies"][0]["source_title"],
            source["title"],
        )
        self.assertEqual(
            reconciled["strategies"][0]["source_author"],
            "Ross Cameron",
        )

    def test_recovers_real_youtube_sources_from_saved_strategy_provenance(self):
        library = {
            "knowledge_sources": [],
            "strategies": [
                {
                    "id": "yt-strategy-1",
                    "source_id": "yt-abc",
                    "source_type": "youtube",
                    "source_title": "Entries and Exits for Momentum Day Trading",
                    "source_author": "Ross Cameron",
                    "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
                    "analyzed_at": "2026-08-27T00:31:44Z",
                },
                {
                    "id": "yt-strategy-2",
                    "source_id": "yt-abc",
                    "source_type": "youtube",
                    "source_title": "Entries and Exits for Momentum Day Trading",
                    "source_author": "Ross Cameron",
                    "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
                },
                {
                    "id": "synthetic-1",
                    "source_id": "legacy-combined",
                    "source_type": "youtube",
                    "source_title": "Combined lessons from 1 analyzed video",
                    "source_url": "",
                },
            ],
        }

        reconciled, changed = reconcile_knowledge_sources(library)

        self.assertTrue(changed)
        self.assertEqual(len(reconciled["knowledge_sources"]), 1)
        source = reconciled["knowledge_sources"][0]
        self.assertEqual(source["id"], "yt-abc")
        self.assertEqual(source["source_type"], "youtube")
        self.assertEqual(
            source["title"],
            "Entries and Exits for Momentum Day Trading",
        )
        self.assertEqual(source["author"], "Ross Cameron")
        self.assertEqual(source["strategy_count"], 2)
        self.assertTrue(source["recovered_from_strategies"])
        self.assertEqual(source["analysis_stage"], "complete")
        self.assertEqual(source["source_claim_status"], "unverified_source_claim")
        self.assertEqual(source["source_role"], "hypothesis_generator")
        for strategy in reconciled["strategies"]:
            if strategy.get("source_id") == "yt-abc":
                self.assertEqual(strategy["source_claim_status"], "unverified_source_claim")
                self.assertEqual(strategy["source_role"], "hypothesis_generator")

    def test_reconciliation_is_stable_after_first_migration(self):
        library = {
            "knowledge_sources": [],
            "strategies": [
                {
                    "id": "yt-strategy-1",
                    "source_id": "yt-abc",
                    "source_type": "youtube",
                    "source_title": "Scalp Trading for Beginners",
                    "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
                }
            ],
        }

        first, changed = reconcile_knowledge_sources(library)
        second, changed_again = reconcile_knowledge_sources(first)

        self.assertTrue(changed)
        self.assertFalse(changed_again)
        self.assertEqual(first["knowledge_sources"], second["knowledge_sources"])


class EmaSemanticCoverageTests(unittest.TestCase):
    def _strategy(self):
        return {
            "id": "ema-pullback",
            "source_type": "book_or_document",
            "name": "Moving Average Trend Pullback (9 EMA)",
            "summary": "Trend-following long setup that buys moving average pullbacks near the 9 EMA.",
            "indicators": ["9 EMA", "20 EMA", "200 EMA", "VWAP"],
            "entry_conditions": [
                "Price consolidates back to tap or trade close to the 9 EMA support.",
                "Preferably enter on the 1st or 2nd moving average pullback.",
            ],
            "risk_rules": ["Place a conservative stop slightly below the 9 EMA support level."],
            "avoid_conditions": ["Avoid buying long when price is below the 200 EMA."],
            "market_context": [
                "Stock is in an active uptrend trading above its moving averages (9 EMA, 20 EMA, 200 EMA) and VWAP."
            ],
            "machine_rules": {"above_vwap": True},
            "evidence": [
                {"location": "p.54", "description": "EMA pullback", "source_excerpt": "short"},
                {"location": "p.57", "description": "200 EMA", "source_excerpt": "short"},
            ],
            "unresolved_rules": ["Discretionary evaluation of trend strength and tape."],
        }

    def test_native_upgrade_backfills_explicit_ema_structure(self):
        upgraded = upgrade_native_strategy_rules(self._strategy())
        rules = upgraded["machine_rules"]
        self.assertEqual(rules["fast_ema_period"], 9)
        self.assertEqual(rules["slow_ema_period"], 20)
        self.assertEqual(rules["trend_ema_period"], 200)
        self.assertTrue(rules["require_fast_ema_pullback"])
        self.assertTrue(rules["require_price_above_slow_ema"])
        self.assertTrue(rules["require_price_above_trend_ema"])
        self.assertEqual(rules["max_pullback_number"], 2)
        self.assertTrue(rules["stop_below_fast_ema"])

    def test_semantic_gate_blocks_backtest_until_near_ema_is_measurable(self):
        upgraded = upgrade_native_strategy_rules(self._strategy())
        readiness = research_readiness(upgraded)
        self.assertEqual(readiness["label"], "partially_modeled")
        self.assertLess(readiness["semantic_coverage_pct"], readiness["semantic_coverage_gate_pct"])
        self.assertTrue(
            any("proximity" in item.lower() for item in readiness["semantic_missing_requirements"])
        )

        upgraded["research_rule_overrides"] = {
            "pullback_touch_tolerance_pct": 0.75,
            "stop_ema_buffer_pct": 0.25,
        }
        ready = research_readiness(upgraded)
        self.assertEqual(ready["label"], "ready_for_backtest")
        self.assertEqual(ready["semantic_coverage_pct"], 100.0)


class NativeRuleUpgradeTests(unittest.TestCase):
    def test_existing_continuation_breakout_is_upgraded_without_inventing_author_number(self):
        strategy = {
            "id": "follow-through",
            "source_type": "book_or_document",
            "name": "Follow Through / Continuation Breakout",
            "summary": (
                "Trades momentum continuation on stocks that were extremely active in the "
                "previous trading session, entering when price makes a fresh breakout over "
                "the previous day's high."
            ),
            "entry_conditions": [
                "Enter on a fresh breakout over the previous day's high."
            ],
            "machine_rules": {},
            "evidence": [
                {"location": "p. 10", "description": "setup", "source_excerpt": "short"}
            ],
            "validation_status": "unvalidated",
        }
        upgraded = upgrade_native_strategy_rules(strategy)
        self.assertTrue(upgraded["machine_rules"]["previous_day_high_breakout"])
        self.assertIsNone(upgraded["machine_rules"]["min_previous_day_volume_ratio"])
        self.assertEqual(
            upgraded["research_rule_overrides"]["min_previous_day_volume_ratio"],
            2.0,
        )
        assumption = next(
            item
            for item in upgraded["compiler_assumptions"]
            if item.get("target_rule") == "min_previous_day_volume_ratio"
        )
        self.assertTrue(assumption["is_research_assumption"])
        self.assertIn("not an author-stated threshold", assumption["rationale"])
        readiness = research_readiness(upgraded)
        self.assertEqual(readiness["label"], "ready_for_backtest")
        self.assertGreater(readiness["score"], 16)

    def test_prior_day_upgrade_does_not_override_explicit_author_threshold(self):
        strategy = {
            "summary": (
                "Trade stocks extremely active in the previous session and break the prior day's high."
            ),
            "machine_rules": {
                "previous_day_high_breakout": True,
                "min_previous_day_volume_ratio": 3.0,
            },
        }
        upgraded = upgrade_native_strategy_rules(strategy)
        self.assertEqual(upgraded["machine_rules"]["min_previous_day_volume_ratio"], 3.0)
        self.assertFalse(
            upgraded.get("research_rule_overrides", {}).get("min_previous_day_volume_ratio")
        )


class EffectiveStrategyTests(unittest.TestCase):
    def test_validated_strategy_uses_frozen_validated_rules(self):
        strategy = {
            "validation_status": "validated",
            "machine_rules": {"min_relative_volume": 2.0},
            "validated_rules": {"min_relative_volume": 4.0},
        }
        effective = effective_strategy_for_live(strategy)
        self.assertEqual(effective["machine_rules"]["min_relative_volume"], 4.0)
        self.assertTrue(effective["using_validated_rules"])
        self.assertEqual(strategy["machine_rules"]["min_relative_volume"], 2.0)

    def test_unvalidated_strategy_does_not_use_saved_validated_rules(self):
        strategy = {
            "validation_status": "research_only",
            "machine_rules": {"min_relative_volume": 2.0},
            "validated_rules": {"min_relative_volume": 4.0},
        }
        effective = effective_strategy_for_live(strategy)
        self.assertEqual(effective["machine_rules"]["min_relative_volume"], 2.0)
        self.assertFalse(effective["using_validated_rules"])

    def test_research_assumptions_fill_gaps_but_never_replace_source_rules(self):
        strategy = {
            "machine_rules": {"min_relative_volume": 2.0},
            "research_rule_overrides": {
                "min_relative_volume": 5.0,
                "max_vwap_distance_pct": 3.0,
            },
        }
        effective = effective_strategy_for_research(strategy)
        self.assertEqual(effective["machine_rules"]["min_relative_volume"], 2.0)
        self.assertEqual(effective["machine_rules"]["max_vwap_distance_pct"], 3.0)
        self.assertTrue(effective["using_research_overrides"])


    def test_ai_autopilot_never_overwrites_explicit_author_rule(self):
        strategy = {
            "source_type": "book_or_document",
            "machine_rules": {"min_relative_volume": 2.0},
            "evidence": [{"location": "p. 10", "description": "RVOL rule", "source_excerpt": "short"}],
        }
        compiled = {
            "model": "gemini-test",
            "generated_at": "2026-08-26T00:00:00Z",
            "summary": "test",
            "suggestions": [
                {
                    "target_rule": "min_relative_volume",
                    "parsed_value": 5.0,
                    "confidence": 99,
                    "source_requirement": "high RVOL",
                    "rationale": "proxy",
                },
                {
                    "target_rule": "max_vwap_distance_pct",
                    "parsed_value": 3.0,
                    "confidence": 90,
                    "source_requirement": "do not chase",
                    "rationale": "proxy",
                },
            ],
            "unmapped_requirements": [],
        }
        prepared = apply_compiler_suggestions(strategy, compiled, minimum_confidence=65)
        self.assertEqual(prepared["machine_rules"]["min_relative_volume"], 2.0)
        self.assertNotIn("min_relative_volume", prepared["research_rule_overrides"])
        self.assertEqual(prepared["research_rule_overrides"]["max_vwap_distance_pct"], 3.0)
        self.assertEqual(prepared["compiler_assumptions"][-1]["accepted_by"], "ai_autopilot")
        self.assertNotIn("min_relative_volume", prepared.get("ai_candidate_rule_options") or {})
        self.assertIn("max_vwap_distance_pct", prepared["ai_candidate_rule_options"])
        self.assertIn(3.0, prepared["ai_candidate_rule_options"]["max_vwap_distance_pct"])
        self.assertGreater(
            len(prepared["ai_candidate_rule_options"]["max_vwap_distance_pct"]),
            1,
        )
        self.assertEqual(
            prepared["compiler_assumptions"][-1]["test_policy"],
            "optimizer_then_walk_forward_holdout",
        )

    def test_ai_autopilot_skips_low_confidence_proxy(self):
        strategy = {"source_type": "book_or_document", "machine_rules": {}}
        compiled = {
            "model": "gemini-test",
            "generated_at": "2026-08-26T00:00:00Z",
            "suggestions": [
                {
                    "target_rule": "min_relative_volume",
                    "parsed_value": 2.0,
                    "confidence": 40,
                }
            ],
            "unmapped_requirements": [],
        }
        prepared = apply_compiler_suggestions(strategy, compiled, minimum_confidence=65)
        self.assertFalse(prepared.get("research_rule_overrides"))
        self.assertFalse(prepared.get("ai_candidate_rule_options"))
        self.assertEqual(prepared["autopilot_preparation"]["skipped_low_confidence"], 1)

    def test_research_readiness_requires_objective_entry_rule(self):
        not_ready = research_readiness(
            {
                "source_type": "book_or_document",
                "machine_rules": {"stop_loss_pct": 2.0, "reward_risk": 2.0},
                "evidence": [{"location": "p. 4", "description": "risk", "source_excerpt": "short"}],
            }
        )
        self.assertEqual(not_ready["label"], "needs_translation")

        ready = research_readiness(
            {
                "source_type": "book_or_document",
                "machine_rules": {"min_relative_volume": 2.0, "stop_loss_pct": 2.0},
                "evidence": [{"location": "p. 4", "description": "entry", "source_excerpt": "short"}],
            }
        )
        self.assertEqual(ready["label"], "ready_for_backtest")




class ProgressiveCheckpointMergeTests(unittest.TestCase):
    def test_progressive_checkpoint_replaces_stale_same_source_strategy(self):
        existing = [
            {
                "id": "s1",
                "source_id": "book1",
                "name": "Breakout",
                "machine_rules": {"min_relative_volume": None},
            },
            {
                "id": "other",
                "source_id": "book2",
                "name": "Other",
                "machine_rules": {},
            },
        ]
        additions = [
            {
                "id": "s1",
                "source_id": "book1",
                "name": "Breakout",
                "machine_rules": {"min_relative_volume": 2.0},
            }
        ]
        merged = merge_ingestion_checkpoint_strategies(
            existing,
            additions,
            source_id="book1",
            replace_source=True,
        )
        by_id = {item["id"]: item for item in merged}
        self.assertEqual(by_id["s1"]["machine_rules"]["min_relative_volume"], 2.0)
        self.assertIn("other", by_id)
        self.assertNotIn("latest_extraction", by_id["s1"])


class BookModelRoutingTests(unittest.TestCase):
    def test_book_defaults_to_flash_with_pro_as_specialist(self):
        analyzer = GeminiBookAnalyzer("key")
        self.assertEqual(analyzer.primary_model, DEFAULT_GEMINI_BOOK_MODEL)
        self.assertEqual(analyzer.primary_model, "gemini-3.6-flash")
        self.assertEqual(analyzer.specialist_model, DEFAULT_GEMINI_BOOK_SPECIALIST_MODEL)
        self.assertEqual(analyzer.specialist_model, "gemini-3.1-pro-preview")
        self.assertNotIn("gemini-3.1-pro-preview", analyzer.fallback_models)

    def test_clear_high_confidence_section_does_not_escalate(self):
        analysis = {
            "source_summary": "Clear setup",
            "detected_title": "",
            "detected_author": "",
            "strategies": [
                {
                    "name": "Breakout",
                    "confidence": 92,
                    "entry_conditions": ["Break a defined high"],
                    "exit_conditions": ["Exit at target"],
                    "risk_rules": ["Stop below setup"],
                    "unresolved_rules": [],
                    "machine_rules": {"min_relative_volume": 2.0},
                    "evidence": [{"location": "p. 10", "description": "Rule", "source_excerpt": "short"}],
                }
            ],
        }
        needs, reasons = GeminiBookAnalyzer._analysis_needs_specialist(analysis)
        self.assertFalse(needs)
        self.assertEqual(reasons, [])

    def test_ambiguous_low_confidence_section_escalates_to_pro(self):
        analyzer = GeminiBookAnalyzer("key")
        primary = {
            "source_summary": "Ambiguous setup",
            "detected_title": "",
            "detected_author": "",
            "strategies": [
                {
                    "name": "Pullback",
                    "category": "momentum",
                    "confidence": 58,
                    "entry_conditions": ["Buy when it looks strong"],
                    "exit_conditions": ["Sell when momentum fades"],
                    "risk_rules": [],
                    "unresolved_rules": ["Entry timing is unclear"],
                    "machine_rules": {},
                    "evidence": [{"location": "p. 20", "description": "Setup", "source_excerpt": "short"}],
                }
            ],
        }
        specialist = {
            "source_summary": "Clarified setup",
            "detected_title": "",
            "detected_author": "",
            "strategies": [],
        }
        progress = []
        with patch.object(analyzer, "_analyze_chunk", return_value=specialist) as call:
            result = analyzer._specialist_review_chunk(
                "source text",
                primary_analysis=primary,
                title="Book",
                author="Author",
                chunk_number=2,
                chunk_count=5,
                focus="",
                progress_callback=lambda i, total, message: progress.append(message),
            )

        self.assertEqual(call.call_count, 1)
        self.assertTrue(analyzer.specialist_used)
        self.assertEqual(analyzer.specialist_sections, [2])
        self.assertEqual(analyzer.model, analyzer.primary_model)
        self.assertIn("Ambiguous setup", result["source_summary"])
        self.assertIn("Clarified setup", result["source_summary"])
        self.assertTrue(any("gemini-3.1-pro-preview" in message for message in progress))


class BookIngestionEfficiencyTests(unittest.TestCase):
    def test_long_book_uses_small_request_count(self):
        analyzer = GeminiBookAnalyzer("key")
        long_text = ("[[PAGE 1]]\nTrading setup discussion.\n\n" * 14000).strip()
        empty = {
            "source_summary": "Read",
            "detected_title": "",
            "detected_author": "",
            "strategies": [],
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"YOUTUBE_STRATEGY_DATA_DIR": directory},
        ), patch.object(
            analyzer,
            "_analyze_chunk_with_adaptive_split",
            return_value=empty,
        ):
            result = analyzer.analyze(long_text, title="Book")

        self.assertLessEqual(result["chunk_count"], 7)
        self.assertGreaterEqual(result["chunk_target_characters"], 72000)
        self.assertEqual(result["checkpoint_version"], 5)

    def test_zero_progress_legacy_checkpoint_uses_new_chunk_plan(self):
        analyzer = GeminiBookAnalyzer("key")
        long_text = ("Paragraph about momentum and VWAP.\n\n" * 9000).strip()
        empty = {
            "source_summary": "Read",
            "detected_title": "",
            "detected_author": "",
            "strategies": [],
        }
        resume = {
            "checkpoint_version": 4,
            "chunk_count": 17,
            "completed_sections": 0,
            "completed_section_indices": [],
            "strategies": [],
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"YOUTUBE_STRATEGY_DATA_DIR": directory},
        ), patch.object(
            analyzer,
            "_analyze_chunk_with_adaptive_split",
            return_value=empty,
        ):
            result = analyzer.analyze(long_text, title="Book", resume_state=resume)

        self.assertEqual(result["checkpoint_version"], 5)
        self.assertLess(result["chunk_count"], 17)

    def test_autopilot_skips_extra_ai_call_when_strategy_is_already_testable(self):
        class Compiler:
            model = "test-model"

            def compile(self, strategy):
                raise AssertionError("Compiler should not be called for an already-testable strategy.")

        strategy = {
            "id": "ready",
            "source_type": "book_or_document",
            "machine_rules": {"min_relative_volume": 2.0},
            "evidence": [
                {"location": "p.1", "description": "RVOL entry filter", "source_excerpt": "short"}
            ],
            "unresolved_rules": [],
        }
        prepared = prepare_strategies_with_ai([strategy], Compiler())
        self.assertEqual(len(prepared), 1)
        self.assertTrue(prepared[0]["autopilot_preparation"]["compiler_skipped"])
        self.assertEqual(
            prepared[0]["research_readiness"]["label"],
            "ready_for_backtest",
        )


class BookAnalyzerResilienceTests(unittest.TestCase):
    def test_quota_uses_backup_api_project_before_waiting(self):
        analyzer = GeminiBookAnalyzer(
            "primary-key",
            "gemini-3.6-flash",
            fallback_api_key="paid-key",
            fallback_model="gemini-3.5-flash",
        )
        progress_messages = []
        with patch.object(
            analyzer,
            "_analyze_chunk",
            side_effect=[
                AppError("Provider request failed (429): RESOURCE_EXHAUSTED quota retry in 60s"),
                {"source_summary": "Recovered", "strategies": []},
            ],
        ), patch("trading_intelligence_core.sleep") as sleeper:
            result = analyzer._analyze_chunk_resilient(
                "text",
                title="Book",
                author="Author",
                chunk_number=1,
                chunk_count=6,
                focus="",
                progress_callback=lambda i, total, message: progress_messages.append(message),
            )

        self.assertEqual(result["source_summary"], "Recovered")
        self.assertEqual(analyzer.api_key, "paid-key")
        self.assertTrue(analyzer.paid_fallback_used)
        sleeper.assert_not_called()
        self.assertTrue(any("backup api project" in message.lower() for message in progress_messages))

    def test_quota_uses_backup_model_before_waiting_when_no_paid_key(self):
        analyzer = GeminiBookAnalyzer(
            "primary-key",
            "gemini-3.6-flash",
            fallback_model="gemini-3.5-flash",
        )
        with patch.object(
            analyzer,
            "_analyze_chunk",
            side_effect=[
                AppError("Provider request failed (429): RESOURCE_EXHAUSTED quota retry in 60s"),
                {"source_summary": "Recovered", "strategies": []},
            ],
        ), patch("trading_intelligence_core.sleep") as sleeper:
            result = analyzer._analyze_chunk_resilient(
                "text",
                title="Book",
                author="Author",
                chunk_number=1,
                chunk_count=6,
                focus="",
                progress_callback=None,
            )

        self.assertEqual(result["source_summary"], "Recovered")
        self.assertEqual(analyzer.model, "gemini-3.5-flash")
        sleeper.assert_not_called()

    def test_503_switches_to_backup_model_after_retry_budget(self):
        analyzer = GeminiBookAnalyzer(
            "primary-key",
            "gemini-3.7-flash",
            fallback_model="gemini-3.6-flash",
        )
        progress_messages = []

        with patch("trading_intelligence_core.BOOK_TRANSIENT_RETRIES_PER_MODEL", 0), patch.object(
            analyzer,
            "_analyze_chunk",
            side_effect=[
                AppError("Provider request failed (503): high demand"),
                {"source_summary": "Recovered", "strategies": []},
            ],
        ):
            result = analyzer._analyze_chunk_resilient(
                "text",
                title="Book",
                author="Author",
                chunk_number=1,
                chunk_count=5,
                focus="",
                progress_callback=lambda i, total, message: progress_messages.append(message),
            )

        self.assertEqual(result["source_summary"], "Recovered")
        self.assertEqual(analyzer.model, "gemini-3.6-flash")
        self.assertTrue(analyzer.model_fallback_used)
        self.assertTrue(any("backup model" in message.lower() for message in progress_messages))

    def test_completed_book_sections_are_saved_when_one_section_stays_unavailable(self):
        first_section = {
            "source_summary": "First section",
            "detected_title": "",
            "detected_author": "",
            "strategies": [],
        }

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"YOUTUBE_STRATEGY_DATA_DIR": directory},
        ), patch(
            "trading_intelligence_core.chunk_source_text",
            return_value=["chunk one", "chunk two"],
        ), patch(
            "trading_intelligence_core.source_fingerprint",
            return_value="resume-test",
        ):
            first = GeminiBookAnalyzer("key", "gemini-3.7-flash")
            with patch.object(
                first,
                "_analyze_chunk_with_adaptive_split",
                side_effect=[
                    first_section,
                    AppError("Provider request failed (503): high demand"),
                    AppError("Provider request failed (503): high demand"),
                ],
            ):
                partial = first.analyze("book", title="Book", author="Author")

            self.assertTrue(partial["analysis_incomplete"])
            self.assertEqual(partial["completed_sections"], 1)
            self.assertEqual(partial["failed_sections"][0]["section"], 2)

            second_section = {
                "source_summary": "Second section",
                "detected_title": "",
                "detected_author": "",
                "strategies": [],
            }
            second = GeminiBookAnalyzer("key", "gemini-3.7-flash")
            with patch.object(
                second,
                "_analyze_chunk_with_adaptive_split",
                return_value=second_section,
            ) as resumed_call:
                result = second.analyze("book", title="Book", author="Author")

            self.assertEqual(resumed_call.call_count, 1)
            self.assertIn("First section", result["summary"])
            self.assertIn("Second section", result["summary"])
            self.assertEqual(result["completed_sections"], 2)
            self.assertFalse(result["analysis_incomplete"])

    def test_read_timeout_switches_to_backup_model(self):
        analyzer = GeminiBookAnalyzer(
            "primary-key",
            "gemini-3.7-flash",
            fallback_model="gemini-3.6-flash",
        )
        progress_messages = []
        with patch("trading_intelligence_core.BOOK_TRANSIENT_RETRIES_PER_MODEL", 0), patch.object(
            analyzer,
            "_analyze_chunk",
            side_effect=[
                AppError(
                    "The provider could not be reached or took too long to respond: "
                    "The read operation timed out"
                ),
                {
                    "source_summary": "Recovered",
                    "detected_title": "",
                    "detected_author": "",
                    "strategies": [],
                },
            ],
        ):
            result = analyzer._analyze_chunk_resilient(
                "text",
                title="Book",
                author="Author",
                chunk_number=1,
                chunk_count=5,
                focus="",
                progress_callback=lambda i, total, message: progress_messages.append(message),
            )

        self.assertEqual(result["source_summary"], "Recovered")
        self.assertEqual(analyzer.model, "gemini-3.6-flash")
        self.assertTrue(any("backup model" in message.lower() for message in progress_messages))

    def test_analyzer_emits_checkpoint_after_each_completed_section(self):
        analyzer = GeminiBookAnalyzer("key")
        checkpoints = []
        first = {
            "source_summary": "One",
            "detected_title": "Book",
            "detected_author": "Author",
            "strategies": [],
        }
        second = {
            "source_summary": "Two",
            "detected_title": "Book",
            "detected_author": "Author",
            "strategies": [],
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"YOUTUBE_STRATEGY_DATA_DIR": directory},
        ), patch(
            "trading_intelligence_core.chunk_source_text",
            return_value=["chunk one", "chunk two"],
        ), patch.object(
            analyzer,
            "_analyze_chunk_with_adaptive_split",
            side_effect=[first, second],
        ):
            result = analyzer.analyze(
                "book",
                title="Book",
                author="Author",
                checkpoint_callback=lambda snapshot: checkpoints.append(snapshot),
            )

        self.assertEqual(result["completed_sections"], 2)
        self.assertGreaterEqual(len(checkpoints), 2)
        self.assertEqual(checkpoints[0]["completed_sections"], 1)
        self.assertTrue(checkpoints[0]["analysis_incomplete"])
        self.assertEqual(checkpoints[-1]["completed_sections"], 2)


    def test_source_id_stays_stable_when_title_is_discovered_later(self):
        analyzer = GeminiBookAnalyzer("key")
        checkpoints = []
        first = {
            "source_summary": "First",
            "detected_title": "",
            "detected_author": "",
            "strategies": [
                {
                    "name": "Breakout",
                    "category": "momentum",
                    "confidence": 90,
                    "entry_conditions": ["Break high"],
                    "exit_conditions": [],
                    "risk_rules": [],
                    "avoid_conditions": [],
                    "unresolved_rules": [],
                    "machine_rules": {"min_relative_volume": 2.0},
                    "evidence": [{"location": "p.1", "description": "rule", "source_excerpt": "short"}],
                }
            ],
        }
        second = {
            "source_summary": "Second",
            "detected_title": "Discovered Book",
            "detected_author": "Author",
            "strategies": [],
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"YOUTUBE_STRATEGY_DATA_DIR": directory},
        ), patch(
            "trading_intelligence_core.chunk_source_text",
            return_value=["chunk one", "chunk two"],
        ), patch.object(
            analyzer,
            "_analyze_chunk_with_adaptive_split",
            side_effect=[first, second],
        ):
            result = analyzer.analyze(
                "same book text",
                title="",
                author="",
                checkpoint_callback=lambda snapshot: checkpoints.append(snapshot),
            )

        self.assertEqual(result["title"], "Discovered Book")
        self.assertGreaterEqual(len(checkpoints), 2)
        self.assertEqual(checkpoints[0]["id"], result["id"])
        self.assertEqual(checkpoints[-1]["id"], result["id"])
        self.assertEqual(checkpoints[0]["strategies"][0]["source_id"], result["id"])


    def test_timeout_can_split_large_section_and_merge_results(self):
        analyzer = GeminiBookAnalyzer("key", "gemini-3.7-flash")
        large_chunk = ("First half paragraph.\n\n" * 400) + ("Second half paragraph.\n\n" * 400)
        left = {
            "source_summary": "Left recovered",
            "detected_title": "Book",
            "detected_author": "Author",
            "strategies": [],
        }
        right = {
            "source_summary": "Right recovered",
            "detected_title": "Book",
            "detected_author": "Author",
            "strategies": [],
        }
        with patch.object(
            analyzer,
            "_analyze_chunk_resilient",
            side_effect=[
                AppError("The read operation timed out"),
                left,
                right,
            ],
        ):
            result = analyzer._analyze_chunk_with_adaptive_split(
                large_chunk,
                title="Book",
                author="Author",
                chunk_number=1,
                chunk_count=1,
                focus="",
                progress_callback=None,
            )

        self.assertIn("Left recovered", result["source_summary"])
        self.assertIn("Right recovered", result["source_summary"])


class StrategyIntegrityTests(unittest.TestCase):
    def test_paper_execution_fidelity_separates_dynamic_backtest_support_from_live_support(self):
        fixed = {
            "id": "fixed",
            "direction": "long",
            "machine_rules": {"stop_loss_pct": 3.0, "reward_risk": 2.0},
        }
        dynamic = {
            "id": "dynamic",
            "direction": "long",
            "machine_rules": {
                "stop_loss_pct": 3.0,
                "reward_risk": 2.0,
                "exit_below_vwap": True,
            },
        }
        self.assertEqual(paper_execution_fidelity(fixed)["status"], "ready")
        blocked = paper_execution_fidelity(dynamic)
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("VWAP-loss exit", blocked["unsupported_management"])

    def test_legacy_validation_is_invalidated_when_defining_logic_was_never_modeled(self):
        strategy = {
            "id": "old-validated-tape",
            "name": "Old validated tape-confirmed breakout",
            "direction": "long",
            "validation_status": "validated",
            "optimization_status": "complete",
            "entry_conditions": ["Enter only when Level 2 and tape speed confirm the breakout."],
            "machine_rules": {
                "breakout_lookback_bars": 20,
                "stop_loss_pct": 3,
                "reward_risk": 2,
            },
            "validated_rules": {"breakout_lookback_bars": 20},
            "validated_backtest_settings": {"risk_per_trade_pct": 0.5},
            "validated_at": "2026-08-20T00:00:00Z",
            "evidence": [{"location": "video", "description": "entry confirmation", "source_excerpt": "short"}],
        }
        upgraded = upgrade_native_strategy_rules(strategy)
        self.assertEqual(upgraded["validation_status"], "unvalidated")
        self.assertEqual(upgraded["optimization_status"], "not_run")
        self.assertNotIn("validated_rules", upgraded)
        audit = upgraded.get("previous_validation_invalidated_by_integrity_audit") or {}
        self.assertIn("Level-2 / tape-reading confirmation", audit.get("missing_requirements") or [])

    def test_scale_out_strategy_is_blocked_until_partial_exits_are_modeled(self):
        strategy = {
            "id": "scaleout",
            "name": "Momentum breakout with scale out",
            "direction": "long",
            "entry_conditions": ["Buy the breakout above resistance."],
            "exit_conditions": ["Take partial profit at the first push and scale out into strength."],
            "machine_rules": {"breakout_lookback_bars": 20, "stop_loss_pct": 3},
            "evidence": [{"location": "p.1", "description": "setup", "source_excerpt": "short"}],
            "unresolved_rules": [],
        }
        report = strategy_integrity_report(strategy)
        readiness = research_readiness(strategy)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("Scale-out / partial-profit management", report["critical_missing_requirements"])
        self.assertEqual(readiness["label"], "partially_modeled")

    def test_low_float_and_tape_requirements_are_not_silently_ignored(self):
        strategy = {
            "id": "tape",
            "name": "Low-float tape breakout",
            "direction": "long",
            "stock_selection": ["Only low-float stocks."],
            "entry_conditions": ["Enter when Level 2 and tape speed confirm the breakout."],
            "machine_rules": {"breakout_lookback_bars": 20},
            "evidence": [{"location": "video", "description": "setup", "source_excerpt": "short"}],
            "unresolved_rules": ["Level 2 confirmation", "low float"],
        }
        report = strategy_integrity_report(strategy)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("Historical float filter", report["critical_missing_requirements"])
        self.assertIn("Level-2 / tape-reading confirmation", report["critical_missing_requirements"])

    def test_existing_explicit_exit_text_is_upgraded_without_reupload(self):
        strategy = {
            "id": "dynamic",
            "name": "Managed runner",
            "direction": "long",
            "exit_conditions": [
                "Use a 5% trailing stop.",
                "After the trade reaches 1R, move the stop to breakeven.",
                "Exit if price closes below VWAP.",
            ],
            "machine_rules": {"min_price": 1},
            "evidence": [{"location": "p.2", "description": "exit", "source_excerpt": "short"}],
        }
        upgraded = upgrade_native_strategy_rules(strategy)
        rules = upgraded["machine_rules"]
        self.assertEqual(rules["trailing_stop_pct"], 5.0)
        self.assertEqual(rules["move_stop_to_breakeven_at_r"], 1.0)
        self.assertTrue(rules["exit_below_vwap"])
        report = strategy_integrity_report(upgraded)
        self.assertNotIn("Trailing-stop exit", report["critical_missing_requirements"])
        self.assertNotIn("Move stop to breakeven", report["critical_missing_requirements"])
        self.assertNotIn("Exit when VWAP is lost", report["critical_missing_requirements"])

    def test_saved_explicit_multi_stage_exit_is_migrated_without_reupload(self):
        strategy = {
            "id": "staged",
            "name": "Staged runner",
            "direction": "long",
            "entry_conditions": ["Buy the breakout above resistance."],
            "exit_conditions": [
                "Scale out 25% at 1R and scale out 25% at 2R.",
                "After the first partial, move the stop to breakeven.",
                "Trail the remainder below VWAP.",
            ],
            "machine_rules": {"breakout_lookback_bars": 20, "stop_loss_pct": 3},
            "evidence": [{"location": "p.4", "description": "management", "source_excerpt": "short"}],
            "unresolved_rules": [],
        }
        upgraded = upgrade_native_strategy_rules(strategy)
        rules = upgraded["machine_rules"]
        self.assertEqual(
            rules["scale_out_stages"],
            [
                {"fraction_pct": 25.0, "at_r": 1.0},
                {"fraction_pct": 25.0, "at_r": 2.0},
            ],
        )
        self.assertTrue(rules["move_stop_to_breakeven_after_scale_out"])
        self.assertTrue(rules["trail_below_vwap"])
        report = strategy_integrity_report(upgraded)
        self.assertNotIn(
            "Scale-out / partial-profit management",
            report["critical_missing_requirements"],
        )
        self.assertNotIn("Move stop to breakeven", report["critical_missing_requirements"])
        self.assertNotIn("Trail remainder beneath VWAP", report["critical_missing_requirements"])

    def test_multi_stage_parser_does_not_cross_pair_different_partial_sizes(self):
        strategy = {
            "id": "staged-different-fractions",
            "name": "Different staged fractions",
            "direction": "long",
            "entry_conditions": ["Buy the breakout."],
            "exit_conditions": [
                "Scale out 25% at 1R and scale out 50% at 2R.",
            ],
            "machine_rules": {"breakout_lookback_bars": 20, "stop_loss_pct": 3},
            "evidence": [{"location": "p.4", "description": "management", "source_excerpt": "short"}],
            "unresolved_rules": [],
        }
        upgraded = upgrade_native_strategy_rules(strategy)
        self.assertEqual(
            upgraded["machine_rules"]["scale_out_stages"],
            [
                {"fraction_pct": 25.0, "at_r": 1.0},
                {"fraction_pct": 50.0, "at_r": 2.0},
            ],
        )

    def test_staged_exit_policy_is_researchable_but_blocked_for_current_paper_auto(self):
        strategy = {
            "id": "staged-paper",
            "name": "Staged paper mismatch",
            "direction": "long",
            "entry_conditions": ["Buy the breakout."],
            "exit_conditions": [
                "Scale out 25% at 1R and scale out 25% at 2R.",
                "Trail the remainder below VWAP.",
            ],
            "machine_rules": {
                "breakout_lookback_bars": 20,
                "stop_loss_pct": 3,
                "reward_risk": None,
                "scale_out_stages": [
                    {"fraction_pct": 25, "at_r": 1},
                    {"fraction_pct": 25, "at_r": 2},
                ],
                "trail_below_vwap": True,
            },
            "evidence": [{"location": "p.5", "description": "setup", "source_excerpt": "short"}],
            "unresolved_rules": [],
        }
        report = strategy_integrity_report(strategy)
        self.assertNotIn(
            "Scale-out / partial-profit management",
            report["critical_missing_requirements"],
        )
        blocked = paper_execution_fidelity(strategy)
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn(
            "Multi-stage partial-profit management",
            blocked["unsupported_management"],
        )
        self.assertIn(
            "VWAP-trailing stop management",
            blocked["unsupported_management"],
        )



if __name__ == "__main__":
    unittest.main()


class ReadinessFailClosedRegressionTests(unittest.TestCase):
    def test_supported_rule_plus_historical_spread_rule_is_partially_modeled(self):
        result = research_readiness(
            {
                "source_type": "autonomous_web_research",
                "machine_rules": {
                    "min_relative_volume": 2.0,
                    "max_spread_pct": 0.15,
                },
                "evidence": [
                    {
                        "location": "research",
                        "description": "RVOL and spread filter",
                        "source_excerpt": "short",
                    }
                ],
                "unresolved_rules": [],
            }
        )
        self.assertEqual(result["label"], "partially_modeled")
        self.assertIn("max_spread_pct", result["note"])

    def test_false_structural_boolean_does_not_make_strategy_testable(self):
        result = research_readiness(
            {
                "source_type": "autonomous_web_research",
                "machine_rules": {"previous_day_high_breakout": False},
                "evidence": [
                    {
                        "location": "research",
                        "description": "No breakout requirement",
                        "source_excerpt": "short",
                    }
                ],
                "unresolved_rules": [],
            }
        )
        self.assertEqual(result["label"], "needs_translation")
        self.assertEqual(result["entry_rule_count"], 0)


def test_stop_placement_rule_alone_does_not_make_strategy_entry_testable():
    result = research_readiness(
        {
            "source_type": "autonomous_web_research",
            "machine_rules": {"stop_below_fast_ema": True},
            "evidence": [
                {
                    "location": "research",
                    "description": "Stop below EMA",
                    "source_excerpt": "short",
                }
            ],
            "unresolved_rules": [],
        }
    )
    assert result["label"] == "needs_translation"
    assert result["entry_rule_count"] == 0
