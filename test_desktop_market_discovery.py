from __future__ import annotations

from types import SimpleNamespace

import hybrid_runtime.market_discovery_job as discovery_job


def test_market_discovery_uses_web_engine_direction_and_keeps_validation_labels(
    monkeypatch, tmp_path
):
    library = {
        "strategies": [
            {
                "id": "validated",
                "name": "Validated Breakout",
                "direction": "long",
                "validation_status": "validated",
                "machine_rules": {"min_price": 1.0},
            },
            {
                "id": "research",
                "name": "Research Pullback",
                "direction": "long",
                "validation_status": "research_only",
                "machine_rules": {"min_price": 1.0},
            },
            {
                "id": "blocked",
                "name": "Unfaithful Rules",
                "direction": "long",
                "validation_status": "validated",
                "machine_rules": {"min_price": 1.0},
            },
        ]
    }

    import hybrid_runtime.library_source as library_source
    import hybrid_runtime.market_cache as market_cache
    import hybrid_runtime.desktop_settings as desktop_settings
    import trading_intelligence_core
    import trading_market_discovery
    import youtube_strategy_engine

    monkeypatch.setattr(
        library_source,
        "load_library_for_job",
        lambda *_args, **_kwargs: SimpleNamespace(
            library=library, metadata={"source": "fixture"}
        ),
    )
    monkeypatch.setattr(
        trading_intelligence_core,
        "strategy_integrity_report",
        lambda item: {
            "status": "blocked" if item.get("id") == "blocked" else "faithful"
        },
    )
    monkeypatch.setattr(
        desktop_settings,
        "load_desktop_settings",
        lambda *_args, **_kwargs: SimpleNamespace(market_feed="sip"),
    )
    monkeypatch.setattr(
        market_cache,
        "load_alpaca_credentials",
        lambda: ("api-key", "secret-key"),
    )

    class FakeMarket:
        def __init__(self, *_args, **_kwargs):
            pass

        def movers(self, top=30):
            return ["AAA", "BBB"][:top]

        def most_active(self, top=30):
            return ["BBB", "CCC"][:top]

    monkeypatch.setattr(youtube_strategy_engine, "AlpacaMarketData", FakeMarket)
    captured = {}

    def fake_scan(_market, symbols, strategies, *, progress):
        captured["symbols"] = symbols
        captured["strategies"] = strategies
        progress("Batch 1/1 · Loading shared intraday market features…")
        return [
            {
                "symbol": "AAA",
                "best_strategy_name": strategies[0]["name"],
                "validation_status": strategies[0]["validation_status"],
                "status": "MATCH",
                "score": 91,
                "metrics": {"price": 5.0},
            }
        ]

    monkeypatch.setattr(trading_market_discovery, "scan_market_strategies", fake_scan)
    progress_events = []
    result = discovery_job.run_market_discovery(
        {
            "universe": "momentum",
            "candidate_count": 50,
            "include_research": False,
        },
        data_dir=str(tmp_path),
        progress=lambda *args: progress_events.append(args),
        cancelled=lambda: False,
    )

    assert captured["symbols"] == ["AAA", "BBB", "CCC"]
    assert [item["id"] for item in captured["strategies"]] == ["validated"]
    assert result["match_count"] == 1
    assert result["validated_match_count"] == 1
    assert result["integrity_blocked_count"] == 1
    assert result["research_only"] is True
    assert result["affects_execution"] is False
    assert progress_events[-1][1] == "saving"


def test_market_discovery_can_target_one_research_strategy(monkeypatch, tmp_path):
    import hybrid_runtime.library_source as library_source
    import hybrid_runtime.market_cache as market_cache
    import hybrid_runtime.desktop_settings as desktop_settings
    import trading_intelligence_core
    import trading_market_discovery
    import youtube_strategy_engine

    strategy = {
        "id": "research",
        "name": "Research Pullback",
        "direction": "long",
        "validation_status": "research_only",
        "machine_rules": {"min_price": 1.0},
    }
    monkeypatch.setattr(
        library_source,
        "load_library_for_job",
        lambda *_args, **_kwargs: SimpleNamespace(
            library={"strategies": [strategy]}, metadata={}
        ),
    )
    monkeypatch.setattr(
        trading_intelligence_core,
        "strategy_integrity_report",
        lambda _item: {"status": "faithful"},
    )
    monkeypatch.setattr(
        desktop_settings,
        "load_desktop_settings",
        lambda *_args, **_kwargs: SimpleNamespace(market_feed="iex"),
    )
    monkeypatch.setattr(market_cache, "load_alpaca_credentials", lambda: ("key", "secret"))
    monkeypatch.setattr(youtube_strategy_engine, "AlpacaMarketData", lambda *_a, **_k: object())
    monkeypatch.setattr(
        trading_market_discovery,
        "scan_market_strategies",
        lambda _market, symbols, strategies, *, progress: [],
    )

    result = discovery_job.run_market_discovery(
        {
            "universe": "custom",
            "custom_symbols": "SDOT, LUCY",
            "candidate_count": 5,
            "include_research": True,
            "strategy_id": "research",
        },
        data_dir=str(tmp_path),
        progress=lambda *_args: None,
        cancelled=lambda: False,
    )

    assert result["candidate_symbols"] == ["SDOT", "LUCY"]
    assert result["strategy_count"] == 1
    assert result["selected_strategy_id"] == "research"
    assert result["feed"] == "iex"


def test_desktop_router_keeps_find_stocks_in_responsive_sidecar():
    from hybrid_runtime.contracts import ExecutionTarget, JobRequest
    from hybrid_runtime.router import RoutingPolicy

    decision = RoutingPolicy().decide(
        JobRequest("market.discovery", {"candidate_count": 50})
    )
    assert decision.target == ExecutionTarget.LOCAL


def test_scanner_launcher_validates_and_persists_web_target(tmp_path):
    from hybrid_runtime.scanner_launcher import (
        discover_scanner_target,
        normalize_scanner_target,
        save_scanner_target,
    )

    target = "https://example.streamlit.app"
    assert normalize_scanner_target(target) == target
    assert save_scanner_target(tmp_path, target) == target
    assert discover_scanner_target(tmp_path) == target
