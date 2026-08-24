"""Research, video ingestion, market-data access, and conservative strategy testing.

This module deliberately never submits brokerage orders. Gemini is used to
extract a structured hypothesis; a deterministic engine evaluates that hypothesis.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd


ET = ZoneInfo("America/New_York")
UTC = timezone.utc
ALPACA_DATA_URL = "https://data.alpaca.markets"
GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_GEMINI_MODEL = "gemini-3.7-flash"
DEFAULT_DATA_DIRECTORY = ".youtube_strategy_data"
MAX_AUTOMATIC_BACKUPS = 30
MAX_RECOVERY_ITEMS = 150
MAX_STRATEGY_VERSIONS = 300
YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class AppError(RuntimeError):
    """An actionable error appropriate for displaying inside the application."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None or isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.strip().lower() in {"true", "yes", "1"}:
            return True
        if value.strip().lower() in {"false", "no", "0"}:
            return False
    return None


def parse_symbols(raw: str | list[str]) -> list[str]:
    parts = raw if isinstance(raw, list) else re.split(r"[,;\s]+", raw or "")
    result: list[str] = []
    for part in parts:
        symbol = str(part).strip().upper()
        if symbol and TICKER_PATTERN.fullmatch(symbol) and symbol not in result:
            result.append(symbol)
    return result


def normalize_youtube_url(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        raise AppError("Enter a public YouTube video URL.")
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]

    video_id = ""
    path_parts = [part for part in parsed.path.split("/") if part]
    if host == "youtu.be" and path_parts:
        video_id = path_parts[0]
    elif host in {"youtube.com", "youtube-nocookie.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed", "live", "v"}:
            video_id = path_parts[1]

    if not YOUTUBE_VIDEO_ID.fullmatch(video_id):
        if "list" in parse_qs(parsed.query) and not video_id:
            raise AppError(
                "A playlist-only link does not identify individual videos. "
                "Paste the public video links, one per line."
            )
        raise AppError("Use a valid public YouTube video link, not a channel or unrelated website.")
    return f"https://www.youtube.com/watch?v={video_id}"


def parse_youtube_urls(raw_text: str) -> tuple[list[str], list[str]]:
    found: list[str] = []
    errors: list[str] = []
    for raw_line in (raw_text or "").splitlines():
        line = raw_line.strip().strip("<>")
        if not line:
            continue
        try:
            normalized = normalize_youtube_url(line)
        except AppError as exc:
            errors.append(f"{line}: {exc}")
            continue
        if normalized not in found:
            found.append(normalized)
    return found, errors


def timestamped_youtube_url(url: str, timestamp: str) -> str:
    parts = str(timestamp or "").strip().split(":")
    try:
        if len(parts) == 2:
            seconds = int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        else:
            return url
    except ValueError:
        return url
    return f"{url}&t={max(0, seconds)}s"


def _json_request(
    url: str,
    headers: dict[str, str],
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: int = 45,
) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        response_text = exc.read().decode("utf-8", errors="replace")
        try:
            decoded = json.loads(response_text)
            message = decoded.get("error", {}).get("message") or decoded.get("message")
        except (json.JSONDecodeError, AttributeError):
            message = response_text[:350]
        if exc.code in {401, 403}:
            raise AppError(
                f"Request was denied ({exc.code}). Check the API key, account permissions, "
                f"and selected data feed. {message or ''}".strip()
            ) from exc
        if exc.code == 429:
            raise AppError(f"The provider's usage or rate limit was reached. {message or ''}".strip()) from exc
        raise AppError(f"Provider request failed ({exc.code}): {message or 'No details supplied.'}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise AppError(f"The provider could not be reached or took too long to respond: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AppError("The provider returned a response that was not valid JSON.") from exc


NULLABLE_NUMBER = {"type": ["number", "null"]}
NULLABLE_INTEGER = {"type": ["integer", "null"]}
NULLABLE_BOOLEAN = {"type": ["boolean", "null"]}
NULLABLE_STRING = {"type": ["string", "null"]}

MACHINE_RULE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "min_price": NULLABLE_NUMBER,
        "max_price": NULLABLE_NUMBER,
        "min_day_change_pct": NULLABLE_NUMBER,
        "min_relative_volume": NULLABLE_NUMBER,
        "min_dollar_volume": NULLABLE_NUMBER,
        "max_spread_pct": NULLABLE_NUMBER,
        "above_vwap": NULLABLE_BOOLEAN,
        "vwap_reclaim": NULLABLE_BOOLEAN,
        "max_vwap_distance_pct": NULLABLE_NUMBER,
        "breakout_lookback_bars": NULLABLE_INTEGER,
        "opening_range_minutes": NULLABLE_INTEGER,
        "volume_surge_ratio": NULLABLE_NUMBER,
        "minimum_green_bars": NULLABLE_INTEGER,
        "stop_loss_pct": NULLABLE_NUMBER,
        "reward_risk": NULLABLE_NUMBER,
        "max_hold_minutes": NULLABLE_INTEGER,
        "session_start": NULLABLE_STRING,
        "session_end": NULLABLE_STRING,
        "catalyst_required": NULLABLE_BOOLEAN,
    },
}

VIDEO_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "video_title": {"type": "string"},
        "creator": {"type": "string"},
        "summary": {"type": "string"},
        "visual_observations": {"type": "array", "items": {"type": "string"}},
        "general_risk_warnings": {"type": "array", "items": {"type": "string"}},
        "strategies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string"},
                    "direction": {"type": "string", "enum": ["long", "short", "both", "unclear"]},
                    "summary": {"type": "string"},
                    "indicators": {"type": "array", "items": {"type": "string"}},
                    "entry_conditions": {"type": "array", "items": {"type": "string"}},
                    "exit_conditions": {"type": "array", "items": {"type": "string"}},
                    "risk_rules": {"type": "array", "items": {"type": "string"}},
                    "avoid_conditions": {"type": "array", "items": {"type": "string"}},
                    "unresolved_rules": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                    "machine_rules": MACHINE_RULE_SCHEMA,
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "timestamp": {"type": "string"},
                                "description": {"type": "string"},
                                "visual_evidence": {"type": "string"},
                                "spoken_evidence": {"type": "string"},
                                "ticker": {"type": "string"},
                            },
                            "required": ["timestamp", "description", "visual_evidence", "spoken_evidence"],
                        },
                    },
                },
                "required": [
                    "name", "category", "direction", "summary", "entry_conditions", "exit_conditions",
                    "risk_rules", "avoid_conditions", "unresolved_rules", "confidence", "machine_rules", "evidence",
                ],
            },
        },
    },
    "required": ["video_title", "creator", "summary", "visual_observations", "general_risk_warnings", "strategies"],
}

VIDEO_EXTRACTION_PROMPT = """You are analyzing a public day-trading education video for a research app.
Actually inspect BOTH the video images and the audio; do not rely only on captions.
Extract every distinct trading setup as an unverified hypothesis, never as proof of profitability.

For each setup:
- Record what the presenter says and what the chart, indicators, time-and-sales, price levels,
  volume, entries, exits, and on-screen annotations actually show.
- Provide exact source timestamps for the most useful evidence and distinguish visual evidence
  from spoken evidence. If small chart text is unreadable, say so; do not invent values or tickers.
- Capture the stock universe, price range, liquidity, relative volume, VWAP, breakout level,
  opening range, trend, entry trigger, stop, target, reward/risk, session time, news catalyst,
  and explicit reasons to avoid the setup whenever the presenter gives them.
- Convert ONLY explicitly stated or visually verified numeric thresholds into machine_rules.
  Set unavailable thresholds to null. Never fabricate values to make a strategy testable.
- Put subjective or unavailable requirements (level 2, float, tape speed, proprietary indicators,
  borrow availability, visual discretion, historical catalyst timing) in unresolved_rules.
- Rate extraction confidence from 0 to 100 based on source clarity, NOT expected profitability.
- Flag cherry-picked results, missing losing examples, simulated P/L, unexplained indicators,
  promotions, and promises of guaranteed returns.
- Describe short-only strategies honestly; this first release only backtests long strategies.

Return only JSON matching the supplied schema.
"""


def _extract_interaction_text(response: dict[str, Any]) -> str:
    pieces: list[str] = []
    for step in response.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for content in step.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "text" and content.get("text"):
                pieces.append(str(content["text"]))
    if not pieces and isinstance(response.get("output_text"), str):
        pieces.append(response["output_text"])
    if not pieces:
        raise AppError("Gemini returned no readable text. The video might be unavailable, private, or restricted.")
    return "\n".join(pieces).strip()


def normalize_machine_rules(raw_rules: dict[str, Any] | None) -> dict[str, Any]:
    raw_rules = raw_rules if isinstance(raw_rules, dict) else {}
    result: dict[str, Any] = {}
    number_fields = {
        "min_price", "max_price", "min_day_change_pct", "min_relative_volume", "min_dollar_volume",
        "max_spread_pct", "max_vwap_distance_pct", "volume_surge_ratio", "stop_loss_pct", "reward_risk",
    }
    integer_fields = {"breakout_lookback_bars", "opening_range_minutes", "minimum_green_bars", "max_hold_minutes"}
    boolean_fields = {"above_vwap", "vwap_reclaim", "catalyst_required"}
    for name in MACHINE_RULE_SCHEMA["properties"]:
        value = raw_rules.get(name)
        if name in number_fields:
            result[name] = safe_float(value)
        elif name in integer_fields:
            numeric = safe_float(value)
            result[name] = max(1, int(numeric)) if numeric is not None and numeric >= 1 else None
        elif name in boolean_fields:
            result[name] = safe_bool(value)
        else:
            text = str(value).strip() if value is not None else ""
            result[name] = text if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text) else None

    for name in {"min_price", "max_price", "min_relative_volume", "min_dollar_volume", "max_spread_pct", "max_vwap_distance_pct", "volume_surge_ratio", "stop_loss_pct", "reward_risk"}:
        if result[name] is not None and result[name] < 0:
            result[name] = None
    if result["stop_loss_pct"] is not None and not 0 < result["stop_loss_pct"] < 100:
        result["stop_loss_pct"] = None
    if result["reward_risk"] is not None and result["reward_risk"] <= 0:
        result["reward_risk"] = None
    if result["min_price"] is not None and result["max_price"] is not None and result["min_price"] > result["max_price"]:
        result["min_price"], result["max_price"] = result["max_price"], result["min_price"]
    return result


def strategy_fingerprint(source_url: str, name: str) -> str:
    material = f"{normalize_youtube_url(source_url)}|{name.strip().lower()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:20]


class GeminiVideoAnalyzer:
    def __init__(self, api_key: str, model: str = DEFAULT_GEMINI_MODEL):
        if not str(api_key or "").strip():
            raise AppError("Add GEMINI_API_KEY in this app's Streamlit Secrets before analyzing videos.")
        self.api_key = str(api_key).strip()
        self.model = str(model or DEFAULT_GEMINI_MODEL).strip()

    def analyze(self, url: str, extra_instructions: str = "") -> dict[str, Any]:
        normalized_url = normalize_youtube_url(url)
        prompt = VIDEO_EXTRACTION_PROMPT
        if extra_instructions.strip():
            prompt += "\nSpecific user focus: " + extra_instructions.strip()[:3000]

        payload = {
            "model": self.model,
            "input": [
                {"type": "video", "uri": normalized_url},
                {"type": "text", "text": prompt},
            ],
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": VIDEO_ANALYSIS_SCHEMA,
            },
            "store": False,
        }
        data = _json_request(
            GEMINI_INTERACTIONS_URL,
            {"x-goog-api-key": self.api_key, "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
            payload=payload,
            timeout=300,
        )
        text = _extract_interaction_text(data)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AppError("Gemini's analysis was not valid structured JSON. Try the video again.") from exc
        if not isinstance(parsed, dict):
            raise AppError("Gemini returned an unexpected analysis format.")

        parsed["url"] = normalized_url
        parsed["analyzed_at"] = isoformat_utc(utc_now())
        parsed["model"] = self.model
        parsed["interaction_id"] = data.get("id")
        parsed["usage"] = data.get("usage") or {}
        normalized_strategies = []
        for raw_strategy in parsed.get("strategies") or []:
            if not isinstance(raw_strategy, dict):
                continue
            name = str(raw_strategy.get("name") or "Unnamed strategy").strip()
            raw_strategy["id"] = strategy_fingerprint(normalized_url, name)
            raw_strategy["name"] = name
            raw_strategy["source_url"] = normalized_url
            raw_strategy["source_title"] = str(parsed.get("video_title") or "YouTube video")
            raw_strategy["creator"] = str(parsed.get("creator") or "Unknown creator")
            raw_strategy["analyzed_at"] = parsed["analyzed_at"]
            raw_strategy["machine_rules"] = normalize_machine_rules(raw_strategy.get("machine_rules"))
            raw_strategy["confidence"] = max(0, min(100, safe_float(raw_strategy.get("confidence"), 0.0) or 0.0))
            raw_strategy.setdefault("approved", False)
            raw_strategy.setdefault("source_warnings", parsed.get("general_risk_warnings") or [])
            normalized_strategies.append(raw_strategy)
        parsed["strategies"] = normalized_strategies
        return parsed


class StrategyStore:
    def __init__(self, directory: str | Path | None = None):
        chosen = directory or os.environ.get("YOUTUBE_STRATEGY_DATA_DIR") or DEFAULT_DATA_DIRECTORY
        self.directory = Path(chosen)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "strategy_library.json"
        self.backups_directory = self.directory / "automatic_backups"

    @staticmethod
    def blank() -> dict[str, Any]:
        return {
            "version": 2,
            "videos": [],
            "strategies": [],
            "paper_positions": [],
            "recovery_items": [],
            "strategy_versions": [],
            "updated_at": None,
        }

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self.blank()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AppError("Saved strategy data could not be read. Restore a previously exported backup.") from exc
        if not isinstance(data, dict):
            raise AppError("The saved strategy library is not a JSON object.")
        result = self.blank()
        result.update(data)
        for name in ("videos", "strategies", "paper_positions", "recovery_items", "strategy_versions"):
            if not isinstance(result[name], list):
                result[name] = []
        return result

    def _make_automatic_backup(self) -> None:
        if not self.path.exists():
            return
        self.backups_directory.mkdir(parents=True, exist_ok=True)
        timestamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
        destination = self.backups_directory / f"strategy_{timestamp}_{uuid4().hex[:8]}.json"
        try:
            shutil.copy2(self.path, destination)
            backups = sorted(self.backups_directory.glob("strategy_*.json"), reverse=True)
            for previous in backups[MAX_AUTOMATIC_BACKUPS:]:
                previous.unlink(missing_ok=True)
        except OSError as exc:
            raise AppError("An automatic strategy backup could not be created. Download a manual backup and try again.") from exc

    @staticmethod
    def _remember_strategy(
        data: dict[str, Any],
        strategy: dict[str, Any],
        reason: str,
        *,
        backtest_summary: dict[str, Any] | None = None,
    ) -> None:
        snapshot = json.loads(json.dumps(strategy, default=str))
        snapshot["machine_rules"] = normalize_machine_rules(snapshot.get("machine_rules"))
        checkpoint = {
            "id": uuid4().hex[:16],
            "strategy_id": snapshot.get("id"),
            "strategy_name": snapshot.get("name") or "Unnamed strategy",
            "source_url": snapshot.get("source_url") or "",
            "saved_at": isoformat_utc(utc_now()),
            "reason": reason,
            "strategy": snapshot,
        }
        if backtest_summary:
            checkpoint["backtest_summary"] = json.loads(json.dumps(backtest_summary, default=str))
        data.setdefault("strategy_versions", []).insert(0, checkpoint)
        data["strategy_versions"] = data["strategy_versions"][:MAX_STRATEGY_VERSIONS]

    @staticmethod
    def _remember_deleted(
        data: dict[str, Any],
        *,
        kind: str,
        title: str,
        video: dict[str, Any] | None = None,
        strategies: list[dict[str, Any]] | None = None,
        position: dict[str, Any] | None = None,
    ) -> None:
        entry = {
            "id": uuid4().hex[:16],
            "kind": kind,
            "title": title,
            "deleted_at": isoformat_utc(utc_now()),
            "video": json.loads(json.dumps(video, default=str)) if video else None,
            "strategies": json.loads(json.dumps(strategies or [], default=str)),
            "position": json.loads(json.dumps(position, default=str)) if position else None,
        }
        data.setdefault("recovery_items", []).insert(0, entry)
        data["recovery_items"] = data["recovery_items"][:MAX_RECOVERY_ITEMS]

    def save(self, data: dict[str, Any]) -> dict[str, Any]:
        value = self.blank()
        value.update(data)
        value["version"] = max(2, int(safe_float(value.get("version"), 2) or 2))
        value["updated_at"] = isoformat_utc(utc_now())
        descriptor, temporary_name = tempfile.mkstemp(prefix="strategy_", suffix=".json", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                json.dump(value, temporary, indent=2, default=str, allow_nan=False)
                temporary.flush()
                os.fsync(temporary.fileno())
            self._make_automatic_backup()
            os.replace(temporary_name, self.path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return value

    def add_video_analysis(self, analysis: dict[str, Any]) -> dict[str, Any]:
        data = self.load()
        url = normalize_youtube_url(str(analysis.get("url") or ""))
        video_record = {key: value for key, value in analysis.items() if key != "strategies"}
        data["videos"] = [item for item in data["videos"] if item.get("url") != url]
        data["videos"].insert(0, video_record)
        previous = {item.get("id"): item for item in data["strategies"]}
        for strategy in analysis.get("strategies") or []:
            item = dict(strategy)
            existing = previous.get(item.get("id")) or {}
            item["machine_rules"] = normalize_machine_rules(item.get("machine_rules"))
            if existing:
                existing_rules = normalize_machine_rules(existing.get("machine_rules"))
                if item["machine_rules"] != existing_rules:
                    self._remember_strategy(data, item, "Alternative AI extraction — not applied")
                preserved = dict(existing)
                preserved["latest_extracted_rules"] = item["machine_rules"]
                preserved["last_reanalyzed_at"] = analysis.get("analyzed_at") or isoformat_utc(utc_now())
                preserved["reanalysis_changed_rules"] = item["machine_rules"] != existing_rules
                previous[item["id"]] = preserved
            else:
                item.setdefault("approved", False)
                self._remember_strategy(data, item, "Initial video extraction")
                previous[item["id"]] = item
        data["strategies"] = list(previous.values())
        return self.save(data)

    def update_strategy(self, strategy_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        data = self.load()
        for strategy in data["strategies"]:
            if strategy.get("id") == strategy_id:
                clean = dict(updates)
                if "machine_rules" in clean:
                    clean["machine_rules"] = normalize_machine_rules(clean["machine_rules"])
                    if clean["machine_rules"] != normalize_machine_rules(strategy.get("machine_rules")):
                        self._remember_strategy(data, strategy, "Before manual rule change")
                        clean["manually_edited"] = True
                strategy.update(clean)
                return self.save(data)
        raise AppError("That strategy no longer exists. Refresh the page and try again.")

    def delete_strategy(self, strategy_id: str) -> dict[str, Any]:
        data = self.load()
        deleted = [strategy for strategy in data["strategies"] if strategy.get("id") == strategy_id]
        if deleted:
            self._remember_deleted(data, kind="strategy", title=str(deleted[0].get("name") or "Trading strategy"), strategies=deleted)
            for strategy in deleted:
                self._remember_strategy(data, strategy, "Before strategy deletion")
        data["strategies"] = [strategy for strategy in data["strategies"] if strategy.get("id") != strategy_id]
        return self.save(data)

    def delete_video(self, source_url: str, *, delete_strategies: bool = False) -> dict[str, Any]:
        data = self.load()
        url = normalize_youtube_url(source_url)
        videos = [video for video in data["videos"] if video.get("url") == url]
        related = [strategy for strategy in data["strategies"] if strategy.get("source_url") == url] if delete_strategies else []
        if videos or related:
            video = videos[0] if videos else None
            self._remember_deleted(
                data,
                kind="video_and_strategies" if delete_strategies else "video",
                title=str((video or {}).get("video_title") or "YouTube video"),
                video=video,
                strategies=related,
            )
            for strategy in related:
                self._remember_strategy(data, strategy, "Before video and strategy deletion")
        data["videos"] = [video for video in data["videos"] if video.get("url") != url]
        if delete_strategies:
            data["strategies"] = [strategy for strategy in data["strategies"] if strategy.get("source_url") != url]
        return self.save(data)

    def restore_recovery_item(self, recovery_id: str) -> dict[str, Any]:
        data = self.load()
        recovered = next((item for item in data["recovery_items"] if item.get("id") == recovery_id), None)
        if not recovered:
            raise AppError("That deleted item is no longer available in recovery.")
        video = recovered.get("video")
        if isinstance(video, dict) and video.get("url"):
            data["videos"] = [item for item in data["videos"] if item.get("url") != video["url"]]
            data["videos"].insert(0, video)
        for strategy in recovered.get("strategies") or []:
            if not isinstance(strategy, dict) or not strategy.get("id"):
                continue
            current = next((item for item in data["strategies"] if item.get("id") == strategy["id"]), None)
            if current:
                self._remember_strategy(data, current, "Before restoring deleted strategy")
            data["strategies"] = [item for item in data["strategies"] if item.get("id") != strategy["id"]]
            data["strategies"].insert(0, strategy)
        position = recovered.get("position")
        if isinstance(position, dict) and position.get("id"):
            data["paper_positions"] = [item for item in data["paper_positions"] if item.get("id") != position["id"]]
            data["paper_positions"].insert(0, position)
        data["recovery_items"] = [item for item in data["recovery_items"] if item.get("id") != recovery_id]
        return self.save(data)

    def restore_strategy_version(self, version_id: str) -> dict[str, Any]:
        data = self.load()
        version = next((item for item in data["strategy_versions"] if item.get("id") == version_id), None)
        snapshot = version.get("strategy") if version else None
        if not isinstance(snapshot, dict) or not snapshot.get("id"):
            raise AppError("That strategy checkpoint could not be found.")
        current = next((item for item in data["strategies"] if item.get("id") == snapshot["id"]), None)
        if current:
            self._remember_strategy(data, current, "Before restoring earlier checkpoint")
        restored = json.loads(json.dumps(snapshot, default=str))
        restored["machine_rules"] = normalize_machine_rules(restored.get("machine_rules"))
        restored["restored_at"] = isoformat_utc(utc_now())
        data["strategies"] = [item for item in data["strategies"] if item.get("id") != restored["id"]]
        data["strategies"].insert(0, restored)
        return self.save(data)

    def record_backtest(
        self,
        strategy_id: str,
        results: list[dict[str, Any]],
        *,
        timeframe: str = "",
        history_days: int | None = None,
    ) -> dict[str, Any]:
        data = self.load()
        strategy = next((item for item in data["strategies"] if item.get("id") == strategy_id), None)
        if not strategy:
            raise AppError("The tested strategy no longer exists. Refresh the strategy library and try again.")
        summary = {
            "tested_at": isoformat_utc(utc_now()),
            "symbols": [str(result.get("symbol") or "?") for result in results],
            "net_pnl": round(sum(safe_float((result.get("metrics") or {}).get("net_pnl"), 0.0) or 0.0 for result in results), 2),
            "holdout_net_pnl": round(sum(safe_float((result.get("out_of_sample") or {}).get("net_pnl"), 0.0) or 0.0 for result in results), 2),
            "trade_count": sum(int(safe_float((result.get("metrics") or {}).get("trade_count"), 0.0) or 0.0) for result in results),
            "holdout_trade_count": sum(int(safe_float((result.get("out_of_sample") or {}).get("trade_count"), 0.0) or 0.0) for result in results),
            "timeframe": timeframe,
            "history_days": history_days,
        }
        strategy["last_backtest"] = summary
        self._remember_strategy(data, strategy, "Backtest checkpoint", backtest_summary=summary)
        return self.save(data)

    def save_optimized_strategy(
        self,
        source_strategy_id: str,
        symbol: str,
        machine_rules: dict[str, Any],
        optimization_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        symbols = parse_symbols(symbol)
        if len(symbols) != 1:
            raise AppError("Choose one valid stock ticker before saving an optimized strategy.")
        target_symbol = symbols[0]
        data = self.load()
        source = next((item for item in data["strategies"] if item.get("id") == source_strategy_id), None)
        if not source:
            raise AppError("The original strategy no longer exists. Restore it before saving an optimized version.")
        normalized = normalize_machine_rules(machine_rules)
        signature = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        optimized_id = hashlib.sha256(
            f"optimized|{source_strategy_id}|{target_symbol}|{signature}".encode("utf-8")
        ).hexdigest()[:20]
        existing = next((item for item in data["strategies"] if item.get("id") == optimized_id), None)
        if existing:
            self._remember_strategy(data, existing, "Before updating stock-specific optimization")
        optimized = json.loads(json.dumps(source, default=str))
        source_name = str(source.get("name") or "Trading strategy")
        optimized.update(
            {
                "id": optimized_id,
                "name": f"{source_name} — {target_symbol} optimized",
                "machine_rules": normalized,
                "approved": bool(existing.get("approved")) if existing else False,
                "optimized_for_symbol": target_symbol,
                "parent_strategy_id": source_strategy_id,
                "optimized_at": isoformat_utc(utc_now()),
                "optimization_summary": json.loads(json.dumps(optimization_summary or {}, default=str)),
            }
        )
        optimized.pop("latest_extracted_rules", None)
        optimized.pop("reanalysis_changed_rules", None)
        full_metrics = (optimization_summary or {}).get("full_metrics") or {}
        holdout_metrics = (optimization_summary or {}).get("holdout_metrics") or {}
        if full_metrics:
            optimized["last_backtest"] = {
                "tested_at": optimized["optimized_at"],
                "symbols": [target_symbol],
                "net_pnl": safe_float(full_metrics.get("net_pnl"), 0.0) or 0.0,
                "holdout_net_pnl": safe_float(holdout_metrics.get("net_pnl"), 0.0) or 0.0,
                "trade_count": int(safe_float(full_metrics.get("trade_count"), 0.0) or 0.0),
                "holdout_trade_count": int(safe_float(holdout_metrics.get("trade_count"), 0.0) or 0.0),
                "timeframe": (optimization_summary or {}).get("timeframe") or "",
                "history_days": (optimization_summary or {}).get("history_days"),
            }
        data["strategies"] = [item for item in data["strategies"] if item.get("id") != optimized_id]
        data["strategies"].insert(0, optimized)
        self._remember_strategy(
            data,
            optimized,
            f"Saved {target_symbol}-specific optimized strategy",
            backtest_summary=optimized.get("last_backtest"),
        )
        return self.save(data)

    def list_automatic_backups(self) -> list[dict[str, Any]]:
        if not self.backups_directory.exists():
            return []
        results = []
        for path in sorted(self.backups_directory.glob("strategy_*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
            except (OSError, json.JSONDecodeError):
                continue
            results.append(
                {
                    "id": path.name,
                    "saved_at": data.get("updated_at"),
                    "videos": len(data.get("videos") or []),
                    "strategies": len(data.get("strategies") or []),
                    "paper_positions": len(data.get("paper_positions") or []),
                }
            )
        return results

    def restore_automatic_backup(self, backup_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"strategy_\d{8}T\d{12}Z_[a-f0-9]{8}\.json", str(backup_id)):
            raise AppError("That automatic backup is not valid.")
        path = self.backups_directory / backup_id
        if not path.is_file():
            raise AppError("That automatic backup no longer exists.")
        try:
            return self.import_data(path.read_bytes())
        except OSError as exc:
            raise AppError("The selected automatic backup could not be read.") from exc

    def import_data(self, raw: bytes | str) -> dict[str, Any]:
        try:
            parsed = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("The imported backup is not a valid UTF-8 JSON file.") from exc
        if not isinstance(parsed, dict) or not isinstance(parsed.get("strategies"), list):
            raise AppError("The backup must contain a strategies list from this application.") from exc
        current = self.load()
        videos = {video.get("url"): video for video in current["videos"] if video.get("url")}
        for video in parsed.get("videos") or []:
            if isinstance(video, dict) and video.get("url"):
                try:
                    video["url"] = normalize_youtube_url(str(video["url"]))
                except AppError:
                    continue
                videos[video["url"]] = video
        strategies = {item.get("id"): item for item in current["strategies"] if item.get("id")}
        for item in parsed["strategies"]:
            if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
                continue
            clean = dict(item)
            clean["machine_rules"] = normalize_machine_rules(clean.get("machine_rules"))
            strategies[clean["id"]] = clean
        positions = {item.get("id"): item for item in current["paper_positions"] if item.get("id")}
        for item in parsed.get("paper_positions") or []:
            if isinstance(item, dict) and item.get("id"):
                positions[item["id"]] = item
        current["videos"] = list(videos.values())
        current["strategies"] = list(strategies.values())
        current["paper_positions"] = list(positions.values())
        for field_name, limit in (("recovery_items", MAX_RECOVERY_ITEMS), ("strategy_versions", MAX_STRATEGY_VERSIONS)):
            known = {item.get("id") for item in current[field_name] if isinstance(item, dict)}
            for item in parsed.get(field_name) or []:
                if isinstance(item, dict) and item.get("id") and item["id"] not in known:
                    current[field_name].append(item)
                    known.add(item["id"])
            current[field_name] = current[field_name][:limit]
        return self.save(current)

    def add_position(self, position: dict[str, Any]) -> dict[str, Any]:
        data = self.load()
        clean = dict(position)
        clean["id"] = clean.get("id") or uuid4().hex[:16]
        clean["symbol"] = str(clean.get("symbol") or "").strip().upper()
        if not TICKER_PATTERN.fullmatch(clean["symbol"]):
            raise AppError("Enter a valid stock ticker for the paper position.")
        quantity = safe_float(clean.get("quantity"))
        entry = safe_float(clean.get("entry_price"))
        if quantity is None or quantity <= 0 or entry is None or entry <= 0:
            raise AppError("Paper positions need a positive share quantity and entry price.")
        clean["quantity"] = quantity
        clean["entry_price"] = entry
        clean.setdefault("status", "open")
        clean.setdefault("opened_at", isoformat_utc(utc_now()))
        data["paper_positions"].insert(0, clean)
        return self.save(data)

    def update_position(self, position_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        data = self.load()
        for position in data["paper_positions"]:
            if position.get("id") != position_id:
                continue
            candidate = dict(position)
            candidate.update(updates)
            quantity = safe_float(candidate.get("quantity"))
            price = safe_float(candidate.get("entry_price"))
            if quantity is None or quantity <= 0 or price is None or price <= 0:
                raise AppError("Quantity and entry price must both be greater than zero.")
            if candidate.get("status") == "closed":
                exit_price = safe_float(candidate.get("exit_price"))
                if exit_price is None or exit_price <= 0:
                    raise AppError("A closed position needs a positive exit price.")
                candidate["closed_at"] = candidate.get("closed_at") or isoformat_utc(utc_now())
                candidate["realized_pnl"] = round((exit_price - price) * quantity, 2)
            position.update(candidate)
            return self.save(data)
        raise AppError("That paper position could not be found.")

    def delete_position(self, position_id: str) -> dict[str, Any]:
        data = self.load()
        deleted = next((item for item in data["paper_positions"] if item.get("id") == position_id), None)
        if deleted:
            self._remember_deleted(
                data,
                kind="paper_position",
                title=f'{deleted.get("symbol", "?")} paper position',
                position=deleted,
            )
        data["paper_positions"] = [position for position in data["paper_positions"] if position.get("id") != position_id]
        return self.save(data)


class AlpacaMarketData:
    def __init__(self, api_key: str, secret_key: str, live_feed: str = "iex", historical_feed: str = "sip"):
        if not api_key or not secret_key:
            raise AppError("Add ALPACA_API_KEY and ALPACA_SECRET_KEY in this app's Streamlit Secrets.")
        self.headers = {
            "APCA-API-KEY-ID": api_key.strip(),
            "APCA-API-SECRET-KEY": secret_key.strip(),
            "Accept": "application/json",
        }
        self.live_feed = live_feed if live_feed in {"iex", "sip"} else "iex"
        self.historical_feed = historical_feed if historical_feed in {"iex", "sip"} else "sip"

    def _get(self, path: str, parameters: dict[str, Any] | None = None) -> Any:
        query = "?" + urlencode(parameters) if parameters else ""
        return _json_request(f"{ALPACA_DATA_URL}{path}{query}", self.headers, timeout=50)

    def movers(self, top: int = 30) -> list[str]:
        data = self._get("/v1beta1/screener/stocks/movers", {"top": max(1, min(50, int(top)))})
        return parse_symbols([item.get("symbol", "") for item in data.get("gainers") or [] if isinstance(item, dict)])

    def most_active(self, top: int = 30) -> list[str]:
        data = self._get("/v1beta1/screener/stocks/most-actives", {"top": max(1, min(100, int(top))), "by": "volume"})
        rows = data.get("most_actives") or data.get("mostActives") or []
        return parse_symbols([item.get("symbol", "") for item in rows if isinstance(item, dict)])

    def snapshots(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        clean = parse_symbols(symbols)
        if not clean:
            return {}
        data = self._get("/v2/stocks/snapshots", {"symbols": ",".join(clean), "feed": self.live_feed})
        if "snapshots" in data and isinstance(data["snapshots"], dict):
            data = data["snapshots"]
        return {str(symbol).upper(): snapshot for symbol, snapshot in data.items() if isinstance(snapshot, dict)}

    def bars(
        self,
        symbols: list[str],
        *,
        start: datetime,
        end: datetime,
        timeframe: str = "1Min",
        feed: str | None = None,
        max_pages: int = 15,
        progress: Callable[[int], None] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        clean = parse_symbols(symbols)
        if not clean:
            return {}
        chosen_feed = feed or self.historical_feed
        merged = {symbol: [] for symbol in clean}
        page_token: str | None = None
        seen_tokens: set[str] = set()
        for page_number in range(max(1, max_pages)):
            parameters: dict[str, Any] = {
                "symbols": ",".join(clean),
                "timeframe": timeframe,
                "start": isoformat_utc(start),
                "end": isoformat_utc(end),
                "limit": 10000,
                "adjustment": "split",
                "feed": chosen_feed,
                "sort": "asc",
            }
            if page_token:
                parameters["page_token"] = page_token
            try:
                response = self._get("/v2/stocks/bars", parameters)
            except AppError as exc:
                if chosen_feed == "sip" and "403" in str(exc):
                    raise AppError(
                        "SIP historical data was denied for this period. For the free Alpaca plan, "
                        "use data ending at least 16 minutes ago or switch historical feed to IEX."
                    ) from exc
                raise
            for symbol, rows in (response.get("bars") or {}).items():
                normalized = str(symbol).upper()
                if normalized in merged and isinstance(rows, list):
                    merged[normalized].extend(row for row in rows if isinstance(row, dict))
            if progress:
                progress(page_number + 1)
            next_token = response.get("next_page_token")
            if not next_token:
                break
            if str(next_token) in seen_tokens:
                raise AppError("Alpaca returned a repeated pagination token; historical download was stopped safely.")
            seen_tokens.add(str(next_token))
            page_token = str(next_token)
        else:
            raise AppError(
                "The requested historical range is too large for one run. Use fewer tickers, "
                "a shorter period, or a larger candle interval."
            )
        return merged

    def news(self, symbols: list[str], hours: int = 24) -> dict[str, list[dict[str, Any]]]:
        clean = parse_symbols(symbols)
        if not clean:
            return {}
        response = self._get(
            "/v1beta1/news",
            {
                "symbols": ",".join(clean),
                "start": isoformat_utc(utc_now() - timedelta(hours=max(1, hours))),
                "end": isoformat_utc(utc_now()),
                "sort": "desc",
                "limit": 50,
                "include_content": "false",
            },
        )
        output: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in clean}
        for item in response.get("news") or []:
            if not isinstance(item, dict):
                continue
            for symbol in item.get("symbols") or []:
                normalized = str(symbol).upper()
                if normalized in output:
                    output[normalized].append(item)
        return output


def bars_to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    columns = ["open", "high", "low", "close", "volume", "timestamp", "session", "session_minute"]
    if not rows:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(rows).rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "t": "timestamp"})
    required = {"open", "high", "low", "close", "volume", "timestamp"}
    if not required.issubset(frame.columns):
        return pd.DataFrame(columns=columns)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    for name in ("open", "high", "low", "close", "volume"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.dropna(subset=list(required)).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    frame = frame[(frame["open"] > 0) & (frame["high"] > 0) & (frame["low"] > 0) & (frame["close"] > 0) & (frame["volume"] >= 0)].copy()
    local = frame["timestamp"].dt.tz_convert(ET)
    minute = local.dt.hour * 60 + local.dt.minute
    mask = (minute >= 9 * 60 + 30) & (minute < 16 * 60)
    frame = frame.loc[mask].copy().reset_index(drop=True)
    local = frame["timestamp"].dt.tz_convert(ET)
    frame["session"] = local.dt.date.astype(str)
    frame["session_minute"] = local.dt.hour * 60 + local.dt.minute - (9 * 60 + 30)
    return frame


def add_indicators(frame: pd.DataFrame, strategy: dict[str, Any]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    data = frame.copy().sort_values("timestamp").reset_index(drop=True)
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    session = data.groupby("session", sort=False)
    typical = (data["high"] + data["low"] + data["close"]) / 3.0
    data["cum_volume"] = session["volume"].cumsum()
    data["cum_dollar_volume"] = (typical * data["volume"]).groupby(data["session"], sort=False).cumsum()
    data["vwap"] = data["cum_dollar_volume"].div(data["cum_volume"].replace(0, float("nan")))
    data["vwap_distance_pct"] = (data["close"].div(data["vwap"]) - 1.0) * 100.0
    data["previous_close"] = data.groupby("session", sort=False)["close"].shift(1)
    data["previous_vwap"] = data.groupby("session", sort=False)["vwap"].shift(1)
    daily_close = data.groupby("session", sort=False)["close"].last()
    previous_daily_close = daily_close.shift(1).to_dict()
    data["previous_daily_close"] = data["session"].map(previous_daily_close)
    data["day_change_pct"] = (data["close"].div(data["previous_daily_close"]) - 1.0) * 100.0
    historical_session_volume = data.groupby("session", sort=False)["volume"].sum().shift(1).rolling(20, min_periods=1).mean().to_dict()
    data["avg_daily_volume"] = data["session"].map(historical_session_volume)
    session_fraction = ((data["session_minute"] + 1) / 390.0).clip(lower=1 / 390.0, upper=1.0)
    data["relative_volume"] = data["cum_volume"].div(data["avg_daily_volume"] * session_fraction)
    rolling_volume = data.groupby("session", sort=False)["volume"].transform(lambda series: series.shift(1).rolling(20, min_periods=3).mean())
    data["volume_surge"] = data["volume"].div(rolling_volume.replace(0, float("nan")))
    lookback = int(rules.get("breakout_lookback_bars") or 20)
    data["prior_breakout_high"] = data.groupby("session", sort=False)["high"].transform(
        lambda series: series.shift(1).rolling(lookback, min_periods=lookback).max()
    )
    opening_minutes = int(rules.get("opening_range_minutes") or 15)
    opening_only = data["high"].where(data["session_minute"] < opening_minutes)
    opening_high = opening_only.groupby(data["session"], sort=False).transform("max")
    data["opening_range_high"] = opening_high.where(data["session_minute"] >= opening_minutes)
    green = (data["close"] > data["open"]).astype(int)
    run_lengths = green.groupby([data["session"], (green == 0).cumsum()]).cumsum()
    data["green_streak"] = run_lengths
    return data


def parse_clock_minutes(value: str | None) -> int | None:
    if not value:
        return None
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(value)):
        return None
    hours, minutes = map(int, str(value).split(":"))
    return hours * 60 + minutes


def backtest_limitations(strategy: dict[str, Any]) -> list[str]:
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    limitations = list(dict.fromkeys(str(item) for item in strategy.get("unresolved_rules") or [] if str(item).strip()))
    if rules.get("catalyst_required"):
        limitations.append("Historical, point-in-time news catalysts are not included in this backtest.")
    if rules.get("max_spread_pct") is not None:
        limitations.append("Historical bid/ask quotes are unavailable; the spread limit is estimated through configured trading costs.")
    if str(strategy.get("direction", "long")).lower() not in {"long", "both"}:
        limitations.append("This release evaluates long trades only; short-only strategies cannot be backtested.")
    if rules.get("stop_loss_pct") is None:
        limitations.append("The video did not specify an exact stop; the editable default stop is a research assumption.")
    if rules.get("reward_risk") is None:
        limitations.append("The video did not specify an exact target; the editable reward/risk setting is a research assumption.")
    return list(dict.fromkeys(limitations))


def evaluate_signal(row: pd.Series, rules: dict[str, Any]) -> bool:
    def has_number(name: str) -> bool:
        return pd.notna(row.get(name))

    close = safe_float(row.get("close"))
    if close is None or close <= 0:
        return False
    comparisons = [
        ("min_price", "close", lambda actual, target: actual >= target),
        ("max_price", "close", lambda actual, target: actual <= target),
        ("min_day_change_pct", "day_change_pct", lambda actual, target: actual >= target),
        ("min_relative_volume", "relative_volume", lambda actual, target: actual >= target),
        ("min_dollar_volume", "cum_dollar_volume", lambda actual, target: actual >= target),
        ("max_vwap_distance_pct", "vwap_distance_pct", lambda actual, target: actual <= target),
        ("volume_surge_ratio", "volume_surge", lambda actual, target: actual >= target),
        ("minimum_green_bars", "green_streak", lambda actual, target: actual >= target),
    ]
    for rule_name, field_name, comparator in comparisons:
        threshold = rules.get(rule_name)
        if threshold is None:
            continue
        if not has_number(field_name) or not comparator(float(row[field_name]), float(threshold)):
            return False

    if rules.get("above_vwap") is True and (not has_number("vwap") or close <= float(row["vwap"])):
        return False
    if rules.get("above_vwap") is False and (not has_number("vwap") or close >= float(row["vwap"])):
        return False
    if rules.get("vwap_reclaim"):
        if not all(has_number(name) for name in ("previous_close", "previous_vwap", "vwap")):
            return False
        if not (float(row["previous_close"]) <= float(row["previous_vwap"]) and close > float(row["vwap"])):
            return False
    if rules.get("breakout_lookback_bars") is not None:
        if not has_number("prior_breakout_high") or close <= float(row["prior_breakout_high"]):
            return False
    if rules.get("opening_range_minutes") is not None:
        if not has_number("opening_range_high") or close <= float(row["opening_range_high"]):
            return False

    clock_minute = 9 * 60 + 30 + int(row.get("session_minute", 0))
    session_start = parse_clock_minutes(rules.get("session_start"))
    session_end = parse_clock_minutes(rules.get("session_end"))
    if session_start is not None and clock_minute < session_start:
        return False
    if session_end is not None and clock_minute > session_end:
        return False
    return True


@dataclass
class BacktestSettings:
    starting_cash: float = 10_000.0
    risk_per_trade_pct: float = 0.5
    max_position_pct: float = 20.0
    default_stop_pct: float = 2.0
    default_reward_risk: float = 2.0
    spread_bps: float = 12.0
    slippage_bps: float = 8.0
    fee_per_order: float = 0.0
    train_fraction: float = 0.7

    def validate(self) -> None:
        if self.starting_cash <= 0:
            raise AppError("Starting cash must be greater than zero.")
        if not 0 < self.risk_per_trade_pct <= 100:
            raise AppError("Risk per trade must be greater than zero and no more than 100%.")
        if not 0 < self.max_position_pct <= 100:
            raise AppError("Maximum position size must be greater than zero and no more than 100%.")
        if not 0 < self.default_stop_pct < 100 or self.default_reward_risk <= 0:
            raise AppError("The default stop and reward/risk settings must be positive.")
        if min(self.spread_bps, self.slippage_bps, self.fee_per_order) < 0:
            raise AppError("Spread, slippage, and fees cannot be negative.")
        if not 0 < self.train_fraction < 1:
            raise AppError("The in-sample fraction must be between zero and one.")


@dataclass
class OptimizationSettings:
    max_variants_per_strategy: int = 36
    finalists_per_strategy: int = 6
    minimum_training_trades: int = 5
    minimum_validation_trades: int = 2
    training_fraction: float = 0.60
    validation_fraction: float = 0.20
    stress_cost_multiplier: float = 1.5

    def validate(self) -> None:
        if not 1 <= self.max_variants_per_strategy <= 120:
            raise AppError("Test between 1 and 120 settings combinations per strategy.")
        if not 1 <= self.finalists_per_strategy <= min(20, self.max_variants_per_strategy):
            raise AppError("The number of validation finalists must be between 1 and the combination limit.")
        if self.minimum_training_trades < 1 or self.minimum_validation_trades < 1:
            raise AppError("Minimum trade counts must be at least one.")
        if not 0.30 <= self.training_fraction <= 0.80:
            raise AppError("The training period must contain between 30% and 80% of the available sessions.")
        if not 0.10 <= self.validation_fraction <= 0.40:
            raise AppError("The validation period must contain between 10% and 40% of the available sessions.")
        if self.training_fraction + self.validation_fraction > 0.90:
            raise AppError("Reserve at least 10% of the sessions for a final untouched holdout test.")
        if not 1.0 <= self.stress_cost_multiplier <= 5.0:
            raise AppError("The higher-cost stress test must use a multiplier between 1 and 5.")


def _empty_backtest(settings: BacktestSettings, strategy: dict[str, Any], symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "strategy_id": strategy.get("id"),
        "strategy_name": strategy.get("name", "Unnamed strategy"),
        "settings": asdict(settings),
        "limitations": backtest_limitations(strategy),
        "trades": [],
        "equity_curve": [{"timestamp": None, "equity": settings.starting_cash}],
        "metrics": summarize_trades([], settings.starting_cash),
        "in_sample": summarize_trades([], settings.starting_cash),
        "out_of_sample": summarize_trades([], settings.starting_cash),
        "sessions": 0,
    }


def summarize_trades(trades: list[dict[str, Any]], starting_cash: float) -> dict[str, Any]:
    if not trades:
        return {
            "trade_count": 0,
            "win_rate_pct": 0.0,
            "net_pnl": 0.0,
            "return_pct": 0.0,
            "average_trade": 0.0,
            "profit_factor": None,
            "max_drawdown_pct": 0.0,
            "average_winner": 0.0,
            "average_loser": 0.0,
        }
    pnl = [safe_float(item.get("pnl"), 0.0) or 0.0 for item in trades]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    equity = starting_cash
    peak = starting_cash
    max_drawdown = 0.0
    for value in pnl:
        equity += value
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100.0)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    return {
        "trade_count": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100.0, 2),
        "net_pnl": round(sum(pnl), 2),
        "return_pct": round(sum(pnl) / starting_cash * 100.0, 2) if starting_cash else 0.0,
        "average_trade": round(sum(pnl) / len(trades), 2),
        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "max_drawdown_pct": round(max_drawdown, 2),
        "average_winner": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "average_loser": round(sum(losses) / len(losses), 2) if losses else 0.0,
    }


def run_backtest(
    rows: list[dict[str, Any]],
    strategy: dict[str, Any],
    symbol: str,
    settings: BacktestSettings | None = None,
    *,
    prepared_indicators: pd.DataFrame | None = None,
) -> dict[str, Any]:
    settings = settings or BacktestSettings()
    settings.validate()
    if str(strategy.get("direction", "long")).lower() not in {"long", "both"}:
        raise AppError("Short-only and unclear-direction strategies cannot be backtested in this long-only release.")
    result = _empty_backtest(settings, strategy, symbol)
    if prepared_indicators is not None:
        if len(prepared_indicators) < 3:
            return result
        data = prepared_indicators
    else:
        base = bars_to_frame(rows)
        if len(base) < 3:
            return result
        data = add_indicators(base, strategy)
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    stop_pct = rules.get("stop_loss_pct") or settings.default_stop_pct
    reward_risk = rules.get("reward_risk") or settings.default_reward_risk
    max_hold = rules.get("max_hold_minutes")
    sessions = list(dict.fromkeys(data["session"].tolist()))
    split_index = max(1, min(len(sessions) - 1, int(len(sessions) * settings.train_fraction))) if len(sessions) > 1 else 1
    holdout_sessions = set(sessions[split_index:]) if len(sessions) > 1 else set()
    cash = settings.starting_cash
    position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = [{"timestamp": str(data.iloc[0]["timestamp"]), "equity": round(cash, 2)}]
    execution_friction = (settings.spread_bps / 2.0 + settings.slippage_bps) / 10_000.0
    records = data.to_dict("records")

    for index in range(1, len(records)):
        current = records[index]
        previous = records[index - 1]
        if position is not None:
            reason: str | None = None
            raw_exit: float | None = None
            if current["session"] != position["session"]:
                raw_exit = float(previous["close"])
                exit_time = previous["timestamp"]
                reason = "End of session"
            else:
                exit_time = current["timestamp"]
                low = float(current["low"])
                high = float(current["high"])
                bar_open = float(current["open"])
                # Stops win ambiguous same-bar touches. Adverse opening gaps fill at the gap.
                if low <= position["stop_price"]:
                    raw_exit = min(bar_open, position["stop_price"])
                    reason = "Stop loss"
                elif high >= position["target_price"]:
                    raw_exit = max(bar_open, position["target_price"])
                    reason = "Profit target"
                elif max_hold is not None:
                    held_minutes = (current["timestamp"] - position["entry_time"]).total_seconds() / 60.0
                    if held_minutes >= max_hold:
                        raw_exit = float(current["close"])
                        reason = "Time limit"
                elif index == len(records) - 1:
                    raw_exit = float(current["close"])
                    reason = "End of available data"

            if reason and raw_exit is not None:
                fill_exit = raw_exit * (1.0 - execution_friction)
                gross = (fill_exit - position["entry_price"]) * position["quantity"]
                pnl = gross - settings.fee_per_order * 2.0
                cash += pnl
                trade = {
                    "symbol": symbol,
                    "entry_time": isoformat_utc(position["entry_time"].to_pydatetime()),
                    "exit_time": isoformat_utc(exit_time.to_pydatetime()),
                    "entry_price": round(position["entry_price"], 4),
                    "exit_price": round(fill_exit, 4),
                    "stop_price": round(position["stop_price"], 4),
                    "target_price": round(position["target_price"], 4),
                    "quantity": position["quantity"],
                    "pnl": round(pnl, 2),
                    "return_pct": round((fill_exit / position["entry_price"] - 1.0) * 100.0, 3),
                    "reason": reason,
                    "sample": "out_of_sample" if position["session"] in holdout_sessions else "in_sample",
                }
                trades.append(trade)
                curve.append({"timestamp": trade["exit_time"], "equity": round(cash, 2)})
                position = None
                # If a previous-session position was closed, the current bar can still
                # serve as the next open for yesterday's signal only when sessions match,
                # so the normal guard below prevents an overnight entry.
                if reason != "End of session":
                    continue

        if position is not None or previous["session"] != current["session"]:
            continue
        if not evaluate_signal(previous, rules):
            continue
        entry = float(current["open"]) * (1.0 + execution_friction)
        if entry <= 0 or cash <= 0:
            continue
        stop_price = entry * (1.0 - stop_pct / 100.0)
        risk_per_share = entry - stop_price
        if risk_per_share <= 0:
            continue
        risk_budget = cash * settings.risk_per_trade_pct / 100.0
        allocation_cap = cash * settings.max_position_pct / 100.0
        quantity = int(min(risk_budget / risk_per_share, allocation_cap / entry))
        if quantity < 1:
            continue
        position = {
            "entry_time": current["timestamp"],
            "entry_price": entry,
            "quantity": quantity,
            "stop_price": stop_price,
            "target_price": entry + risk_per_share * reward_risk,
            "session": current["session"],
        }

    # Close any remaining position using the final bar; never leave an invisible trade.
    if position is not None:
        final_row = records[-1]
        raw_exit = float(final_row["close"])
        fill_exit = raw_exit * (1.0 - execution_friction)
        pnl = (fill_exit - position["entry_price"]) * position["quantity"] - settings.fee_per_order * 2.0
        cash += pnl
        trade = {
            "symbol": symbol,
            "entry_time": isoformat_utc(position["entry_time"].to_pydatetime()),
            "exit_time": isoformat_utc(final_row["timestamp"].to_pydatetime()),
            "entry_price": round(position["entry_price"], 4),
            "exit_price": round(fill_exit, 4),
            "stop_price": round(position["stop_price"], 4),
            "target_price": round(position["target_price"], 4),
            "quantity": position["quantity"],
            "pnl": round(pnl, 2),
            "return_pct": round((fill_exit / position["entry_price"] - 1.0) * 100.0, 3),
            "reason": "End of available data",
            "sample": "out_of_sample" if position["session"] in holdout_sessions else "in_sample",
        }
        trades.append(trade)
        curve.append({"timestamp": trade["exit_time"], "equity": round(cash, 2)})

    in_sample = [trade for trade in trades if trade["sample"] == "in_sample"]
    out_sample = [trade for trade in trades if trade["sample"] == "out_of_sample"]
    holdout_start_cash = settings.starting_cash + sum(float(trade["pnl"]) for trade in in_sample)
    result.update(
        {
            "trades": trades,
            "equity_curve": curve,
            "metrics": summarize_trades(trades, settings.starting_cash),
            "in_sample": summarize_trades(in_sample, settings.starting_cash),
            "out_of_sample": summarize_trades(out_sample, max(holdout_start_cash, 0.01)),
            "sessions": len(sessions),
            "holdout_start": min(holdout_sessions) if holdout_sessions else None,
        }
    )
    return result


def _optimizer_number_options(
    value: float,
    multipliers: tuple[float, ...],
    *,
    minimum: float,
    maximum: float,
    integer: bool = False,
) -> list[float | int]:
    options: list[float | int] = []
    for multiplier in multipliers:
        candidate = min(maximum, max(minimum, value * multiplier))
        rounded: float | int = int(round(candidate)) if integer else round(candidate, 4)
        if rounded not in options:
            options.append(rounded)
    return options


def _shift_strategy_clock(value: str, minutes: int, *, earliest: int, latest: int) -> str:
    parsed = parse_clock_minutes(value)
    if parsed is None:
        return value
    adjusted = min(latest, max(earliest, parsed + minutes))
    return f"{adjusted // 60:02d}:{adjusted % 60:02d}"


def generate_strategy_variants(
    strategy: dict[str, Any],
    backtest_settings: BacktestSettings | None = None,
    *,
    maximum: int = 36,
) -> list[dict[str, Any]]:
    """Create reproducible, bounded variants without inventing new entry signals."""
    settings = backtest_settings or BacktestSettings()
    settings.validate()
    limit = max(1, min(120, int(maximum)))
    original = normalize_machine_rules(strategy.get("machine_rules"))
    baseline = dict(original)
    baseline["stop_loss_pct"] = original.get("stop_loss_pct") or settings.default_stop_pct
    baseline["reward_risk"] = original.get("reward_risk") or settings.default_reward_risk
    variants: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(updates: dict[str, Any]) -> None:
        if len(variants) >= limit:
            return
        candidate = normalize_machine_rules({**baseline, **updates})
        if candidate.get("session_start") and candidate.get("session_end"):
            start = parse_clock_minutes(candidate["session_start"])
            end = parse_clock_minutes(candidate["session_end"])
            if start is not None and end is not None and start >= end:
                return
        signature = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
        if signature not in seen:
            seen.add(signature)
            variants.append(candidate)

    add({})
    stops = _optimizer_number_options(
        float(baseline["stop_loss_pct"]),
        (0.70, 0.85, 1.0, 1.20, 1.50),
        minimum=0.15,
        maximum=20.0,
    )
    rewards = _optimizer_number_options(
        float(baseline["reward_risk"]),
        (0.75, 1.0, 1.25, 1.50, 2.0),
        minimum=0.5,
        maximum=6.0,
    )
    for index in range(max(len(stops), len(rewards))):
        if index < len(stops):
            add({"stop_loss_pct": stops[index]})
        if index < len(rewards):
            add({"reward_risk": rewards[index]})

    tunable = (
        ("min_day_change_pct", (0.70, 1.25), -25.0, 80.0, False),
        ("min_relative_volume", (0.75, 1.30), 0.25, 20.0, False),
        ("min_dollar_volume", (0.70, 1.40), 1_000.0, 1_000_000_000.0, False),
        ("max_vwap_distance_pct", (0.70, 1.40), 0.10, 50.0, False),
        ("breakout_lookback_bars", (0.70, 1.40), 2.0, 100.0, True),
        ("opening_range_minutes", (0.70, 1.50), 5.0, 90.0, True),
        ("volume_surge_ratio", (0.75, 1.35), 0.25, 20.0, False),
        ("minimum_green_bars", (0.70, 1.50), 1.0, 8.0, True),
        ("max_hold_minutes", (0.70, 1.40), 5.0, 360.0, True),
    )
    threshold_adjustments: list[dict[str, Any]] = []
    for field_name, multipliers, minimum, maximum_value, integer in tunable:
        current = safe_float(original.get(field_name))
        if current is None:
            continue
        for option in _optimizer_number_options(
            current,
            multipliers,
            minimum=minimum,
            maximum=maximum_value,
            integer=integer,
        ):
            if option != current:
                update = {field_name: option}
                threshold_adjustments.append(update)
                add(update)
    for field_name in ("session_start", "session_end"):
        clock = original.get(field_name)
        if not clock:
            continue
        for offset in (-15, 15):
            adjusted = _shift_strategy_clock(str(clock), offset, earliest=9 * 60 + 30, latest=15 * 60 + 55)
            if adjusted != clock:
                update = {field_name: adjusted}
                threshold_adjustments.append(update)
                add(update)

    for stop in stops:
        for reward in rewards:
            add({"stop_loss_pct": stop, "reward_risk": reward})
            if len(variants) >= limit:
                break
        if len(variants) >= limit:
            break

    for adjustment in threshold_adjustments:
        for stop in (stops[0], stops[-1]):
            add({**adjustment, "stop_loss_pct": stop})
        for reward in (rewards[0], rewards[-1]):
            add({**adjustment, "reward_risk": reward})
        if len(variants) >= limit:
            break
    return variants


def _optimization_score(metrics: dict[str, Any], settings: BacktestSettings, minimum_trades: int) -> float:
    pnl = safe_float(metrics.get("net_pnl"), 0.0) or 0.0
    drawdown_pct = safe_float(metrics.get("max_drawdown_pct"), 0.0) or 0.0
    count = int(safe_float(metrics.get("trade_count"), 0.0) or 0.0)
    drawdown_penalty = settings.starting_cash * drawdown_pct / 100.0 * 0.65
    risk_budget = settings.starting_cash * settings.risk_per_trade_pct / 100.0
    sample_penalty = max(0, minimum_trades - count) * risk_budget * 0.75
    return round(pnl - drawdown_penalty - sample_penalty, 4)


def _trade_session(trade: dict[str, Any]) -> str:
    value = str(trade.get("entry_time") or "")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return timestamp.astimezone(ET).date().isoformat()


def _period_metrics(
    result: dict[str, Any],
    selected_sessions: set[str],
    starting_cash: float,
) -> dict[str, Any]:
    trades = result.get("trades") or []
    period_trades = [trade for trade in trades if _trade_session(trade) in selected_sessions]
    first_session = min(selected_sessions) if selected_sessions else ""
    earlier_pnl = sum(
        safe_float(trade.get("pnl"), 0.0) or 0.0
        for trade in trades
        if _trade_session(trade) and _trade_session(trade) < first_session
    )
    return summarize_trades(period_trades, max(starting_cash + earlier_pnl, 0.01))


def optimize_stock_strategies(
    rows: list[dict[str, Any]],
    strategies: list[dict[str, Any]],
    symbol: str,
    backtest_settings: BacktestSettings | None = None,
    optimization_settings: OptimizationSettings | None = None,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Tune on training data, select on validation, and inspect holdout only once."""
    settings = backtest_settings or BacktestSettings()
    settings.validate()
    optimizer = optimization_settings or OptimizationSettings()
    optimizer.validate()
    tickers = parse_symbols(symbol)
    if len(tickers) != 1:
        raise AppError("Enter exactly one valid stock ticker to optimize.")
    target_symbol = tickers[0]
    frame = bars_to_frame(rows)
    sessions = list(dict.fromkeys(frame.get("session", pd.Series(dtype=str)).tolist()))
    if len(sessions) < 8:
        raise AppError(
            "Optimization needs at least eight separate trading sessions so training, validation, "
            "and final holdout periods stay separate. Request more historical data."
        )

    training_end = max(3, min(len(sessions) - 3, int(len(sessions) * optimizer.training_fraction)))
    validation_end = max(
        training_end + 1,
        min(len(sessions) - 1, int(len(sessions) * (optimizer.training_fraction + optimizer.validation_fraction))),
    )
    training_sessions = sessions[:training_end]
    validation_sessions = sessions[training_end:validation_end]
    holdout_sessions = sessions[validation_end:]
    frames = {
        "training": frame[frame["session"].isin(training_sessions)].copy().reset_index(drop=True),
        "validation": frame[frame["session"].isin(training_sessions + validation_sessions)].copy().reset_index(drop=True),
        "full": frame.copy().reset_index(drop=True),
    }
    warnings: list[str] = []
    eligible: list[dict[str, Any]] = []
    for strategy in strategies:
        if not isinstance(strategy, dict) or not strategy.get("id"):
            continue
        if str(strategy.get("direction", "long")).lower() not in {"long", "both"}:
            warnings.append(f'{strategy.get("name", "Unnamed strategy")}: skipped because short-only strategies are not supported.')
            continue
        target = str(strategy.get("optimized_for_symbol") or "").strip().upper()
        if target and target != target_symbol:
            warnings.append(f'{strategy.get("name", "Unnamed strategy")}: skipped because it is locked to {target}.')
            continue
        eligible.append(strategy)
    if not eligible:
        raise AppError("No saved long strategies are available for this stock. Add or review a strategy first.")

    search_plan = [
        (strategy, generate_strategy_variants(strategy, settings, maximum=optimizer.max_variants_per_strategy))
        for strategy in eligible
    ]
    total_steps = sum(len(variants) + min(len(variants), optimizer.finalists_per_strategy) + 1 for _, variants in search_plan) + 1
    completed_steps = 0
    indicator_cache: dict[tuple[str, int, int], pd.DataFrame] = {}

    def evaluate(candidate_strategy: dict[str, Any], period: str, chosen_settings: BacktestSettings) -> dict[str, Any]:
        rules = normalize_machine_rules(candidate_strategy.get("machine_rules"))
        key = (
            period,
            int(rules.get("breakout_lookback_bars") or 20),
            int(rules.get("opening_range_minutes") or 15),
        )
        if key not in indicator_cache:
            indicator_cache[key] = add_indicators(frames[period], candidate_strategy)
        return run_backtest([], candidate_strategy, target_symbol, chosen_settings, prepared_indicators=indicator_cache[key])

    def notify(message: str) -> None:
        nonlocal completed_steps
        completed_steps += 1
        if progress:
            progress(min(completed_steps, total_steps), total_steps, message)

    ranked: list[dict[str, Any]] = []
    for source_strategy, variants in search_plan:
        name = str(source_strategy.get("name") or "Unnamed strategy")
        original = normalize_machine_rules(source_strategy.get("machine_rules"))
        trained: list[dict[str, Any]] = []
        for index, candidate_rules in enumerate(variants):
            candidate_strategy = {**source_strategy, "machine_rules": candidate_rules}
            result = evaluate(candidate_strategy, "training", settings)
            metrics = result["metrics"]
            trained.append(
                {
                    "variant_index": index,
                    "rules": candidate_rules,
                    "training_metrics": metrics,
                    "training_score": _optimization_score(metrics, settings, optimizer.minimum_training_trades),
                }
            )
            notify(f"Testing {name}: settings {index + 1} of {len(variants)}")
        ordered = sorted(trained, key=lambda item: (item["training_score"], -item["variant_index"]), reverse=True)
        finalist_count = min(len(ordered), optimizer.finalists_per_strategy)
        finalists = ordered[:finalist_count]
        baseline = trained[0]
        if all(item["variant_index"] != 0 for item in finalists):
            finalists[-1] = baseline

        validated: list[dict[str, Any]] = []
        for candidate in finalists:
            candidate_strategy = {**source_strategy, "machine_rules": candidate["rules"]}
            result = evaluate(candidate_strategy, "validation", settings)
            validation_metrics = _period_metrics(result, set(validation_sessions), settings.starting_cash)
            validation_score = (
                _optimization_score(validation_metrics, settings, optimizer.minimum_validation_trades)
                + candidate["training_score"] * 0.10
            )
            validated.append({**candidate, "validation_metrics": validation_metrics, "validation_score": validation_score})
            notify(f"Checking unseen validation sessions for {name}")
        best = max(validated, key=lambda item: (item["validation_score"], -item["variant_index"]))
        stressed_settings = replace(
            settings,
            spread_bps=settings.spread_bps * optimizer.stress_cost_multiplier,
            slippage_bps=settings.slippage_bps * optimizer.stress_cost_multiplier,
        )
        stressed = evaluate({**source_strategy, "machine_rules": best["rules"]}, "validation", stressed_settings)
        stress_metrics = _period_metrics(stressed, set(validation_sessions), stressed_settings.starting_cash)
        notify(f"Stress-testing {name} with higher trading costs")

        training_metrics = best["training_metrics"]
        validation_metrics = best["validation_metrics"]
        adequate_sample = (
            training_metrics["trade_count"] >= optimizer.minimum_training_trades
            and validation_metrics["trade_count"] >= optimizer.minimum_validation_trades
        )
        if not adequate_sample:
            status = "LIMITED DATA"
        elif validation_metrics["net_pnl"] <= 0:
            status = "NO VALIDATED EDGE"
        elif training_metrics["net_pnl"] <= 0:
            status = "UNSTABLE"
        elif stress_metrics["net_pnl"] <= 0:
            status = "COST SENSITIVE"
        else:
            status = "VALIDATED"
        changed_rules = {
            key: {"original": original.get(key), "optimized": value}
            for key, value in best["rules"].items()
            if value != original.get(key)
        }
        ranked.append(
            {
                "source_strategy_id": source_strategy["id"],
                "strategy_name": name,
                "symbol": target_symbol,
                "optimized_rules": best["rules"],
                "changed_rules": changed_rules,
                "variants_tested": len(variants),
                "finalists_tested": len(validated),
                "training_metrics": training_metrics,
                "validation_metrics": validation_metrics,
                "stress_metrics": stress_metrics,
                "score": round(best["validation_score"], 2),
                "status": status,
                "adequate_sample": adequate_sample,
                "baseline_training_metrics": baseline["training_metrics"],
                "limitations": backtest_limitations(source_strategy),
            }
        )

    ranked.sort(
        key=lambda item: (
            item["status"] == "VALIDATED",
            item["adequate_sample"],
            item["validation_metrics"]["net_pnl"] > 0,
            item["score"],
        ),
        reverse=True,
    )
    winner = ranked[0]
    source = next(item for item in eligible if item.get("id") == winner["source_strategy_id"])
    winner_backtest = evaluate({**source, "machine_rules": winner["optimized_rules"]}, "full", settings)
    winner["holdout_metrics"] = _period_metrics(winner_backtest, set(holdout_sessions), settings.starting_cash)
    winner["full_metrics"] = winner_backtest["metrics"]
    notify("Running the final untouched holdout test on the selected winner")

    if not winner["adequate_sample"]:
        warnings.append("The highest-ranked setup has too few trades to support a reliable conclusion.")
    if winner["holdout_metrics"]["trade_count"] < optimizer.minimum_validation_trades:
        warnings.append("The final untouched holdout contains too few trades to confirm this strategy.")
    if winner["validation_metrics"]["net_pnl"] <= 0:
        warnings.append("No tested strategy earned a positive simulated result in the separate validation period.")
    if winner["holdout_metrics"]["trade_count"] and winner["holdout_metrics"]["net_pnl"] <= 0:
        warnings.append("The selected setup did not stay profitable in its untouched final holdout period.")
    if winner["stress_metrics"]["net_pnl"] <= 0 and winner["validation_metrics"]["net_pnl"] > 0:
        warnings.append("The selected setup becomes unprofitable when estimated spread and slippage increase.")

    return {
        "symbol": target_symbol,
        "generated_at": isoformat_utc(utc_now()),
        "session_count": len(sessions),
        "strategies_tested": len(eligible),
        "variants_tested": sum(len(variants) for _, variants in search_plan),
        "training_sessions": training_sessions,
        "validation_sessions": validation_sessions,
        "holdout_sessions": holdout_sessions,
        "backtest_settings": asdict(settings),
        "optimization_settings": asdict(optimizer),
        "rankings": ranked,
        "winner": winner,
        "winning_backtest": winner_backtest,
        "warnings": list(dict.fromkeys(warnings)),
    }


def session_progress(now: datetime | None = None) -> float:
    local = (now or utc_now()).astimezone(ET)
    if local.weekday() >= 5:
        return 1.0
    minutes = local.hour * 60 + local.minute - (9 * 60 + 30)
    return max(1 / 390.0, min(1.0, minutes / 390.0))


def snapshot_metrics(
    symbol: str,
    snapshot: dict[str, Any],
    *,
    average_daily_volume: float | None = None,
    news_items: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    trade = snapshot.get("latestTrade") or snapshot.get("latest_trade") or {}
    quote_data = snapshot.get("latestQuote") or snapshot.get("latest_quote") or {}
    daily = snapshot.get("dailyBar") or snapshot.get("daily_bar") or {}
    previous = snapshot.get("prevDailyBar") or snapshot.get("prev_daily_bar") or {}
    price = safe_float(trade.get("p")) or safe_float(daily.get("c"))
    if price is None or price <= 0:
        return None
    previous_close = safe_float(previous.get("c"))
    vwap = safe_float(daily.get("vw"))
    volume = safe_float(daily.get("v"), 0.0) or 0.0
    bid = safe_float(quote_data.get("bp"))
    ask = safe_float(quote_data.get("ap"))
    spread_pct = None
    if bid and ask and ask >= bid:
        midpoint = (bid + ask) / 2.0
        spread_pct = (ask - bid) / midpoint * 100.0 if midpoint else None
    progress = session_progress(now)
    relative_volume = volume / (average_daily_volume * progress) if average_daily_volume and average_daily_volume > 0 else None
    day_change = (price / previous_close - 1.0) * 100.0 if previous_close else None
    distance = (price / vwap - 1.0) * 100.0 if vwap else None
    high = safe_float(daily.get("h"))
    return {
        "symbol": symbol,
        "price": round(price, 4),
        "day_change_pct": round(day_change, 2) if day_change is not None else None,
        "volume": int(volume),
        "dollar_volume": round(price * volume, 2),
        "relative_volume": round(relative_volume, 2) if relative_volume is not None else None,
        "vwap": round(vwap, 4) if vwap else None,
        "vwap_distance_pct": round(distance, 2) if distance is not None else None,
        "above_vwap": bool(vwap and price > vwap),
        "spread_pct": round(spread_pct, 3) if spread_pct is not None else None,
        "high": high,
        "distance_from_high_pct": round((high - price) / high * 100.0, 2) if high else None,
        "news": news_items or [],
        "has_catalyst": bool(news_items),
        "quote_timestamp": quote_data.get("t"),
        "trade_timestamp": trade.get("t"),
    }


def match_strategy(metrics: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    rules = normalize_machine_rules(strategy.get("machine_rules"))
    checks: list[dict[str, Any]] = []

    def check(label: str, actual: Any, required: Any, compare: Callable[[float, float], bool]) -> None:
        if required is None:
            return
        actual_float = safe_float(actual)
        if actual_float is None:
            status = "unknown"
        else:
            status = "pass" if compare(actual_float, float(required)) else "fail"
        checks.append({"label": label, "actual": actual, "required": required, "status": status})

    check("Minimum price", metrics.get("price"), rules.get("min_price"), lambda actual, expected: actual >= expected)
    check("Maximum price", metrics.get("price"), rules.get("max_price"), lambda actual, expected: actual <= expected)
    check("Day change %", metrics.get("day_change_pct"), rules.get("min_day_change_pct"), lambda actual, expected: actual >= expected)
    check("Relative volume", metrics.get("relative_volume"), rules.get("min_relative_volume"), lambda actual, expected: actual >= expected)
    check("Dollar volume", metrics.get("dollar_volume"), rules.get("min_dollar_volume"), lambda actual, expected: actual >= expected)
    check("Maximum spread %", metrics.get("spread_pct"), rules.get("max_spread_pct"), lambda actual, expected: actual <= expected)
    check("Maximum VWAP extension %", metrics.get("vwap_distance_pct"), rules.get("max_vwap_distance_pct"), lambda actual, expected: actual <= expected)
    if rules.get("above_vwap") is not None:
        required = bool(rules["above_vwap"])
        actual = bool(metrics.get("above_vwap")) if metrics.get("vwap") is not None else None
        status = "unknown" if actual is None else ("pass" if actual is required else "fail")
        checks.append({"label": "Price above VWAP", "actual": actual, "required": required, "status": status})
    if rules.get("catalyst_required"):
        checks.append({"label": "Recent news catalyst", "actual": bool(metrics.get("has_catalyst")), "required": True, "status": "pass" if metrics.get("has_catalyst") else "fail"})

    # These triggers require actual recent bars; do not pretend a snapshot proves them.
    chart_checks = metrics.get("chart_checks") if isinstance(metrics.get("chart_checks"), dict) else {}
    for field_name, label in (
        ("vwap_reclaim", "Confirmed VWAP reclaim"),
        ("breakout_lookback_bars", "Confirmed resistance breakout"),
        ("opening_range_minutes", "Confirmed opening-range breakout"),
        ("volume_surge_ratio", "Current candle volume surge"),
        ("minimum_green_bars", "Consecutive green candles"),
    ):
        if rules.get(field_name):
            observed = chart_checks.get(field_name)
            if isinstance(observed, bool):
                checks.append(
                    {
                        "label": label,
                        "actual": "Confirmed from recent candles" if observed else "Not present in recent candles",
                        "required": rules[field_name],
                        "status": "pass" if observed else "fail",
                    }
                )
            else:
                checks.append({"label": label, "actual": "Current candles unavailable", "required": rules[field_name], "status": "unknown"})

    passed = sum(item["status"] == "pass" for item in checks)
    failed = sum(item["status"] == "fail" for item in checks)
    unknown = sum(item["status"] == "unknown" for item in checks)
    denominator = len(checks) or 1
    score = round((passed + unknown * 0.20) / denominator * 100.0, 1)
    if failed == 0 and unknown == 0 and checks:
        status = "MATCH"
    elif failed == 0 and passed > 0:
        status = "VERIFY"
    elif failed <= 1 and passed >= 2:
        status = "WATCH"
    else:
        status = "NO MATCH"
    price = safe_float(metrics.get("price")) or 0.0
    stop_pct = rules.get("stop_loss_pct")
    ratio = rules.get("reward_risk")
    stop = price * (1.0 - stop_pct / 100.0) if price and stop_pct else None
    target = price + (price - stop) * ratio if price and stop is not None and ratio else None
    return {
        "strategy_id": strategy.get("id"),
        "strategy_name": strategy.get("name", "Unnamed strategy"),
        "status": status,
        "score": score,
        "passed": passed,
        "failed": failed,
        "unknown": unknown,
        "checks": checks,
        "suggested_stop": round(stop, 4) if stop else None,
        "suggested_target": round(target, 4) if target else None,
        "reward_risk": ratio,
    }


def chart_trigger_checks(rows: list[dict[str, Any]], strategy: dict[str, Any]) -> dict[str, bool | None]:
    """Confirm chart-derived rules only from candles available at scan time."""
    fields = (
        "vwap_reclaim", "breakout_lookback_bars", "opening_range_minutes",
        "volume_surge_ratio", "minimum_green_bars",
    )
    outcome: dict[str, bool | None] = {name: None for name in fields}
    frame = bars_to_frame(rows)
    if frame.empty:
        return outcome
    enriched = add_indicators(frame, strategy)
    last = enriched.iloc[-1]
    rules = normalize_machine_rules(strategy.get("machine_rules"))

    if rules.get("vwap_reclaim"):
        recent = enriched.tail(3)
        eligible = recent.dropna(subset=["previous_close", "previous_vwap", "vwap"])
        if not eligible.empty:
            crosses = (eligible["previous_close"] <= eligible["previous_vwap"]) & (eligible["close"] > eligible["vwap"])
            outcome["vwap_reclaim"] = bool(crosses.any())

    if rules.get("breakout_lookback_bars") and pd.notna(last.get("prior_breakout_high")):
        outcome["breakout_lookback_bars"] = bool(float(last["close"]) > float(last["prior_breakout_high"]))
    if rules.get("opening_range_minutes") and pd.notna(last.get("opening_range_high")):
        outcome["opening_range_minutes"] = bool(float(last["close"]) > float(last["opening_range_high"]))
    if rules.get("volume_surge_ratio") and pd.notna(last.get("volume_surge")):
        outcome["volume_surge_ratio"] = bool(float(last["volume_surge"]) >= float(rules["volume_surge_ratio"]))
    if rules.get("minimum_green_bars") and pd.notna(last.get("green_streak")):
        outcome["minimum_green_bars"] = bool(float(last["green_streak"]) >= float(rules["minimum_green_bars"]))
    return outcome


def average_completed_daily_volume(rows: list[dict[str, Any]], today_et: date | None = None) -> float | None:
    today = today_et or utc_now().astimezone(ET).date()
    volumes: list[float] = []
    for row in rows:
        raw_timestamp = row.get("t")
        try:
            timestamp = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
            local_day = timestamp.astimezone(ET).date()
        except (TypeError, ValueError):
            continue
        volume = safe_float(row.get("v"))
        if local_day < today and volume and volume > 0:
            volumes.append(volume)
    recent = volumes[-20:]
    return sum(recent) / len(recent) if recent else None


def demo_strategy() -> dict[str, Any]:
    return {
        "id": "demo_vwap_momentum",
        "name": "Example VWAP momentum setup",
        "category": "Momentum / VWAP",
        "direction": "long",
        "summary": "Editable demonstration rules. These are examples, not claims extracted from a YouTube video.",
        "indicators": ["VWAP", "relative volume", "dollar volume"],
        "entry_conditions": ["Price above VWAP", "Relative volume at least 2x", "Day change at least 3%"],
        "exit_conditions": ["2% example stop", "2:1 example reward/risk"],
        "risk_rules": ["Paper-test before considering any live trade."],
        "avoid_conditions": ["Spread wider than 0.8%", "More than 6% above VWAP"],
        "unresolved_rules": [],
        "confidence": 100,
        "machine_rules": normalize_machine_rules(
            {
                "min_price": 1.0,
                "max_price": 30.0,
                "min_day_change_pct": 3.0,
                "min_relative_volume": 2.0,
                "min_dollar_volume": 500_000,
                "max_spread_pct": 0.8,
                "above_vwap": True,
                "max_vwap_distance_pct": 6.0,
                "stop_loss_pct": 2.0,
                "reward_risk": 2.0,
            }
        ),
        "evidence": [],
        "source_url": "",
        "source_title": "Built-in demonstration — not a real video",
        "creator": "Example only",
        "approved": False,
        "analyzed_at": isoformat_utc(utc_now()),
        "source_warnings": ["This sample is for testing the interface; it has no verified trading edge."],
    }
