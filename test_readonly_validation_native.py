"""Native read-only evidence, context and absence of execution controls."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import time

import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QPushButton, QLineEdit
from desktop.trading_intelligence.saved_validation_page import SavedValidationPage
from desktop.trading_intelligence.saved_validation_controller import SavedValidationController
from desktop.trading_intelligence.analysis_page import AnalysisPage
from desktop.trading_intelligence.strategy_lab_page import StrategyLabPage
from desktop.trading_intelligence.parity_window import MainWindow
from hybrid_runtime.saved_validation_reader import read_saved_validation
from test_readonly_validation_results import setup, evidence, SID, JID


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_saved_native_evidence_and_no_execution_controls(app, setup):
    result=read_saved_validation(setup.worker,setup.request); before=deepcopy(result)
    page=SavedValidationPage(result)
    assert JID in page.identity.text() and SID in page.identity.text()
    assert "FAILED" in page.verdict.text() and "NO RELIABLE EDGE FOUND" in page.verdict.text()
    assert "13/100" in page.strength.text() and "WEAK" in page.strength.text()
    assert page.checks.item(1,1).text()=="BRITTLE (complete)"
    assert "fold count: 3" in page.checks.item(0,2).text()
    assert "tested: 12" in page.checks.item(1,2).text()
    assert [page.tests.item(i,2).text() for i in range(3)]==["0","0","0"]
    assert page.confidence.item(1,1).text()=="WEAK"
    assert page.warnings.rowCount()>0
    assert not page.findChildren(QPushButton)
    assert not hasattr(page,"backtest_requested") and not hasattr(page,"run_requested")
    assert result==before


def test_native_timeout_has_no_strategy_score(app, setup):
    from hybrid_runtime.contracts import JobStatus
    setup.job.status=JobStatus.FAILED;setup.job.error={"kind":"execution_timeout","message":"Execution timed out"};setup.job.result=None
    page=SavedValidationPage(read_saved_validation(setup.worker,setup.request))
    assert "failed (not running)" in page.verdict.text()
    assert "No strategy-validation verdict" in page.verdict.text()
    assert not hasattr(page,"strength") and not hasattr(page,"tabs")


@pytest.mark.parametrize("value", [0,None])
def test_zero_and_missing_strength_are_distinct(app, setup,value):
    setup.job.result["strength"]["score"]=value
    page=SavedValidationPage(read_saved_validation(setup.worker,setup.request))
    assert ("0/100" if value==0 else "Not recorded") in page.strength.text()


def context():
    return {"symbol":"SPY","best_strategy_id":SID,"best_strategy_name":"Pivot-Confirmed Swing High AVWAP Dynamic Trend Filter",
            "metrics":{"trade_timestamp":"2026-09-04T19:59:59Z"},
            "signal":{"status":"MATCH","score":100,"checks":[{"label":"Anchored VWAP available","status":"pass","actual":770.1,"required":"swing_high"}]}}


@pytest.mark.parametrize("loaded_first", [True,False])
def test_exact_strategy_survives_options_loading(app, loaded_first):
    page=StrategyLabPage()
    options={"strategies":[{"id":"other","name":"Other"},{"id":SID,"name":"Exact"}],"faithful_count":2}
    if loaded_first:page.set_options(options)
    page.select_strategy_id(SID)
    if not loaded_first:page.set_options(options)
    assert page.options_loaded and page.strategy.currentData()==SID
    assert not page.compare_all.isChecked()
    page.select_strategy_id("missing")
    assert not page.run.isEnabled() and page.strategy.currentIndex()==-1


@pytest.mark.parametrize("active", [True,False])
def test_context_handoff_without_validation_submission(app,active):
    analysis=AnalysisPage();finder=SimpleNamespace(symbol=QLineEdit("OLD"));lab=StrategyLabPage()
    lab.set_options({"strategies":[{"id":SID,"name":"Exact"}],"faithful_count":1})
    w=SimpleNamespace(analysis=analysis,finder=finder,strategy_lab=lab,
        market_discovery=SimpleNamespace(_results=[context()]), finder_job_id="busy" if active else "",
        strategy_lab_job_id="busy" if active else "",stack=SimpleNamespace(indexOf=lambda _:3),show_page=Mock())
    submitted=[];lab.run_requested.connect(submitted.append)
    MainWindow.analyze_discovery_symbol(w,"SPY")
    assert not submitted and SID in analysis.signal_summary.text()
    assert "slope" not in analysis.signal_summary.text().lower()
    assert finder.symbol.text()==("OLD" if active else "SPY")
    assert lab.ticker.text()==("SDOT" if active else "SPY")
    analysis.symbol.setText("QQQ")
    assert analysis.signal_card.isHidden()
    analysis.symbol.setText("SPY")
    assert SID in analysis.signal_summary.text()


def test_normal_validation_defaults_unchanged(app):
    page=StrategyLabPage();page.set_options({"strategies":[{"id":SID,"name":"Exact"}],"faithful_count":1})
    requests=[];page.run_requested.connect(requests.append);page._emit_run();p=requests[0]
    assert (p["training_fraction"],p["validation_fraction"])==(.6,.2)
    assert (p["minimum_training_trades"],p["minimum_validation_trades"])==(5,2)
    assert p["run_walk_forward"] and (p["wf_folds"],p["wf_history_sessions"],p["wf_test_sessions"])==(3,8,2)
    assert p["search_depth"]==36 and p["history_days"]==30


def test_view_controller_calls_only_read_endpoint(app, setup):
    from PySide6.QtWidgets import QWidget, QLabel
    window=QWidget(); window.top_status=QLabel()
    response=read_saved_validation(setup.worker,setup.request)
    calls=[]
    def request(method,path,body,**kw):
        calls.append((method,path,body));return deepcopy(response)
    window.runtime=SimpleNamespace(request_json=request)
    controller=SavedValidationController(window,SimpleNamespace(panels=[]));controller.timer.stop()
    row={**setup.request,"kind":"Strategy Lab","status":"complete"}
    controller.open(row)
    deadline=time.monotonic()+3
    while controller.results.empty() and time.monotonic()<deadline:time.sleep(.01)
    controller.tick()
    assert not controller.busy and JID in controller.page.identity.text()
    assert [(m,p) for m,p,_ in calls]==[("POST","/v1/saved-validations/result")]
    assert [b.text() for b in controller.dialog.findChildren(QPushButton)]==["Close"]
    controller.dialog.close();window.close()
