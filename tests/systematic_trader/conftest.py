"""Explicit trust fixture for isolated Trading Lab collector unit tests."""
from pathlib import Path
import pytest


@pytest.fixture
def fixture_canonical_source(monkeypatch):
    from systematic_trader import service
    root = Path(service.__file__).resolve().parents[1]
    monkeypatch.setattr(service, "CANONICAL_ROOT", root)
    service.assert_canonical_source()
    return root
