"""Narrow market proxy that reuses exact finalized raw research windows.

Only single-symbol calls with explicit start/end/timeframe boundaries and
`adjustment="raw"` are eligible. Every other market-data method and ambiguous
bar request delegates untouched to the authoritative provider.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from research_history_cache import load_or_fetch_research_history


class CachedResearchMarket:
    def __init__(
        self,
        market: Any,
        *,
        store: Any | None = None,
        data_dir: str | None = None,
    ) -> None:
        self._market = market
        self._store = store
        self._data_dir = data_dir
        self.research_cache_events: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._market, name)

    def bars(self, symbols: list[str], **kwargs: Any) -> dict[str, list[dict[str, Any]]]:
        normalized = [str(value or "").strip().upper() for value in symbols or []]
        normalized = [value for value in normalized if value]
        start = kwargs.get("start")
        end = kwargs.get("end")
        timeframe = str(kwargs.get("timeframe") or "").strip()
        # The authoritative provider owns its default adjustment semantics. Cache
        # only strict raw-price research. Split/dividend-adjusted histories can
        # change after later corporate actions and therefore remain provider-owned.
        if "adjustment" not in kwargs:
            return self._market.bars(symbols, **kwargs)
        adjustment = str(kwargs.get("adjustment") or "").strip().lower()
        # Do not reinterpret batch/live/underspecified/provider-specific calls.
        if (
            len(normalized) != 1
            or not isinstance(start, datetime)
            or not isinstance(end, datetime)
            or not timeframe
            or adjustment != "raw"
        ):
            return self._market.bars(symbols, **kwargs)

        supported_names = {
            "start",
            "end",
            "timeframe",
            "adjustment",
            "max_pages",
            "progress",
        }
        # Provider-specific knobs such as explicit `feed=` must remain under
        # direct provider ownership until they become part of the cache identity.
        if any(name not in supported_names for name in kwargs):
            return self._market.bars(symbols, **kwargs)

        result = load_or_fetch_research_history(
            self._market,
            symbol=normalized[0],
            start=start,
            end=end,
            timeframe=timeframe,
            adjustment=adjustment,
            max_pages=int(kwargs.get("max_pages") or 30),
            progress=kwargs.get("progress"),
            data_dir=self._data_dir,
            store=self._store,
        )
        metadata = deepcopy(result.metadata)
        metadata.pop("artifact_path", None)
        self.research_cache_events.append(metadata)
        return {normalized[0]: [dict(item) for item in result.rows]}
