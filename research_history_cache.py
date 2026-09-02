"""Exact-compatible persistent raw-history cache for deterministic research.

The display/Quick Analysis cache is intentionally separate from this module.
Strict Strategy Lab and Finder research need the provider's raw bar records,
exact frozen start/end boundaries, adjustment mode, and feed to remain part of
the evidence contract. Exact finalized windows are reused directly. A later
rolling window may also reuse the finalized prefix and fetch only its missing
suffix with a small correction overlap.
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
RESEARCH_HISTORY_CACHE_VERSION = 2
RESEARCH_HISTORY_CACHE_DIRECTORY = "research-history-cache-v2"
RESEARCH_HISTORY_CACHE_MAX_ARTIFACTS = 64
RESEARCH_HISTORY_CACHE_MAX_BYTES = 320 * 1024 * 1024
RESEARCH_HISTORY_CORRECTION_OVERLAP_BARS = 3
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


def _parse_iso(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is missing")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return _utc(parsed)


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


def _base_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: identity.get(key)
        for key in (
            "cache_version",
            "provider",
            "historical_feed",
            "symbol",
            "timeframe",
            "adjustment",
        )
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
    return _utc(end) <= _utc(observed_at) - timedelta(seconds=interval)


def _rows_digest(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical(rows).encode("utf-8")).hexdigest()


def _row_time(row: Mapping[str, Any]) -> datetime | None:
    raw = row.get("t")
    if raw is None:
        raw = row.get("timestamp")
    if raw is None:
        raw = row.get("time")
    try:
        if isinstance(raw, datetime):
            return _utc(raw)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            number = float(raw)
            if number > 10**17:
                number /= 1_000_000_000.0
            elif number > 10**14:
                number /= 1_000_000.0
            elif number > 10**11:
                number /= 1_000.0
            return datetime.fromtimestamp(number, tz=UTC)
        return _parse_iso(raw)
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def _row_key(row: Mapping[str, Any]) -> str:
    stamp = _row_time(row)
    if stamp is not None:
        return "t:" + _iso(stamp)
    return "row:" + hashlib.sha256(_canonical(dict(row)).encode("utf-8")).hexdigest()


def _trim_rows(
    rows: list[dict[str, Any]],
    *,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    start_utc = _utc(start)
    end_utc = _utc(end)
    selected: list[dict[str, Any]] = []
    for row in rows:
        stamp = _row_time(row)
        if stamp is None:
            continue
        if start_utc <= stamp <= end_utc:
            selected.append(dict(row))
    selected.sort(key=lambda item: _row_time(item) or datetime.min.replace(tzinfo=UTC))
    return selected


def _merge_rows(
    cached_rows: list[dict[str, Any]],
    provider_rows: list[dict[str, Any]],
    *,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    merged = {_row_key(row): dict(row) for row in cached_rows}
    # Provider overlap wins so late corrections to the most recent finalized bars
    # replace the cached copy without changing older evidence.
    for row in provider_rows:
        merged[_row_key(row)] = dict(row)
    return _trim_rows(list(merged.values()), start=start, end=end)


def _touch(path: Path) -> None:
    try:
        os.utime(path, None)
    except OSError:
        pass


def _prune_cache(directory: Path, *, keep: Path | None = None) -> None:
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
    remaining_count = len(records)
    for path, size, _mtime in reversed(records):
        over_count = remaining_count > RESEARCH_HISTORY_CACHE_MAX_ARTIFACTS
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
            remaining_count -= 1
        except OSError:
            continue


def _read_unbound_artifact(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            decoded = json.load(handle)
    except (OSError, EOFError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    identity = decoded.get("identity")
    rows = decoded.get("rows")
    if not isinstance(identity, dict) or int(identity.get("cache_version") or 0) != RESEARCH_HISTORY_CACHE_VERSION:
        return None
    if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
        return None
    expected_digest = str(decoded.get("rows_sha256") or "")
    if not expected_digest or _rows_digest(rows) != expected_digest:
        return None
    decoded["rows"] = [dict(item) for item in rows]
    return decoded


def _read_artifact(
    path: Path,
    identity: Mapping[str, Any],
) -> dict[str, Any] | None:
    decoded = _read_unbound_artifact(path)
    if decoded is None or decoded.get("identity") != dict(identity):
        return None
    _touch(path)
    return decoded


def _compatible_finalized_artifacts(
    directory: Path,
    identity: Mapping[str, Any],
) -> list[tuple[Path, dict[str, Any]]]:
    wanted = _base_identity(identity)
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in directory.glob("*.json.gz"):
        decoded = _read_unbound_artifact(path)
        if decoded is None or not bool(decoded.get("finalized")):
            continue
        saved_identity = decoded.get("identity") or {}
        if _base_identity(saved_identity) != wanted:
            continue
        matches.append((path, decoded))
    return matches


def _best_reuse_candidate(
    directory: Path,
    identity: Mapping[str, Any],
) -> tuple[str, Path, dict[str, Any]] | None:
    requested_start = _parse_iso(identity.get("start"))
    requested_end = _parse_iso(identity.get("end"))
    covering: list[tuple[datetime, Path, dict[str, Any]]] = []
    prefix: list[tuple[datetime, Path, dict[str, Any]]] = []
    for path, decoded in _compatible_finalized_artifacts(directory, identity):
        saved = decoded.get("identity") or {}
        try:
            saved_start = _parse_iso(saved.get("start"))
            saved_end = _parse_iso(saved.get("end"))
        except ValueError:
            continue
        if saved_start <= requested_start and saved_end >= requested_end:
            covering.append((saved_start, path, decoded))
        elif saved_start <= requested_start < saved_end < requested_end:
            prefix.append((saved_end, path, decoded))
    if covering:
        # Prefer the narrowest covering history to minimize decompressed/trimmed rows.
        covering.sort(key=lambda item: item[0], reverse=True)
        _value, path, decoded = covering[0]
        return "covering_window", path, decoded
    if prefix:
        prefix.sort(key=lambda item: item[0], reverse=True)
        _value, path, decoded = prefix[0]
        return "incremental_prefix", path, decoded
    return None


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


def _result_metadata(
    *,
    identity: Mapping[str, Any],
    path: Path,
    rows: list[dict[str, Any]],
    rows_sha256: str,
    fetched_at: str,
    finalized: bool,
    cache_hit: bool,
    network_request: bool,
    reuse_mode: str,
    reused_row_count: int = 0,
    provider_row_count: int = 0,
    provider_start: datetime | None = None,
) -> dict[str, Any]:
    return {
        "cache_hit": cache_hit,
        "network_request": network_request,
        "reuse_mode": reuse_mode,
        "finalized": finalized,
        "fingerprint": research_history_fingerprint(identity),
        "rows_sha256": rows_sha256,
        "row_count": len(rows),
        "reused_row_count": max(0, int(reused_row_count)),
        "provider_row_count": max(0, int(provider_row_count)),
        "provider_start": _iso(provider_start) if provider_start is not None else "",
        "fetched_at": fetched_at,
        "artifact_path": str(path),
        "identity": dict(identity),
    }


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
    """Return raw provider rows with exact or finalized-prefix reuse."""

    now = _utc(observed_at or datetime.now(UTC))
    start_utc = _utc(start)
    end_utc = _utc(end)
    identity = research_history_identity(
        market,
        symbol=symbol,
        start=start_utc,
        end=end_utc,
        timeframe=timeframe,
        adjustment=adjustment,
    )
    directory = research_cache_directory(data_dir=data_dir, store=store)
    path = _artifact_path(directory, identity)
    with _CACHE_LOCK:
        cached = None if force_refresh else _read_artifact(path, identity)
        reuse = None if force_refresh or cached is not None else _best_reuse_candidate(directory, identity)
    if cached is not None and bool(cached.get("finalized")):
        rows = [dict(item) for item in cached.get("rows") or []]
        return ResearchHistoryResult(
            rows=rows,
            metadata=_result_metadata(
                identity=identity,
                path=path,
                rows=rows,
                rows_sha256=str(cached.get("rows_sha256") or ""),
                fetched_at=str(cached.get("fetched_at") or ""),
                finalized=True,
                cache_hit=True,
                network_request=False,
                reuse_mode="exact_window",
                reused_row_count=len(rows),
            ),
        )

    if reuse is not None and reuse[0] == "covering_window":
        _mode, source_path, source = reuse
        rows = _trim_rows(
            [dict(item) for item in source.get("rows") or []],
            start=start_utc,
            end=end_utc,
        )
        rows_sha256 = _rows_digest(rows)
        payload = {
            "identity": dict(identity),
            "fingerprint": research_history_fingerprint(identity),
            "rows_sha256": rows_sha256,
            "row_count": len(rows),
            "fetched_at": str(source.get("fetched_at") or _iso(now)),
            "finalized": True,
            "derived_from_fingerprint": str(source.get("fingerprint") or ""),
            "rows": rows,
        }
        with _CACHE_LOCK:
            _write_artifact(path, payload)
            _touch(source_path)
        return ResearchHistoryResult(
            rows=rows,
            metadata=_result_metadata(
                identity=identity,
                path=path,
                rows=rows,
                rows_sha256=rows_sha256,
                fetched_at=str(payload["fetched_at"]),
                finalized=True,
                cache_hit=True,
                network_request=False,
                reuse_mode="covering_window",
                reused_row_count=len(rows),
            ),
        )

    cached_prefix: list[dict[str, Any]] = []
    provider_start = start_utc
    reuse_mode = "network_full"
    if reuse is not None and reuse[0] == "incremental_prefix":
        _mode, source_path, source = reuse
        saved_identity = source.get("identity") or {}
        saved_end = _parse_iso(saved_identity.get("end"))
        cached_prefix = _trim_rows(
            [dict(item) for item in source.get("rows") or []],
            start=start_utc,
            end=min(saved_end, end_utc),
        )
        overlap = timedelta(
            seconds=(
                _TIMEFRAME_SECONDS[str(identity["timeframe"])]
                * RESEARCH_HISTORY_CORRECTION_OVERLAP_BARS
            )
        )
        provider_start = max(start_utc, saved_end - overlap)
        reuse_mode = "incremental_prefix"
        with _CACHE_LOCK:
            _touch(source_path)

    response = market.bars(
        [str(identity["symbol"])],
        start=provider_start,
        end=end_utc,
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
    provider_rows = [dict(item) for item in raw_rows or [] if isinstance(item, Mapping)]
    rows = (
        _merge_rows(
            cached_prefix,
            provider_rows,
            start=start_utc,
            end=end_utc,
        )
        if cached_prefix
        else [dict(item) for item in provider_rows]
    )
    finalized = _window_is_finalized(
        end=end_utc,
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
        metadata=_result_metadata(
            identity=identity,
            path=path,
            rows=rows,
            rows_sha256=rows_sha256,
            fetched_at=_iso(now),
            finalized=finalized,
            cache_hit=bool(cached_prefix),
            network_request=True,
            reuse_mode=reuse_mode,
            reused_row_count=len(cached_prefix),
            provider_row_count=len(provider_rows),
            provider_start=provider_start,
        ),
    )
