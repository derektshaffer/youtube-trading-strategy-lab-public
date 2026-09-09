import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ai_review.__main__ import fixture_demo
from ai_review.storage import read_summary


def test_separate_process_replay_matches_exact_audit(tmp_path):
    directory = tmp_path / 'demo'
    expected = fixture_demo(directory)
    output = subprocess.check_output([sys.executable, '-m', 'ai_review', 'replay', str(directory)], text=True)
    assert json.loads(output) == expected
    assert expected['integrity'] == 'ok' and expected['execution_authority'] == 'none'
    states = {x['disposition'] for x in expected['artifacts'].values()}
    assert states == {'INDEPENDENT_REVIEW_PENDING', 'CLEARED_FOR_NEXT_VALIDATION_STAGE', 'DETERMINISTIC_VALIDATION_FAILED'}


def test_read_summary_missing_database_does_not_create_it(tmp_path):
    path = tmp_path / 'missing.sqlite3'
    assert read_summary(path) == [] and not path.exists()


def test_compact_expander_and_plain_text_ui(tmp_path):
    pytest.importorskip('PySide6')
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from desktop.trading_intelligence.research_ml_page import ResearchMLPage
    app = QApplication.instance() or QApplication([])
    directory = tmp_path / 'demo'
    fixture_demo(directory)
    rows = read_summary(directory / 'hybrid.sqlite3')
    page = ResearchMLPage()
    try:
        assert not page.independent_review.toggle.isChecked()
        page.render_summary({'independent_reviews': rows})
        section = page.independent_review
        assert section.selector.count() == 3
        section.set_expanded(True)
        assert section.detail.textFormat() == Qt.TextFormat.PlainText
        for index in range(3):
            section.selector.setCurrentIndex(index)
            content = section.detail.text()
            assert '\n\n' in content and '\\n' not in content
            for label in ('Primary AI result:', 'Independent review:', 'Agreement status:',
                          'Reviewer concerns:', 'Deterministic validation:', 'Final disposition:'):
                assert label in content
        page.set_error('Test unavailable')
        assert section.records == []
    finally:
        page.close()


def test_summary_handler_loads_review_from_same_local_store(tmp_path, monkeypatch):
    import hybrid_runtime.engine_adapter as engine
    import hybrid_runtime.library_source as source
    from types import SimpleNamespace
    directory = tmp_path / 'demo'; fixture_demo(directory)
    monkeypatch.setenv('TRADING_INTELLIGENCE_DESKTOP_DATA_DIR', str(directory))
    monkeypatch.setattr(source, 'load_library_for_job', lambda *a, **k: SimpleNamespace(library={}, metadata={}))
    result = engine.research_ml_summary_handler({}, lambda *a: None, lambda: False)
    assert len(result['independent_reviews']) == 3
