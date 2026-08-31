import ast
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent


class ApplicationEntrypointStructureTests(unittest.TestCase):
    def test_streamlit_entrypoints_do_not_rewrite_or_exec_source(self):
        entrypoints = [
            ROOT / "youtube_strategy_app.py",
            ROOT / "pages" / "Full_Trading_Lab.py",
            ROOT / "pages" / "Live_Strategy_Runner.py",
            ROOT / "pages" / "Machine_Learning_Lab.py",
            ROOT / "pages" / "Trading_Intelligence_Lab.py",
        ]
        for path in entrypoints:
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source)
                calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
                self.assertFalse(any(isinstance(node.func, ast.Name) and node.func.id == "exec" for node in calls))
                self.assertNotIn("read_text", source)
                self.assertNotIn("source.replace", source)

    def test_side_effect_core_entrypoints_render_on_every_streamlit_rerun(self):
        entrypoints = {
            ROOT / "youtube_strategy_app.py": "trading_intelligence_app",
            ROOT / "pages" / "Full_Trading_Lab.py": "youtube_strategy_app_core",
            ROOT / "pages" / "Machine_Learning_Lab.py": "machine_learning_lab_core",
            ROOT / "pages" / "Trading_Intelligence_Lab.py": "trading_intelligence_app",
        }
        for path, module_name in entrypoints.items():
            with self.subTest(path=path.name), patch.object(runpy, "run_module") as run_module:
                runpy.run_path(str(path), run_name="__main__")
                runpy.run_path(str(path), run_name="__main__")
                self.assertEqual(run_module.call_count, 2)
                run_module.assert_called_with(module_name, run_name="__main__")

    def test_trading_intelligence_reuses_prepared_library_between_reruns(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        engine = (ROOT / "youtube_strategy_engine.py").read_text(encoding="utf-8")
        self.assertIn("LIBRARY_CLOUD_REFRESH_SECONDS = 60.0", source)
        self.assertIn("_til_library_render_cache", source)
        self.assertIn("_local_library_mtime_ns", source)
        self.assertIn("library_revision()", source)
        self.assertIn("def library_revision", engine)

    def test_finder_auto_refresh_only_runs_while_cloud_work_is_active(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        self.assertIn('@st.fragment(run_every="60s")', source)
        self.assertIn("_initial_active_cloud_finders", source)
        self.assertIn("load_cloud_status_library()", source)
        self.assertIn("automatic refresh stops so the page stays still", source)

    def test_workspace_is_remembered_across_streamlit_sessions(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        self.assertIn('st.query_params.get("workspace")', source)
        self.assertIn('st.query_params["workspace"] = module', source)

    def test_market_discovery_scans_all_strategies_automatically(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        market_source = (ROOT / "trading_market_discovery.py").read_text(encoding="utf-8")
        self.assertIn("scan_market_strategies(", source)
        self.assertIn("def scan_market_strategies(", market_source)
        self.assertIn("Automatic strategy coverage", source)
        self.assertIn("Find the best opportunities now", source)
        self.assertNotIn('"Strategy to scan for"', source)

    def test_home_and_sidebar_use_goal_based_simple_navigation(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        for marker in (
            "WHAT DO YOU WANT TO DO?",
            "Find & test a strategy",
            "Steps 2–5 appear automatically after Step 1 finds a strategy.",
            "Standalone Stock Analyzer",
            "Find stocks worth watching",
            "Add research material",
            "AI discoveries & research",
            "Advanced / Research Details",
            "What do you want to do?",
            "Find the best strategy for a stock",
            "YOUR STRATEGY WORKFLOW",
            "Compare → Validate → Current Setup",
            "③ Validate this strategy →",
            "④ Check current setup",
            "⑤ Open paper testing →",
            "on_click=queue_workspace_navigation",
            "prime_action_feedback",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_guided_strategy_workflow_is_goal_ordered_and_actionable(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        for marker in (
            '(1, "Search"',
            '(2, "Compare"',
            '(3, "Validate"',
            '(4, "Current Setup"',
            '(5, "Paper Test"',
            '"③ Validate this strategy →"',
            '"④ Check current setup"',
            '"⑤ Open paper testing →"',
            "queue_strategy_validation_from_analyzer",
            "queue_paper_test_from_analyzer",
            "til_strategy_lab_candidate_payload",
            "til_guided_validation_mode",
            "Walk-forward testing is required and already turned on.",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_stock_analyzer_allows_only_ranked_ticker_tested_strategy_switching(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        for marker in (
            '"Strategy tested for this stock"',
            'key="til_analyzer_strategy_id"',
            "tested_strategy_rankings",
            "guided_finder_run_id",
            "Showing only strategies Step 1 tested for",
            "Validation return is the strategy's",
            "finder-tested-",
            'analysis["_selected_strategy_id"]',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)
        self.assertNotIn('"Strategy to check"', source)

    def test_strategy_integrity_audit_is_available_in_advanced_ui(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        core = (ROOT / "trading_intelligence_core.py").read_text(encoding="utf-8")
        finder = (ROOT / "stock_strategy_finder.py").read_text(encoding="utf-8")
        self.assertIn("Strategy Integrity Audit", source)
        self.assertIn("Rule-by-rule backtester check", source)
        self.assertIn("Strategies fully represented", source)
        self.assertIn("Strategies partly represented", source)
        self.assertIn("Strategies with critical gaps", source)
        self.assertIn("Avg. strategy rules reproduced", source)
        self.assertIn("No rules detected", source)
        self.assertIn("N/A — no rules detected", source)
        self.assertIn("excluding strategies where no rules were detected", source)
        self.assertIn("This is not a profitability, accuracy, confidence, or win-rate score.", source)
        self.assertIn("Missing stock-selection rules", source)
        self.assertIn('"Rules modeled"', source)
        self.assertNotIn('"Rules represented %"', source)
        self.assertNotIn('"Rules represented":', source)
        self.assertNotIn('"Rules detected"', source)
        self.assertNotIn('"Universe gaps"', source)
        self.assertNotIn('"Average fidelity"', source)
        self.assertNotIn('"Important gaps"', source)
        self.assertIn("strategy_integrity_report", source)
        self.assertIn("def strategy_integrity_report", core)
        self.assertIn("strategy fidelity audit failed", finder)

    def test_low_fidelity_strategies_are_excluded_from_user_facing_research_paths(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        self.assertIn("integrity_safe_strategies", source)
        self.assertIn("integrity_blocked_count", source)
        self.assertIn("excluded from backtesting", source)
        self.assertIn("excluded from cross-stock research", source)
        self.assertIn("excluded because", source)
        self.assertIn("Review integrity gaps", source)
        self.assertIn(
            "legacy_changed or native_strategy_changed or sources_changed or canonical_changed",
            source,
        )

    def test_legacy_finder_results_are_visibly_marked_pre_integrity(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        finder = (ROOT / "stock_strategy_finder.py").read_text(encoding="utf-8")
        persistence = (ROOT / "finder_report_persistence.py").read_text(encoding="utf-8")
        self.assertIn("strategy_fidelity_engine_version", source)
        self.assertIn("Legacy research result", source)
        self.assertIn("STRATEGY_FIDELITY_ENGINE_VERSION = 1", finder)
        self.assertIn("strategy_fidelity_engine_version", persistence)

    def test_retrospective_teacher_workspace_enforces_causal_boundary(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        teacher = (ROOT / "retrospective_teacher.py").read_text(encoding="utf-8")
        self.assertIn("Retrospective Teacher → Causal Learner", source)
        self.assertIn("Build retrospective teaching examples", source)
        self.assertIn("future_data_allowed_for", teacher)
        self.assertIn("future_data_forbidden_for", teacher)
        self.assertIn("validate_no_lookahead", teacher)
        self.assertIn("known_at", source)

    def test_open_source_repositories_are_reference_evidence_not_profitability_evidence(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        catalog = (ROOT / "open_source_reference_catalog.py").read_text(encoding="utf-8")
        orchestrator = (ROOT / "trading_research_orchestrator.py").read_text(encoding="utf-8")
        self.assertIn("Open-source implementation references", source)
        self.assertIn("implementation/reference evidence", source)
        self.assertIn("OPEN_SOURCE_REFERENCE_CATALOG", catalog)
        self.assertIn("not profitability evidence", orchestrator)
        self.assertIn("Retrospective/smoothed algorithms may be useful teachers", orchestrator)

    def test_retrospective_workspace_surfaces_volume_avwap_and_indicator_layers(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        teacher = (ROOT / "retrospective_teacher.py").read_text(encoding="utf-8")
        volume = (ROOT / "causal_volume_profile.py").read_text(encoding="utf-8")
        avwap = (ROOT / "anchored_vwap_engine.py").read_text(encoding="utf-8")
        crosscheck = (ROOT / "indicator_cross_validation.py").read_text(encoding="utf-8")
        self.assertIn("Learning layers used", source)
        self.assertIn("Indicator consistency check", source)
        self.assertIn("label_volume_exhaustion_outcomes", teacher)
        self.assertIn("label_multi_avwap_pinch_outcomes", teacher)
        self.assertIn("apply_causal_volume_profile_features", volume)
        self.assertIn("apply_multi_anchor_avwap_teacher_features", avwap)
        self.assertIn("cross_validate_indicators", crosscheck)

    def test_retrospective_observations_feed_autonomous_research(self):
        source = (ROOT / "trading_research_orchestrator.py").read_text(encoding="utf-8")
        self.assertIn("retrospective_teacher_challenge_jobs", source)
        self.assertIn("descriptive observations", source)
        self.assertIn('origin": "retrospective_teacher"', source)

    def test_continuous_research_button_is_clearly_manual(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        self.assertIn("Run today's research cycle now", source)
        self.assertIn("Runs automatically once per UTC day", source)
        self.assertIn("hourly cloud worker", source)

    def test_trading_intelligence_refreshes_storage_before_health_snapshot(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        self.assertLess(
            source.index("library = load_library()"),
            source.index("persistence_snapshot = intelligence_store().persistence_status"),
        )

    def test_stock_finder_surfaces_recent_completed_cloud_runs_independent_of_depth(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        self.assertIn("Recent completed cloud research", source)
        self.assertIn("stock_strategy_finder_runs", source)
        self.assertIn("latest_symbol_finder_result", source)
        self.assertIn("Open {completed_symbol} {completed_profile} result", source)

    def test_cloud_finder_queue_failures_stay_inside_the_page(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        self.assertIn('queue_error = ""', source)
        self.assertIn("Cloud Finder could not confirm a durable queue update", source)
        self.assertIn("No automatic retry was started", source)

    def test_stock_specific_finder_children_are_available_only_to_downstream_workflows(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        self.assertIn("stock_specific_strategies", source)
        self.assertIn(
            "downstream_strategies = [*stock_specific_strategies, *managed_strategies]",
            source,
        )
        self.assertIn(
            "finder_family_strategies = stock_finder_strategy_families(strategies)",
            source,
        )
        self.assertIn("finder_family_strategies,", source)

    def test_live_runner_consumes_exact_finder_strategy_handoff_before_widget(self):
        source = (ROOT / "live_strategy_runner_page.py").read_text(encoding="utf-8")
        request_index = source.index(
            'st.session_state.pop("til_selected_strategy_id", "")'
        )
        widget_index = source.index(
            'st.selectbox("Strategy to run", list(options), key="runner_strategy_v2")'
        )
        self.assertLess(request_index, widget_index)
        self.assertIn("build_intelligence_store().load_latest()", source)

    def test_manual_lab_cannot_weaken_the_finder_validation_protocol(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        self.assertIn("stress_cost_multiplier=1.75", source)
        self.assertIn("automatic_slippage=True", source)
        self.assertIn('number_input("Walk-forward folds", 2, 6, 3, 1)', source)
        self.assertIn(
            '"Validation drawdown ceiling (%)",\n                1.0,\n                20.0,',
            source,
        )

    def test_streamlit_version_supports_locked_sidebar(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("streamlit>=1.59,<2", requirements)

    def test_trading_intelligence_sidebar_is_locked_open_on_desktop(self):
        app_source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        theme_source = (ROOT / "trading_glass_theme.py").read_text(encoding="utf-8")
        self.assertIn('initial_sidebar_state="locked"', app_source)
        self.assertIn(
            '[data-testid="stHeader"] {\n            height: 0 !important;',
            theme_source,
        )
        self.assertNotIn('data-testid="stSidebarCollapsedControl"', theme_source)
        self.assertIn(
            '[data-testid="stSidebar"][aria-expanded="false"]',
            theme_source,
        )
        self.assertIn("transform: none !important;", theme_source)

    def test_trading_intelligence_recovers_known_cloud_divergence_without_blocking_ui(self):
        source = (ROOT / "trading_intelligence_app.py").read_text(encoding="utf-8")
        self.assertIn("store.restore_cloud_backup()", source)
        self.assertIn("_til_cloud_conflict_recovered", source)
        self.assertIn(
            "Both the local Trading Lab library and the private GitHub library changed",
            source,
        )

    def test_full_lab_features_are_integrated_into_the_core_module(self):
        source = (ROOT / "youtube_strategy_app_core.py").read_text(encoding="utf-8")
        for marker in (
            "Backtest run history — reproduce an earlier test",
            "Fixed dates — reproducible",
            "backtest_run_context",
            "Actual settings this backtest will use",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)
        compile(source, str(ROOT / "youtube_strategy_app_core.py"), "exec")

    def test_deprecated_streamlit_width_argument_is_not_used(self):
        paths = [
            path
            for path in [*ROOT.glob("*.py"), *(ROOT / "pages").glob("*.py")]
            if not path.name.startswith("test_")
        ]
        for path in paths:
            with self.subTest(path=path.name):
                self.assertNotIn("use_container_width=", path.read_text(encoding="utf-8"))

    def test_full_lab_does_not_invoke_deprecated_inline_component(self):
        source = (ROOT / "youtube_strategy_app_core.py").read_text(encoding="utf-8")
        self.assertNotIn("components.html", source)
        self.assertNotIn("install_technical_tooltips", source)

    def test_every_static_page_navigation_target_exists(self):
        source_paths = [*ROOT.glob("*.py"), *(ROOT / "pages").glob("*.py")]
        targets = []
        for source_path in source_paths:
            if source_path.name.startswith("test_"):
                continue
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr != "switch_page" or not node.args:
                    continue
                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    targets.append((source_path, node.args[0].value))
        self.assertTrue(targets)
        for source_path, target in targets:
            with self.subTest(source=source_path.name, target=target):
                self.assertTrue((ROOT / target).is_file())

    def test_machine_lab_back_navigation_precedes_credential_stop(self):
        source = (ROOT / "machine_learning_lab_core.py").read_text(encoding="utf-8")
        self.assertLess(source.index("ml_back_dashboard"), source.index("st.stop()"))


if __name__ == "__main__":
    unittest.main()