"""Cloned cloud files must decode before any research or audit is attempted."""
import json
import sys

import pytest

from cloud_library_storage import CloudStorageError, encode_library


def document():
    return {
        'version': 2, 'strategies': [{'id': 'TEST_ONLY', 'validation_status': 'research_only'}],
        'research_queue': [{'id': 'TEST_ONLY-q', 'status': 'queued'}],
        'padding': 'x' * 1_100_000,
    }


def test_audit_does_not_misreport_compressed_library_as_empty(tmp_path):
    from research_library_audit import summarize
    raw = json.dumps(document()).encode()
    plain, compressed = tmp_path / 'plain.json', tmp_path / 'compressed.json'
    plain.write_bytes(raw)
    compressed.write_bytes(encode_library(raw, compress=True))
    before, after = summarize(plain), summarize(compressed)
    assert before['source_size_bytes'] == plain.stat().st_size
    assert after['source_size_bytes'] == compressed.stat().st_size
    assert after['source_size_bytes'] < before['source_size_bytes']
    # Stored size changes intentionally; research content and conclusions do not.
    for value in (before, after):
        value.pop('generated_at', None)
        value.pop('source_size_bytes')
    assert before == after


def test_targeted_reader_reaches_exact_strategy_before_any_research(tmp_path, monkeypatch):
    import profit_first_targeted_audit as audit
    path = tmp_path / 'compressed.json'
    expected = document()
    path.write_bytes(encode_library(json.dumps(expected).encode(), compress=True))
    monkeypatch.setattr(sys, 'argv', ['audit', str(path), str(tmp_path / 'out.json'),
                                     '--strategy-id', 'TEST_ONLY'])

    class ReachedFrozenStrategy(Exception):
        pass

    def stop_before_research(strategy):
        assert strategy == expected['strategies'][0]
        raise ReachedFrozenStrategy

    monkeypatch.setattr(audit, 'research_readiness', stop_before_research)
    with pytest.raises(ReachedFrozenStrategy):
        audit.main()
    assert not (tmp_path / 'out.json').exists()


@pytest.mark.parametrize('reader', ['audit', 'targeted'])
def test_direct_readers_reject_corruption_before_processing(tmp_path, monkeypatch, reader):
    import research_library_audit as audit
    import profit_first_targeted_audit as targeted
    stored = json.loads(encode_library(json.dumps(document()).encode(), compress=True))
    stored['_trading_lab_storage']['sha256'] = '0' * 64
    path = tmp_path / 'corrupt.json'
    path.write_text(json.dumps(stored))
    monkeypatch.setattr(sys, 'argv', ['audit', str(path), str(tmp_path / 'out.json')])
    with pytest.raises(CloudStorageError):
        audit.summarize(path) if reader == 'audit' else targeted.main()
    assert not (tmp_path / 'out.json').exists()
