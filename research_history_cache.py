"""Exact-compatible persistent raw-history cache for deterministic research.

The display/Quick Analysis cache is intentionally separate from this module.
Strict Strategy Lab and Finder research need the provider's raw bar records,
exact frozen start/end boundaries, adjustment mode, and feed to remain part of
the evidence contract. This cache only reuses an artifact when every one of
those inputs matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any, Callable, Mapping


UTC = timezone.utc
RESEARCH_HISTORY_CACHE_VERSION = 1
RESEARCH_HISTORY_CACHE_DIRECTORY = "research-history-cache-v1"
RESEARCH_HISTORY_CACHE_MAX_ARTIFACTS = 64
RESEARCH_HISTORY_CACHE_MAX_BYTES = 320 * 1024 * 1024
_CACHE_LOCK = RLock()
_TIMEFRAME_SECONDS = {
    "1Min": 60,
    "5Min": 5 * 60,
    "15Min": 15 * 60,
    "1Hour": 60 * 60,
    "1Day": 24 * 60 * 60,
}


class ResearchHistoryCacheError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ResearchHistoryResult:
    rows: list[dict[str, Any]]
    metadata: dict[str, Any]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def _history_feed(market: Any) -> str:
    return str(getattr(market, "historical_feed", "") or "unknown").strip().lower()


def _provider_name(market: Any) -> str:
    cls = type(market)
    return f"{cls.__module__}.{cls.__qualname__}"


def research_cache_directory(
    *,
    data_dir: str | Path | None = None,
    store: Any | None = None,
) -> Path:
    if data_dir is not None and str(data_dir).strip():
        root = Path(data_dir).expanduser().resolve()
    else:
        store_directory = getattr(store, "directory", None)
        if store_directory is not None:
            root = Path(store_directory).expanduser().resolve()
        else:
            configured = str(
                os.environ.get("TRADING_INTELLIGENCE_DESKTOP_DATA_DIR")
                or os.environ.get("YOUTUBE_STRATEGY_DATA_DIR")
                or ".youtube_strategy_data"
            ).strip()
            root = Path(configured).expanduser().resolve()
    path = root / RESEARCH_HISTORY_CACHE_DIRECTORY
    path.mkdir(parents=True, exist_ok=True)
    return path


def research_history_identity(
    market: Any,
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe: str,
    adjustment: str,
) -> dict[str, Any]:
    clean_symbol = str(symbol or "").strip().upper()
    clean_timeframe = str(timeframe or "").strip()
    clean_adjustment = str(adjustment or "raw").strip().lower()
    if not clean_symbol:
        raise ResearchHistoryCacheError("Research history needs one stock symbol.")
    if clean_timeframe not in _TIMEFRAME_SECONDS:
        raise ResearchHistoryCacheError(
            f"Unsupported research timeframe: {clean_timeframe or 'missing'}."
        )
    if clean_adjustment not in {"raw", "split", "dividend", "all"}:
        raise ResearchHistoryCacheError(
            f"Unsupported research adjustment: {clean_adjustment}."
        )
    start_utc = _utc(start)
    end_utc = _utc(end)
    if end_utc <= start_utc:
        raise ResearchHistoryCacheError("Research history end must be after start.")
    return {
        "cache_version": RESEARCH_HISTORY_CACHE_VERSION,
        "provider": _provider_name(market),
        "historical_feed": _history_feed(market),
        "symbol": clean_symbol,
        "start": _iso(start_utc),
        "end": _iso(end_utc),
        "timeframe": clean_timeframe,
        "adjustment": clean_adjustment,
    }


def research_history_fingerprint(identity: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(dict(identity)).encode("utf-8")).hexdigest()


def _artifact_path(directory: Path, identity: Mapping[str, Any]) -> Path:
    digest = research_history_fingerprint(identity)[:24]
    symbol = str(identity.get("symbol") or "UNKNOWN")
    timeframe = str(identity.get("timeframe") or "bars")
    return directory / f"{symbol}-{timeframe}-{digest}.json.gz"


def _window_is_finalized(
    *,
    end: datetime,
    timeframe: str,
    observed_at: datetime,
) -> bool:
    interval = _TIMEFRAME_SECONDS[str(timeframe)]
    # Reuse only after at least one full bar duration has elapsed past the frozen
    # end boundary. A research run begun in the current candle therefore gets a
    # fresh provider read on its first later resume, rather than freezing a
    # partially formed bar forever.
    return _utc(end) <= _utc(observed_at) - timedelta(seconds=interval)


def _rows_digest(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical(rows).encode("utf-8")).hexdigest()


def _touch(path: Path) -> None:
    try:
        os.utime(path, None)
    except OSError:
        pass


def _prune_cache(directory: Path, *, keep: Path | None = None) -> None:
    """Bound local cache growth without ever deleting the artifact just written."""

    try:
        records = []
        for path in directory.glob("*.json.gz"):
            try:
                stat = path.stat()
            except OSError:
                continue
            records.append((path, int(stat.st_size), int(stat.st_mtime_ns)))
    except OSError:
        return
    records.sort(key=lambda item: item[2], reverse=True)
    keep_resolved = keep.resolve() if keep is not None else None
    total = sum(item[1] for item in records)
    for index, (path, size, _mtime) in enumerate(records):
        over_count = len(records) - index > RESEARCH_HISTORY_CACHE_MAX_ARTIFACTS
        over_bytes = total > RESEARCH_HISTORY_CACHE_MAX_BYTES
        if not over_count and not over_bytes:
            break
        try:
            if keep_resolved is not None and path.resolve() == keep_resolved:
                continue
        except OSError:
            pass
        try:
            path.unlink()
            total -= size
        except OSError:
            continue


def _read_artifact(
    path: Path,
    identity: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            decoded = json.load(handle)
    except (OSError, EOFError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    saved_identity = decoded.get("identity")
    rows = decoded.get("rows")
    if not isinstance(saved_identity, dict) or saved_identity != dict(identity):
        return None
    if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
        return None
    expected_digest = str(decoded.get("rows_sha256") or "")
    if not expected_digest or _rows_digest(rows) != expected_digest:
        return None
    decoded["rows"] = [dict(item) for item in rows]
    _touch(path)
    return decoded


def _write_artifact(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    os.close(fd)
    temporary = Path(temp_name)
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=5) as handle:
            json.dump(
                dict(payload),
                handle,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
                default=str,
            )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _prune_cache(path.parent, keep=path)
    finally:
        temporary.unlink(missing_ok=True)


def load_or_fetch_research_history(
    market: Any,
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe: str,
    adjustment: str = "raw",
    max_pages: int = 30,
    progress: Callable[[int], None] | None = None,
    data_dir: str | Path | None = None,
    store: Any | None = None,
    observed_at: datetime | None = None,
    force_refresh: bool = False,
) -> ResearchHistoryResult:
    """Return exact raw provider rows, reusing only a finalized matching window."""

    now = _utc(observed_at or datetime.now(UTC))
    identity = research_history_identity(
        market,
        symbol=symbol,
        start=start,
        end=end,
        timeframe=timeframe,
        adjustment=adjustment,
    )
    directory = research_cache_directory(data_dir=data_dir, store=store)
    path = _artifact_path(directory, identity)
    with _CACHE_LOCK:
        cached = None if force_refresh else _read_artifact(path, identity)
    if cached is not None and bool(cached.get("finalized")):
        rows = [dict(item) for item in cached.get("rows") or []]
        return ResearchHistoryResult(
            rows=rows,
            metadata={
                "cache_hit": True,
                "network_request": False,
                "finalized": True,
                "fingerprint": research_history_fingerprint(identity),
                "rows_sha256": str(cached.get("rows_sha256") or ""),
                "row_count": len(rows),
                "fetched_at": str(cached.get("fetched_at") or ""),
                "artifact_path": str(path),
                "identity": dict(identity),
            },
        )

    response = market.bars(
        [str(identity["symbol"])],
        start=_utc(start),
        end=_utc(end),
        timeframe=str(identity["timeframe"]),
        adjustment=str(identity["adjustment"]),
        max_pages=max(1, int(max_pages)),
        progress=progress,
    )
    raw_rows = (
        response.get(str(identity["symbol"]))
        if isinstance(response, Mapping)
        else None
    )
    rows = [dict(item) for item in raw_rows or [] if isinstance(item, Mapping)]
    finalized = _window_is_finalized(
        end=_utc(end),
        timeframe=str(identity["timeframe"]),
        observed_at=now,
    )
    rows_sha256 = _rows_digest(rows)
    payload = {
        "identity": dict(identity),
        "fingerprint": research_history_fingerprint(identity),
        "rows_sha256": rows_sha256,
        "row_count": len(rows),
        "fetched_at": _iso(now),
        "finalized": finalized,
        "rows": rows,
    }
    with _CACHE_LOCK:
        _write_artifact(path, payload)
    return ResearchHistoryResult(
        rows=rows,
        metadata={
            "cache_hit": False,
            "network_request": True,
            "finalized": finalized,
            "fingerprint": research_history_fingerprint(identity),
            "rows_sha256": rows_sha256,
            "row_count": len(rows),
            "fetched_at": _iso(now),
            "artifact_path": str(path),
            "identity": dict(identity),
        },
    )
