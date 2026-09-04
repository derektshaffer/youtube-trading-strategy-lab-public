"""No provider calls or live artifacts: exercise the finalization credential boundary."""
from copy import deepcopy
from pathlib import Path

import pytest
import distributed_stock_finder as finder
import test_distributed_stock_finder as fixtures


def test_aggregate_inherits_same_alpaca_secret_and_feed_settings_as_prepare():
    workflow = (Path(__file__).parent / ".github/workflows/distributed-stock-finder.yml").read_text()
    prepare = workflow.split("\n  prepare:", 1)[1].split("\n    steps:", 1)[0]
    aggregate = workflow.split("\n  aggregate:", 1)[1].split("\n    steps:", 1)[0]
    for name in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_LIVE_FEED", "ALPACA_HISTORICAL_FEED"):
        lines = [line.strip() for line in prepare.splitlines() if line.strip().startswith(name + ":")]
        assert len(lines) == 1
        assert lines[0] in aggregate


@pytest.mark.parametrize("credentials", [True, False])
def test_saved_shards_reach_spread_check_or_fail_explicitly_without_losing_artifacts(monkeypatch, credentials):
    plan = fixtures.DistributedStockFinderReliabilityTests._saved_sdot_plan()
    library = {"research_queue": [fixtures.DistributedStockFinderReliabilityTests._saved_sdot_job(status="running")], "strategies": []}
    deleted, stages, markets = [], [], []

    class Artifacts:
        def read_json_gz(self, path):
            if path == finder.plan_path("dist-sdot"):
                return deepcopy(plan)
            for index in range(12):
                if path == finder.shard_path("dist-sdot", index):
                    return {"version": finder.DISTRIBUTED_SHARD_VERSION, "run_id": "dist-sdot",
                            "index": index, "timeframe": "1Min", "report": {"distributed_elapsed_seconds": 1}}
            raise FileNotFoundError(path)

        def delete(self, path):
            deleted.append(path)

    def mutate(fn, **kwargs):
        updated = fn(deepcopy(library))
        library.clear()
        library.update(updated)
        return deepcopy(library)

    def audit(market, *args, **kwargs):
        markets.append(market)
        assert market.headers["APCA-API-KEY-ID"] == "fixture-key"
        assert market.headers["APCA-API-SECRET-KEY"] == "fixture-secret"
        assert market.historical_feed == "iex"
        return {"status": "fixture_verified"}

    monkeypatch.setenv("ALPACA_API_KEY", "fixture-key" if credentials else "")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "fixture-secret" if credentials else "")
    monkeypatch.setenv("ALPACA_HISTORICAL_FEED", "iex")
    monkeypatch.setattr(finder, "PrivateRunArtifactStore", Artifacts)
    monkeypatch.setattr(finder, "mutate_remote_library", mutate)
    monkeypatch.setattr(finder, "_update_parent_cloud_progress", lambda *a, **kw: stages.append(kw["stage"]))
    monkeypatch.setattr(finder, "combine_strategy_family_reports", lambda *a, **kw: {})
    monkeypatch.setattr(finder, "combine_stock_timeframe_reports", lambda *a, **kw: {})
    monkeypatch.setattr(finder, "complete_stock_strategy_finder_from_optimization", lambda *a, **kw: {
        "generated_at": "2026-09-04T00:00:00Z", "symbol": "SDOT", "profile": {"name": "Very Deep"},
        "optimization": {"winner": {}, "winning_backtest": {"trades": [{"fixture": True}]},
                         "holdout_sessions": ["2026-09-03"]},
    })
    monkeypatch.setattr(finder, "historical_entry_spread_audit", audit)
    monkeypatch.setattr(finder, "apply_historical_spread_integrity_guard", lambda report, audit: report)
    if credentials:
        assert finder.command_aggregate("dist-sdot") == 0
        assert len(markets) == 1
        assert library["research_queue"][0]["status"] == "complete"
        assert library["research_queue"][0]["result_ref"]
        assert len(deleted) == 13  # existing successful-finalization cleanup only
    else:
        with pytest.raises(finder.AppError, match="aggregate GitHub Actions worker"):
            finder.command_aggregate("dist-sdot")
        saved = library["research_queue"][0]
        assert saved["status"] == "retry"
        assert saved["failure_step"] == "historical_spread_audit"
        assert "Saved shard results are retained" in saved["last_error"]
        assert not deleted and not markets
        assert saved["payload"]["distributed_shards_completed"] == list(range(12))
    assert "historical_spread_audit" in stages
