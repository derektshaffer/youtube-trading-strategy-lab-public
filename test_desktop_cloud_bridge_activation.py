from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_sidecar_starts_local_and_cloud_workers_and_exposes_links():
    source = (ROOT / "hybrid_runtime" / "server.py").read_text(encoding="utf-8")
    ast.parse(source, filename="hybrid_runtime/server.py")

    assert "from .cloud_bridge import CloudBridgeWorker" in source
    assert "from .cloud_link_store import CloudLinkStore" in source
    assert "CloudLinkStore(data_dir / \"cloud-links.sqlite3\")" in source
    assert "CloudBridgeWorker(" in source
    assert 'name="trading-intelligence-local-worker"' in source
    assert 'name="trading-intelligence-cloud-bridge"' in source
    assert "cloud_link_lookup=cloud_links.get" in source
    assert "for thread in threads:" in source
    assert "thread.start()" in source
    assert "stop_event.set()" in source
    assert "thread.join(timeout=2.0)" in source


def test_cloud_bridge_job_types_expand_only_through_explicit_contracts():
    source = (ROOT / "hybrid_runtime" / "cloud_bridge.py").read_text(encoding="utf-8")
    supported_line = source.split("SUPPORTED_CLOUD_JOB_TYPES", 1)[1].split("DEFAULT_LIBRARY_PATH", 1)[0]

    assert '"strategy.profit_first_validation"' in supported_line
    assert '"strategy.stock_finder"' in supported_line
    # Guard against an accidental catch-all that would silently send unknown
    # local jobs through the cloud bridge without a publication/result contract.
    assert "research.autonomous" not in supported_line
    assert "ml.train" not in supported_line
