from pathlib import Path


def _pattern_validation_block() -> str:
    source = Path("trading_intelligence_app.py").read_text(encoding="utf-8")
    start = source.index('elif module == "Pattern Validation":')
    end = source.index('elif module == "Catalyst Intelligence":', start)
    return source[start:end]


def test_predictive_ml_workspace_is_research_only_and_walk_forward():
    block = _pattern_validation_block()
    assert "### Predictive ML Research" in block
    assert "build_cross_stock_training_dataset" in block
    assert "walk_forward_logistic_baseline" in block
    assert 'embargo_sessions=1' in block
    assert 'test_sessions_per_fold=2' in block
    assert "does not change scanner rankings" in block
    assert "No model from this panel is used live." in block


def test_predictive_ml_workspace_is_bounded_for_interactive_use():
    block = _pattern_validation_block()
    assert 'options=[5, 15, 30]' in block
    assert 'max_pages=120' in block
    assert '[:5]' in block
    assert '"Trading days"' in block
    assert '"ML history (days)"' not in block
    assert "session_limit=ml_days" in block
    assert "ml_calendar_lookback_days" in block
    assert "weekends and market holidays do not count" in block


def test_predictive_ml_workspace_respects_alpaca_sip_delay():
    block = _pattern_validation_block()
    assert 'historical_feed' in block
    assert 'timedelta(minutes=16)' in block
    assert 'current Alpaca entitlement' in block
    assert 'build_cross_stock_training_dataset(\n                ml_market,' in block


def test_predictive_ml_workspace_has_broader_five_stock_benchmark_preset():
    block = _pattern_validation_block()
    assert "Load broader 5-stock benchmark" in block
    assert 'broader_symbols = "SDOT RR KULR FCEL ACHR"' in block
    assert 'st.session_state["til_ml_history_days"] = 30' in block
    assert 'st.session_state["til_ml_target_horizon"] = 15' in block
    assert 'st.session_state["til_ml_target_mode_choice"] = "Trade-quality move"' in block
    assert 'st.session_state["til_ml_profit_target_pct"] = 1.0' in block
    assert 'st.session_state["til_ml_stop_loss_pct"] = 0.75' in block

def test_predictive_ml_workspace_separates_hours_and_runs_symbol_holdout():
    block = _pattern_validation_block()
    assert '"Regular session"' in block
    assert '"Premarket"' in block
    assert '"After-hours"' in block
    assert "session_mode=ml_session_mode" in block
    assert "leave_one_symbol_out_walk_forward_logistic_baseline" in block
    assert "Held-out-stock generalization" in block
    assert "held-out ticker was excluded" not in block.lower()



def test_predictive_ml_results_render_without_forced_rerun():
    block = _pattern_validation_block()
    result_store = block.index('st.session_state["til_predictive_ml_result"] = {')
    result_reader = block.index('stored_ml_result = st.session_state.get("til_predictive_ml_result")')
    between = block[result_store:result_reader]
    assert "st.rerun()" not in between
    assert 'if key != "predictions"' in between
    assert "Predictive ML results are ready below." in between
