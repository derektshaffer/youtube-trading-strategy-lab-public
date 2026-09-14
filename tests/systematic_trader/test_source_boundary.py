"""Source rejection must happen before collector effects, even in test checkouts."""
from pathlib import Path
from unittest.mock import Mock
import pytest
from systematic_trader import collection_runtime, service


def test_foreign_source_is_rejected_before_store_or_cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "CANONICAL_ROOT", tmp_path / "foreign-source")
    store = Mock(side_effect=AssertionError("source guard precedes storage"))
    cycle = Mock(side_effect=AssertionError("source guard precedes providers"))
    monkeypatch.setattr(collection_runtime, "CollectionService", store)
    with pytest.raises(service.CaptureFailure, match="noncanonical_source_refused"):
        collection_runtime.serve(tmp_path / "must-not-exist", once=True, cycle=cycle)
    store.assert_not_called()
    cycle.assert_not_called()
    assert not (tmp_path / "must-not-exist").exists()


def test_fixture_source_guard_remains_active_after_root_changes(fixture_canonical_source, tmp_path, monkeypatch):
    assert fixture_canonical_source == Path(service.__file__).resolve().parents[1]
    service.assert_canonical_source()
    monkeypatch.setattr(service, "CANONICAL_ROOT", tmp_path / "different-source")
    with pytest.raises(service.CaptureFailure, match="noncanonical_source_refused"):
        service.assert_canonical_source()
