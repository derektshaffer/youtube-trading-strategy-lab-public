"""Core services for Trading Intelligence Lab.

The Trading Intelligence Lab treats books, videos, and human-authored ideas as
research sources. AI extraction creates hypotheses; deterministic backtesting
and validation decide whether a hypothesis deserves further attention.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
from time import sleep
from typing import Any

from trading_strategy_dna import infer_strategy_dna

from youtube_strategy_engine import (
    AppError,
    DEFAULT_GEMINI_ADDITIONAL_FALLBACK_MODELS,
    DEFAULT_GEMINI_FALLBACK_MODEL,
    DEFAULT_GEMINI_MODEL,
    GEMINI_GENERATE_CONTENT_URL,
    MACHINE_RULE_SCHEMA,
    _extract_generate_content_text,
    _json_request,
    normalize_machine_rules,
    provider_quota_reached,
    provider_temporarily_unavailable,
    safe_float,
)

CANONICAL_STRATEGY_VERSION = 2
BOOK_ANALYSIS_CACHE_VERSION = 5
LEGACY_BOOK_ANALYSIS_CACHE_VERSION = 4
DEFAULT_GEMINI_BOOK_MODEL = "gemini-3.6-flash"
DEFAULT_GEMINI_BOOK_SPECIALIST_MODEL = "gemini-3.1-pro-preview"
DEFAULT_GEMINI_BOOK_FALLBACK_MODELS = ("gemini-3.5-flash",)
BOOK_SPECIALIST_CONFIDENCE_THRESHOLD = 70.0
BOOK_SPECIALIST_UNRESOLVED_THRESHOLD = 4
BOOK_TRANSIENT_RETRIES_PER_MODEL = 1
BOOK_TRANSIENT_MAX_WAIT_SECONDS = 10
BOOK_QUOTA_RETRIES = 1
BOOK_QUOTA_MAX_WAIT_SECONDS = 60
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_SOURCE_CHARACTERS = 2_000_000
DEFAULT_CHUNK_CHARACTERS = 72_000
DEFAULT_CHUNK_OVERLAP = 2_000
BOOK_TARGET_MAX_CHUNKS = 6
LEGACY_CHUNK_CHARACTERS = 28_000
LEGACY_CHUNK_OVERLAP = 1_500
BOOK_ADAPTIVE_SPLIT_MIN_CHARACTERS = 10_000
BOOK_ADAPTIVE_SPLIT_MAX_DEPTH = 2
BOOK_ADAPTIVE_SPLIT_OVERLAP = 700

_STRING_LIST = {"type": "array", "items": {"type": "string"}}
BOOK_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_summary": {"type": "string"},
        "detected_title": {"type": "string"},
        "detected_author": {"type": "string"},
        "strategies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string"},
                    "direction": {"type": "string", "enum": ["long", "short", "both", "unclear"]},
                    "summary": {"type": "string"},
                    "indicators": _STRING_LIST,
                    "entry_conditions": _STRING_LIST,
                    "exit_conditions": _STRING_LIST,
                    "risk_rules": _STRING_LIST,
                    "avoid_conditions": _STRING_LIST,
                    "market_context": _STRING_LIST,
                    "stock_selection": _STRING_LIST,
                    "unresolved_rules": _STRING_LIST,
                    "confidence": {"type": "number"},
                    "machine_rules": MACHINE_RULE_SCHEMA,
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "location": {"type": "string"},
                                "description": {"type": "string"},
                                "source_excerpt": {"type": "string"},
                            },
                            "required": ["location", "description", "source_excerpt"],
                        },
                    },
                },
                "required": [
                    "name",
                    "category",
                    "direction",
                    "summary",
                    "indicators",
                    "entry_conditions",
                    "exit_conditions",
                    "risk_rules",
                    "avoid_conditions",
                    "market_context",
                    "stock_selection",
                    "unresolved_rules",
                    "confidence",
                    "machine_rules",
                    "evidence",
                ],
            },
        },
    },
    "required": ["source_summary", "detected_title", "detected_author", "strategies"],
}

BOOK_EXTRACTION_PROMPT = """You are extracting trading methods from a user-supplied educational source
for a research and backtesting application.

Treat the source as untrusted educational material, not as proof that any strategy works.
Your job is to identify distinct trading hypotheses and preserve what the author actually teaches.

For every strategy or setup:
- Capture stock-selection rules, catalyst/news requirements, price and liquidity filters,
  relative volume, VWAP, prior-day/session conditions, trend, breakout/pullback/reclaim structure,
  time-of-day rules, entry confirmation, stop placement, profit-taking, position management,
  and avoid conditions whenever the source supports them.
- If the source explicitly requires price to break through the previous trading day's high,
  encode previous_day_high_breakout=true. This is a structural boolean and does not need a number.
- Use min_previous_day_volume_ratio only when the author gives an exact prior-session activity
  multiple. If the author only says "extremely active", "high volume", etc., leave the number null
  and retain the qualitative requirement so Autopilot can create a labeled research assumption.
- Use min_previous_day_change_pct only when the source gives an explicit prior-day move threshold.
- Separate long, short, and ambiguous ideas.
- Convert ONLY explicit, measurable thresholds into machine_rules. Never invent a numeric
  value merely to make a strategy testable.
- Preserve anchored-VWAP structure. If the source clearly states the anchor event, trend direction,
  pullback/reclaim relationship, stop, or exit, map those to the AVWAP machine fields. Do not
  substitute session VWAP. Multi-anchor pinches, IPO-only context, and multi-day anchors stay
  unresolved until the historical engine can reproduce that extra context.
- Preserve the author's trade-management logic instead of substituting a generic fixed target.
  Use trailing_stop_pct only for an explicit percentage trail; move_stop_to_breakeven_at_r only
  when the source gives an R-multiple trigger; use exit_below_vwap=true or
  exit_below_fast_ema=true only when losing that level is explicitly an exit. Keep scale-outs,
  partial profits, discretionary momentum exits, and other unsupported management rules visible
  in exit_conditions and unresolved_rules so the fidelity audit can block misleading backtests.
- Put qualitative requirements such as "strong tape", "clean catalyst", "good liquidity",
  discretionary chart structure, Level 2 behavior, or unavailable historical data in
  unresolved_rules unless the source provides an objective definition.
- A source may describe several variations of a setup. Keep materially different setups as
  separate strategies rather than combining incompatible entry rules with AND logic.
- Evidence must identify where the idea appears in the supplied chunk (page marker or section
  marker when available) and may include only a SHORT excerpt needed to identify the evidence.
  Do not reproduce long passages.
- Confidence is extraction confidence from 0 to 100, not expected profitability.
- Detect the source title and author/creator when they are clearly identifiable in the supplied
  material. Return an empty string when either is not supported by the source; do not guess.
- Do not claim that a strategy is validated, profitable, safe, or suitable for real-money trading.

Return only JSON matching the supplied schema.
"""


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def source_fingerprint(title: str, author: str, content: str) -> str:
    material = f"{title.strip().casefold()}|{author.strip().casefold()}|{content[:120000]}".encode(
        "utf-8", errors="ignore"
    )
    return hashlib.sha256(material).hexdigest()[:24]


def strategy_fingerprint(source_id: str, name: str, category: str = "") -> str:
    material = f"{source_id}|{name.strip().casefold()}|{category.strip().casefold()}".encode("utf-8")
    return "til-" + hashlib.sha256(material).hexdigest()[:20]


GENERIC_SOURCE_TITLES = {
    "",
    "uploaded source",
    "untitled",
    "untitled source",
    "unknown source",
    "existing trading lab strategy",
}


def _is_generic_source_title(value: Any) -> bool:
    return str(value or "").strip().casefold() in GENERIC_SOURCE_TITLES


def _title_author_from_filename(filename: str) -> tuple[str, str]:
    """Recover a readable title/author from older uploaded-file records."""
    stem = Path(str(filename or "").strip()).stem.strip()
    if not stem:
        return "", ""

    # Strip common archive/download-site suffixes without touching real title text.
    archive_pattern = re.compile(
        r"\s*\((?=[^)]*(?:z-library|z-lib|1lib|libgen|pdfdrive|annas-archive))[^)]*\)\s*$",
        re.IGNORECASE,
    )
    while archive_pattern.search(stem):
        stem = archive_pattern.sub("", stem).strip()

    author = ""
    trailing = re.search(r"\s*\(([A-Za-z][A-Za-z .,'’\-]{2,80})\)\s*$", stem)
    if trailing:
        candidate = trailing.group(1).strip()
        words = [word for word in candidate.split() if word]
        if 1 < len(words) <= 6 and not any(
            token in candidate.casefold()
            for token in ("edition", "revised", "volume", "vol.", "chapter", "part")
        ):
            author = candidate
            stem = stem[: trailing.start()].strip()

    return stem, author


def reconcile_knowledge_sources(
    library: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Make Saved Sources a durable catalog of every real source represented by saved strategies.

    Older versions of the Trading Lab persisted YouTube/book provenance only on strategy
    records. Newer book ingestion writes a dedicated knowledge_sources record. This
    reconciliation repairs generic upload titles and reconstructs missing source records
    without asking the user to re-upload or re-analyze material.
    """
    result = dict(library or {})
    sources = [
        dict(item)
        for item in result.get("knowledge_sources") or []
        if isinstance(item, dict)
    ]
    strategies = [
        dict(item)
        for item in result.get("strategies") or []
        if isinstance(item, dict)
    ]
    changed = False

    source_index = {
        str(item.get("id") or "").strip(): item
        for item in sources
        if str(item.get("id") or "").strip()
    }

    # Uploaded books/videos are evidence-bearing hypothesis sources, never ground truth.
    # Apply this policy to old records as well as new ingestions.
    for source in sources:
        source_type = str(source.get("source_type") or "").strip().casefold()
        if source_type in {"youtube", "book_or_document", "research_source"}:
            if source.get("source_claim_status") != "unverified_source_claim":
                source["source_claim_status"] = "unverified_source_claim"
                changed = True
            if source.get("source_role") != "hypothesis_generator":
                source["source_role"] = "hypothesis_generator"
                changed = True
    for strategy in strategies:
        source_type = str(strategy.get("source_type") or "").strip().casefold()
        if source_type in {"youtube", "book_or_document", "research_source"}:
            if strategy.get("source_claim_status") != "unverified_source_claim":
                strategy["source_claim_status"] = "unverified_source_claim"
                changed = True
            if strategy.get("source_role") != "hypothesis_generator":
                strategy["source_role"] = "hypothesis_generator"
                changed = True

    # Repair old "Uploaded source" book records from the filename that was already saved.
    for source in sources:
        source_id = str(source.get("id") or "").strip()
        if _is_generic_source_title(source.get("title")) and source.get("filename"):
            recovered_title, recovered_author = _title_author_from_filename(
                str(source.get("filename") or "")
            )
            if recovered_title and recovered_title != str(source.get("title") or ""):
                source["title"] = recovered_title
                changed = True
            if recovered_author and not str(source.get("author") or "").strip():
                source["author"] = recovered_author
                changed = True

        # Keep strategy provenance labels aligned with a repaired source record.
        resolved_title = str(source.get("title") or "").strip()
        resolved_author = str(source.get("author") or "").strip()
        if source_id and resolved_title:
            for strategy in strategies:
                if str(strategy.get("source_id") or "").strip() != source_id:
                    continue
                if (
                    _is_generic_source_title(strategy.get("source_title"))
                    and not _is_generic_source_title(resolved_title)
                ):
                    strategy["source_title"] = resolved_title
                    changed = True
                if resolved_author and not str(strategy.get("source_author") or "").strip():
                    strategy["source_author"] = resolved_author
                    changed = True

    grouped: dict[str, list[dict[str, Any]]] = {}
    for strategy in strategies:
        source_id = str(strategy.get("source_id") or "").strip()
        if not source_id:
            continue
        source_type = str(strategy.get("source_type") or "").strip().casefold()
        source_url = str(strategy.get("source_url") or "").strip()

        # Do not turn cross-source/synthetic strategy records into fake research sources.
        if source_type == "youtube" and not (
            source_url or source_id.casefold().startswith("yt-")
        ):
            continue
        if source_type in {"synthetic", "strategy_dna", "research_synthesis"}:
            continue
        grouped.setdefault(source_id, []).append(strategy)

    for source_id, group in grouped.items():
        unique_strategy_ids = {
            str(item.get("id") or "").strip()
            for item in group
            if str(item.get("id") or "").strip()
        }
        strategy_count = len(unique_strategy_ids) or len(group)

        if source_id in source_index:
            source = source_index[source_id]
            if int(source.get("strategy_count") or 0) != strategy_count:
                source["strategy_count"] = strategy_count
                changed = True
            continue

        def best_text(field: str) -> str:
            values = [
                str(item.get(field) or "").strip()
                for item in group
                if str(item.get(field) or "").strip()
            ]
            if field == "source_title":
                values = [value for value in values if not _is_generic_source_title(value)]
            return values[0] if values else ""

        source_type = best_text("source_type") or "research_source"
        source_title = best_text("source_title")
        source_author = best_text("source_author") or best_text("creator")
        source_url = best_text("source_url")
        if not source_title:
            source_title = (
                "Recovered YouTube source"
                if source_type.casefold() == "youtube"
                else "Recovered research source"
            )

        analyzed_at = best_text("analyzed_at") or best_text("created_at") or _utc_iso()
        recovered = {
            "id": source_id,
            "source_type": source_type,
            "source_claim_status": (
                "unverified_source_claim"
                if str(source_type or "").strip().casefold()
                in {"youtube", "book_or_document", "research_source"}
                else "research_hypothesis"
            ),
            "source_role": "hypothesis_generator",
            "title": source_title,
            "author": source_author,
            "source_url": source_url,
            "summary": (
                f"Recovered from {strategy_count} saved strategy record"
                + ("" if strategy_count == 1 else "s")
                + " already stored in the Trading Intelligence Library."
            ),
            "analyzed_at": analyzed_at,
            "analysis_stage": "complete",
            "analysis_in_progress": False,
            "recovered_from_strategies": True,
            "strategy_count": strategy_count,
        }
        sources.append(recovered)
        source_index[source_id] = recovered
        changed = True

    result["knowledge_sources"] = sources
    result["strategies"] = strategies
    return result, changed


def extract_source_text(filename: str, payload: bytes) -> tuple[str, dict[str, Any]]:
    """Extract text from PDF, TXT, or Markdown while preserving coarse page markers."""
    if not payload:
        raise AppError("The uploaded source is empty.")
    if len(payload) > MAX_SOURCE_BYTES:
        raise AppError("Keep each uploaded source under 20 MB for this first version.")

    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix in {"txt", "md", "markdown"}:
        text = payload.decode("utf-8", errors="replace")
        metadata = {"format": suffix or "text", "pages": None}
    elif suffix == "pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise AppError("PDF support is not installed. Add pypdf to requirements.txt.") from exc
        try:
            reader = PdfReader(io.BytesIO(payload))
        except Exception as exc:
            raise AppError(f"That PDF could not be opened: {exc}") from exc
        pieces: list[str] = []
        extracted_pages = 0
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""
            page_text = page_text.strip()
            if page_text:
                extracted_pages += 1
                pieces.append(f"\n\n[[PAGE {page_number}]]\n{page_text}")
        text = "".join(pieces).strip()
        metadata = {
            "format": "pdf",
            "pages": len(reader.pages),
            "pages_with_text": extracted_pages,
        }
        if not text:
            raise AppError(
                "No readable text was found in that PDF. It may be image-only/scanned; "
                "OCR support can be added as a later ingestion option."
            )
    else:
        raise AppError("Upload a PDF, TXT, or Markdown file in this first version.")

    text = re.sub(r"\x00+", "", text).strip()
    if len(text) > MAX_SOURCE_CHARACTERS:
        text = text[:MAX_SOURCE_CHARACTERS]
        metadata["truncated"] = True
    else:
        metadata["truncated"] = False

    if len(text) < 200:
        raise AppError("The source did not contain enough readable text to analyze.")
    metadata["characters"] = len(text)
    return text, metadata


def chunk_source_text(
    text: str,
    *,
    target_chars: int = DEFAULT_CHUNK_CHARACTERS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Split a long source on paragraph boundaries with small context overlap."""
    clean = str(text or "").strip()
    if not clean:
        return []
    target_chars = max(8_000, int(target_chars))
    overlap_chars = max(0, min(int(overlap_chars), target_chars // 4))
    paragraphs = re.split(r"\n{2,}", clean)
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        candidate = paragraph if not current else current + "\n\n" + paragraph
        if len(candidate) <= target_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)
            tail = current[-overlap_chars:] if overlap_chars else ""
            current = (tail + "\n\n" + paragraph).strip()
        else:
            for start in range(0, len(paragraph), target_chars - overlap_chars):
                piece = paragraph[start : start + target_chars]
                if piece:
                    chunks.append(piece)
                if start + target_chars >= len(paragraph):
                    break
            current = ""

    if current:
        chunks.append(current)
    return chunks


NATIVE_RULE_SCHEMA_VERSION = 6


def upgrade_native_strategy_rules(strategy: dict[str, Any]) -> dict[str, Any]:
    """Upgrade saved strategy text into newly supported rules without inventing author claims."""
    item = dict(strategy or {})
    rules = normalize_machine_rules(item.get("machine_rules"))
    overrides = {
        key: value
        for key, value in normalize_machine_rules(item.get("research_rule_overrides")).items()
        if value is not None
    }
    text_parts = [
        item.get("name"),
        item.get("summary"),
        *(item.get("indicators") or []),
        *(item.get("entry_conditions") or []),
        *(item.get("exit_conditions") or []),
        *(item.get("risk_rules") or []),
        *(item.get("avoid_conditions") or []),
        *(item.get("stock_selection") or []),
        *(item.get("market_context") or []),
        *(item.get("unresolved_rules") or []),
    ]
    text = " ".join(str(value or "") for value in text_parts).casefold()
    changed = False
    explicit_migrations = list(item.get("native_explicit_rule_migrations") or [])

    previous_high_language = bool(
        re.search(
            r"(?:previous|prior)\s+(?:trading\s+)?day(?:'s)?\s+high|yesterday(?:'s)?\s+high",
            text,
        )
    )
    breakout_language = any(
        token in text
        for token in ("breakout", "break out", "break above", "break over", "fresh breakout")
    )
    if (
        rules.get("previous_day_high_breakout") is None
        and previous_high_language
        and breakout_language
    ):
        rules["previous_day_high_breakout"] = True
        explicit_migrations.append(
            {
                "rule": "previous_day_high_breakout",
                "value": True,
                "basis": "Explicit saved source text describes a breakout through the previous trading day's high.",
            }
        )
        changed = True

    previous_session_language = bool(
        re.search(r"(?:previous|prior)\s+(?:trading\s+)?(?:day|session)|yesterday", text)
    )
    unusual_activity_language = any(
        phrase in text
        for phrase in (
            "extremely active",
            "very active",
            "unusually active",
            "unusual volume",
            "heavy volume",
            "high volume",
        )
    )
    if (
        rules.get("min_previous_day_volume_ratio") is None
        and overrides.get("min_previous_day_volume_ratio") is None
        and previous_session_language
        and unusual_activity_language
    ):
        # The source supplied a qualitative prior-session activity requirement but no
        # number. Seed a neutral research hypothesis that the optimizer can vary.
        overrides["min_previous_day_volume_ratio"] = 2.0
        assumptions = list(item.get("compiler_assumptions") or [])
        if not any(
            isinstance(record, dict)
            and record.get("target_rule") == "min_previous_day_volume_ratio"
            for record in assumptions
        ):
            assumptions.append(
                {
                    "target_rule": "min_previous_day_volume_ratio",
                    "value": 2.0,
                    "source_requirement": "Qualitative requirement that the previous session was unusually/extremely active.",
                    "rationale": (
                        "2.0x is an automated starting research hypothesis, not an author-stated threshold. "
                        "The optimizer is allowed to vary it."
                    ),
                    "confidence": 80.0,
                    "accepted_at": _utc_iso(),
                    "model": "native-rule-upgrade",
                    "accepted_by": "ai_autopilot",
                    "is_research_assumption": True,
                }
            )
        item["compiler_assumptions"] = assumptions[-150:]
        changed = True

    # Backfill moving-average structure from saved source text now that the
    # deterministic engine can actually represent it. Only explicit periods/structure
    # are migrated into source rules; vague distances remain unresolved for the compiler.
    ema_periods = sorted(
        {
            int(match)
            for match in re.findall(r"\b(\d{1,3})\s*(?:-\s*)?ema\b", text, flags=re.IGNORECASE)
            if 2 <= int(match) <= 500
        }
    )
    pullback_language = any(
        phrase in text
        for phrase in ("pullback", "pull back", "consolidates back", "tap the", "trade close to")
    )
    if ema_periods and pullback_language:
        fast_period = ema_periods[0]
        if rules.get("fast_ema_period") is None:
            rules["fast_ema_period"] = fast_period
            explicit_migrations.append({
                "rule": "fast_ema_period",
                "value": fast_period,
                "basis": f"Saved source text explicitly names the {fast_period} EMA in the pullback setup.",
            })
            changed = True
        if rules.get("require_fast_ema_pullback") is None:
            rules["require_fast_ema_pullback"] = True
            explicit_migrations.append({
                "rule": "require_fast_ema_pullback",
                "value": True,
                "basis": "Saved source text explicitly describes a pullback/tap toward the fast EMA.",
            })
            changed = True

    if len(ema_periods) >= 2:
        slow_period = next((value for value in ema_periods if value > ema_periods[0]), None)
        if slow_period is not None and rules.get("slow_ema_period") is None:
            rules["slow_ema_period"] = slow_period
            explicit_migrations.append({
                "rule": "slow_ema_period",
                "value": slow_period,
                "basis": f"Saved source text explicitly names the {slow_period} EMA as a secondary trend average.",
            })
            changed = True

    trend_period = next((value for value in reversed(ema_periods) if value >= 100), None)
    if trend_period is not None and rules.get("trend_ema_period") is None:
        rules["trend_ema_period"] = trend_period
        explicit_migrations.append({
            "rule": "trend_ema_period",
            "value": trend_period,
            "basis": f"Saved source text explicitly names the {trend_period} EMA as a long-term trend reference.",
        })
        changed = True

    above_moving_averages = any(
        phrase in text
        for phrase in (
            "above its moving averages",
            "above the moving averages",
            "trading above its moving averages",
            "trading above the 9 ema",
            "trading above the 20 ema",
        )
    )
    if rules.get("slow_ema_period") is not None and above_moving_averages and rules.get("require_price_above_slow_ema") is None:
        rules["require_price_above_slow_ema"] = True
        explicit_migrations.append({
            "rule": "require_price_above_slow_ema",
            "value": True,
            "basis": "Saved source text explicitly requires the trend to remain above its moving averages.",
        })
        changed = True

    long_below_trend_avoid = bool(
        re.search(r"(?:avoid|rarely|do not|don't)[^\.]{0,80}(?:long|buy)[^\.]{0,80}below[^\.]{0,40}(?:200\s*ema|moving average)", text)
        or "avoid buying long when price is below the 200 ema" in text
    )
    if rules.get("trend_ema_period") is not None and (above_moving_averages or long_below_trend_avoid) and rules.get("require_price_above_trend_ema") is None:
        rules["require_price_above_trend_ema"] = True
        explicit_migrations.append({
            "rule": "require_price_above_trend_ema",
            "value": True,
            "basis": "Saved source text explicitly avoids long entries below the long-term EMA / requires trend alignment above it.",
        })
        changed = True

    first_second_pullback = bool(
        re.search(r"(?:first|1st)[^\.]{0,30}(?:second|2nd)[^\.]{0,30}pullback|(?:first|1st)\s+(?:or|and)\s+(?:second|2nd)\s+pullback", text)
    )
    if first_second_pullback and rules.get("max_pullback_number") is None:
        rules["max_pullback_number"] = 2
        explicit_migrations.append({
            "rule": "max_pullback_number",
            "value": 2,
            "basis": "Saved source text explicitly prefers the first and second moving-average pullbacks.",
        })
        changed = True

    stop_below_ema_language = bool(
        re.search(r"stop[^\.]{0,80}below[^\.]{0,50}(?:\d{1,3}\s*)?ema", text)
    )
    if stop_below_ema_language and rules.get("stop_below_fast_ema") is None:
        rules["stop_below_fast_ema"] = True
        explicit_migrations.append({
            "rule": "stop_below_fast_ema",
            "value": True,
            "basis": "Saved source text explicitly places the stop below the fast EMA support area.",
        })
        changed = True

    exit_text = " ".join(
        str(value or "")
        for value in (
            *(item.get("exit_conditions") or []),
            *(item.get("risk_rules") or []),
        )
    ).casefold()

    if rules.get("exit_below_vwap") is None and (
        re.search(r"(?:exit|sell|close)[^\.]{0,80}(?:below|under|lose|loses|loss of)[^\.]{0,40}vwap", exit_text)
        or re.search(r"(?:lose|loses|loss of)[^\.]{0,30}vwap[^\.]{0,60}(?:exit|sell|close)", exit_text)
    ):
        rules["exit_below_vwap"] = True
        explicit_migrations.append({
            "rule": "exit_below_vwap",
            "value": True,
            "basis": "Saved source text explicitly exits when VWAP is lost.",
        })
        changed = True

    if rules.get("exit_below_fast_ema") is None and (
        re.search(r"(?:exit|sell|close)[^\.]{0,80}(?:below|under|lose|loses)[^\.]{0,50}(?:\d{1,3}\s*)?ema", exit_text)
        or re.search(r"(?:close|closes)[^\.]{0,30}below[^\.]{0,40}(?:\d{1,3}\s*)?ema", exit_text)
    ):
        rules["exit_below_fast_ema"] = True
        explicit_migrations.append({
            "rule": "exit_below_fast_ema",
            "value": True,
            "basis": "Saved source text explicitly exits when the EMA is lost.",
        })
        changed = True

    if rules.get("trailing_stop_pct") is None:
        trailing_match = (
            re.search(
                r"(?:trailing\s+stop|trail(?:ing)?\s+(?:the\s+)?stop)[^0-9%]{0,30}(\d+(?:\.\d+)?)\s*%",
                exit_text,
            )
            or re.search(
                r"(\d+(?:\.\d+)?)\s*%[^\.]{0,30}(?:trailing\s+stop|trail(?:ing)?\s+(?:the\s+)?stop)",
                exit_text,
            )
        )
        if trailing_match:
            normalized_trail = normalize_machine_rules(
                {"trailing_stop_pct": safe_float(trailing_match.group(1))}
            ).get("trailing_stop_pct")
            if normalized_trail is not None:
                rules["trailing_stop_pct"] = normalized_trail
                explicit_migrations.append({
                    "rule": "trailing_stop_pct",
                    "value": normalized_trail,
                    "basis": "Saved source text explicitly states a percentage trailing stop.",
                })
                changed = True

    if rules.get("move_stop_to_breakeven_at_r") is None and (
        "breakeven" in exit_text or "break even" in exit_text or "break-even" in exit_text
    ):
        breakeven_match = (
            re.search(r"(\d+(?:\.\d+)?)\s*r[^\.]{0,80}(?:breakeven|break even|break-even)", exit_text)
            or re.search(r"(?:breakeven|break even|break-even)[^\.]{0,80}(\d+(?:\.\d+)?)\s*r", exit_text)
        )
        if breakeven_match:
            normalized_breakeven = normalize_machine_rules(
                {"move_stop_to_breakeven_at_r": safe_float(breakeven_match.group(1))}
            ).get("move_stop_to_breakeven_at_r")
            if normalized_breakeven is not None:
                rules["move_stop_to_breakeven_at_r"] = normalized_breakeven
                explicit_migrations.append({
                    "rule": "move_stop_to_breakeven_at_r",
                    "value": normalized_breakeven,
                    "basis": "Saved source text explicitly states the R trigger for moving the stop to breakeven.",
                })
                changed = True

    ai_options = {
        str(name): list(values)
        for name, values in (item.get("ai_candidate_rule_options") or {}).items()
        if isinstance(values, list)
    }
    assumptions = list(item.get("compiler_assumptions") or [])

    def add_native_assumption(
        rule_name: str,
        value: Any,
        options: list[Any],
        source_requirement: str,
        rationale: str,
    ) -> None:
        nonlocal changed
        parsed = normalize_machine_rules({rule_name: value}).get(rule_name)
        if parsed is None:
            return
        if rules.get(rule_name) is None and overrides.get(rule_name) is None:
            overrides[rule_name] = parsed
            changed = True
        clean_options: list[Any] = []
        for raw_option in options:
            option = normalize_machine_rules({rule_name: raw_option}).get(rule_name)
            if option is not None and option not in clean_options:
                clean_options.append(option)
        if clean_options:
            ai_options[rule_name] = clean_options
        if not any(
            isinstance(record, dict)
            and record.get("target_rule") == rule_name
            and str(record.get("accepted_by") or "") == "native-exit-management-research"
            for record in assumptions
        ):
            assumptions.append(
                {
                    "target_rule": rule_name,
                    "value": parsed,
                    "source_requirement": source_requirement,
                    "rationale": rationale,
                    "confidence": 90.0,
                    "accepted_at": _utc_iso(),
                    "model": "native-rule-upgrade",
                    "accepted_by": "native-exit-management-research",
                    "is_research_assumption": True,
                }
            )

    # Migrate explicit multi-stage partials from already-saved source text. This only
    # activates when at least two exact percent + R pairs are present; qualitative
    # language still remains a research assumption rather than an invented rule.
    explicit_stage_pairs: list[tuple[float, float]] = []
    # Pair each percentage with the nearest R multiple that follows it inside
    # the same clause. A single permissive prefix regex can cross-pair the first
    # percentage with a later target when a sentence contains several stages.
    stage_pattern = (
        r"(?<![\d.])(\d+(?:\.\d+)?)\s*%"
        r"[^\.;]{0,36}?"
        r"(\d+(?:\.\d+)?)\s*r\b"
    )
    for fraction_text, r_text in re.findall(stage_pattern, exit_text):
        fraction_value = safe_float(fraction_text)
        r_value = safe_float(r_text)
        if fraction_value is None or r_value is None:
            continue
        pair = (float(fraction_value), float(r_value))
        if pair not in explicit_stage_pairs:
            explicit_stage_pairs.append(pair)
    if len(explicit_stage_pairs) >= 2 and not rules.get("scale_out_stages"):
        normalized_stages = normalize_machine_rules({
            "scale_out_stages": [
                {"fraction_pct": fraction, "at_r": at_r}
                for fraction, at_r in explicit_stage_pairs
            ]
        }).get("scale_out_stages")
        if normalized_stages and len(normalized_stages) >= 2:
            rules["scale_out_stages"] = normalized_stages
            explicit_migrations.append({
                "rule": "scale_out_stages",
                "value": normalized_stages,
                "basis": (
                    "Saved source text explicitly states multiple partial-exit percentages "
                    "and their R-multiple triggers."
                ),
            })
            changed = True

    scale_language = any(
        phrase in exit_text
        for phrase in ("scale out", "scaling out", "partial profit", "take partial")
    )
    if scale_language and not rules.get("scale_out_stages"):
        if rules.get("scale_out_fraction_pct") is None:
            fraction_value = None
            fraction_basis = ""
            half_language = any(
                phrase in exit_text
                for phrase in ("half the position", "half position", "sell half", "take half")
            )
            if half_language:
                fraction_value = 50.0
                fraction_basis = "Saved source text explicitly says to exit half of the position."
            else:
                percent_match = (
                    re.search(
                        r"(?:scale out|scaling out|partial profit|take partial)[^\.]{0,50}(\d+(?:\.\d+)?)\s*%",
                        exit_text,
                    )
                    or re.search(
                        r"(\d+(?:\.\d+)?)\s*%[^\.]{0,50}(?:scale out|scaling out|partial profit|take partial)",
                        exit_text,
                    )
                )
                if percent_match:
                    fraction_value = safe_float(percent_match.group(1))
                    fraction_basis = "Saved source text explicitly states the partial-exit position percentage."
            normalized_fraction = normalize_machine_rules(
                {"scale_out_fraction_pct": fraction_value}
            ).get("scale_out_fraction_pct")
            if normalized_fraction is not None:
                rules["scale_out_fraction_pct"] = normalized_fraction
                explicit_migrations.append(
                    {
                        "rule": "scale_out_fraction_pct",
                        "value": normalized_fraction,
                        "basis": fraction_basis,
                    }
                )
                changed = True
            else:
                add_native_assumption(
                    "scale_out_fraction_pct",
                    50.0,
                    [25.0, 33.3, 50.0, 66.7, 75.0],
                    "The source explicitly requires taking partial profits / scaling out but does not quantify the position fraction.",
                    (
                        "50% is only a neutral optimizer seed. The Lab tests a broad set of partial-exit "
                        "fractions and does not attribute any of them to the source author."
                    ),
                )

        if rules.get("scale_out_at_r") is None:
            scale_r_match = (
                re.search(
                    r"(?:scale out|scaling out|partial profit|take partial)[^\.]{0,70}(\d+(?:\.\d+)?)\s*r\b",
                    exit_text,
                )
                or re.search(
                    r"(\d+(?:\.\d+)?)\s*r\b[^\.]{0,70}(?:scale out|scaling out|partial profit|take partial)",
                    exit_text,
                )
            )
            scale_r_value = safe_float(scale_r_match.group(1)) if scale_r_match else None
            normalized_scale_r = normalize_machine_rules(
                {"scale_out_at_r": scale_r_value}
            ).get("scale_out_at_r")
            if normalized_scale_r is not None:
                rules["scale_out_at_r"] = normalized_scale_r
                explicit_migrations.append(
                    {
                        "rule": "scale_out_at_r",
                        "value": normalized_scale_r,
                        "basis": "Saved source text explicitly states the R-multiple for taking the partial exit.",
                    }
                )
                changed = True
            else:
                add_native_assumption(
                    "scale_out_at_r",
                    1.0,
                    [0.5, 0.75, 1.0, 1.5, 2.0],
                    "The source explicitly requires taking partial profits / scaling out but does not quantify the first partial target.",
                    (
                        "1R is only a neutral optimizer seed. The Lab tests multiple R-based partial targets "
                        "and keeps them labeled as research assumptions."
                    ),
                )

    # Preserve explicitly structural trailing instructions separately from a
    # percentage trail. These levels are applied causally to the next bar.
    structural_trails = (
        (
            "trail_below_avwap",
            r"trail[^\.]{0,60}(?:under|below)[^\.]{0,35}(?:anchored vwap|avwap)",
            "Saved source text explicitly trails the remainder beneath anchored VWAP.",
        ),
        (
            "trail_below_vwap",
            r"trail[^\.]{0,60}(?:under|below)[^\.]{0,35}vwap",
            "Saved source text explicitly trails the remainder beneath session VWAP.",
        ),
        (
            "trail_below_fast_ema",
            r"trail[^\.]{0,60}(?:under|below)[^\.]{0,35}(?:fast )?(?:\d{1,3}\s*)?ema",
            "Saved source text explicitly trails the remainder beneath the fast EMA.",
        ),
    )
    for rule_name, pattern, basis in structural_trails:
        if rule_name == "trail_below_vwap" and rules.get("trail_below_avwap") is True:
            continue
        if rules.get(rule_name) is not True and re.search(pattern, exit_text):
            rules[rule_name] = True
            explicit_migrations.append({"rule": rule_name, "value": True, "basis": basis})
            changed = True

    qualitative_trailing = any(
        phrase in exit_text for phrase in ("trailing stop", "trail the stop", "trail stop")
    )
    if (
        qualitative_trailing
        and rules.get("trailing_stop_pct") is None
        and not any(
            rules.get(name) is True
            for name in ("trail_below_vwap", "trail_below_fast_ema", "trail_below_avwap")
        )
    ):
        add_native_assumption(
            "trailing_stop_pct",
            3.0,
            [1.0, 2.0, 3.0, 5.0, 8.0],
            "The source explicitly requires trailing the stop but does not give a percentage trail.",
            (
                "3% is only a neutral optimizer seed. The Lab tests several trailing distances and "
                "does not present the chosen value as author-stated."
            ),
        )

    qualitative_breakeven = (
        "breakeven" in exit_text or "break even" in exit_text or "break-even" in exit_text
    )
    breakeven_after_partial = bool(
        qualitative_breakeven
        and (
            re.search(
                r"(?:partial|scale out|scaling out)[^\.]{0,90}(?:breakeven|break even|break-even)",
                exit_text,
            )
            or re.search(
                r"(?:breakeven|break even|break-even)[^\.]{0,90}(?:after|once)[^\.]{0,50}(?:partial|scale out|scaling out)",
                exit_text,
            )
        )
    )
    if (
        breakeven_after_partial
        and rules.get("move_stop_to_breakeven_after_scale_out") is not True
    ):
        rules["move_stop_to_breakeven_after_scale_out"] = True
        explicit_migrations.append({
            "rule": "move_stop_to_breakeven_after_scale_out",
            "value": True,
            "basis": "Saved source text explicitly moves the stop to breakeven after taking a partial.",
        })
        changed = True
    if (
        qualitative_breakeven
        and not breakeven_after_partial
        and rules.get("move_stop_to_breakeven_at_r") is None
    ):
        add_native_assumption(
            "move_stop_to_breakeven_at_r",
            1.0,
            [0.5, 0.75, 1.0, 1.5, 2.0],
            "The source explicitly says to move the stop to breakeven but does not quantify the trigger.",
            (
                "1R is only a neutral optimizer seed. The Lab tests multiple activation thresholds and "
                "keeps the entire range labeled as research assumptions."
            ),
        )

    if ai_options:
        item["ai_candidate_rule_options"] = ai_options
    if assumptions:
        item["compiler_assumptions"] = assumptions[-150:]

    avwap_language = "anchored vwap" in text or "avwap" in text
    if avwap_language:
        direction = str(item.get("direction") or "").strip().casefold()
        mode = rules.get("avwap_anchor_mode")
        if mode is None:
            if "higher low" in text or ("handoff" in text and direction in {"long", "both"}):
                mode = "higher_low_handoff"
            elif "lower high" in text or ("handoff" in text and direction == "short"):
                mode = "lower_high_handoff"
            elif "swing low" in text or ("cross purchase" in text and "dip" in text) or "rising avwap" in text:
                mode = "swing_low"
            elif "swing high" in text or ("cross short" in text and "rip" in text) or "declining avwap" in text:
                mode = "swing_high"
            elif re.search(r"anchor(?:ed|ing)?[^.]{0,50}(?:previous|prior)[^.]{0,30}day[^.]{0,20}high", text):
                mode = "previous_day_high_break"
            elif re.search(r"anchor(?:ed|ing)?[^.]{0,50}breakout(?: bar)?", text):
                mode = "breakout_bar"
            elif re.search(r"anchor(?:ed|ing)?[^.]{0,60}(?:second|2nd) minute", text):
                mode = "session_minute"
                if rules.get("avwap_anchor_session_minute") is None:
                    rules["avwap_anchor_session_minute"] = 1
                    explicit_migrations.append({
                        "rule": "avwap_anchor_session_minute",
                        "value": 1,
                        "basis": "Saved source text explicitly anchors AVWAP to the second session minute.",
                    })
                    changed = True
        if mode is not None and rules.get("avwap_anchor_mode") is None:
            rules["avwap_anchor_mode"] = mode
            explicit_migrations.append({
                "rule": "avwap_anchor_mode",
                "value": mode,
                "basis": "Saved source text identifies a causal anchored-VWAP reference structure.",
            })
            changed = True

        if rules.get("avwap_anchor_mode") in {"swing_low", "swing_high", "higher_low_handoff", "lower_high_handoff"}:
            if overrides.get("avwap_pivot_confirm_bars") is None and rules.get("avwap_pivot_confirm_bars") is None:
                overrides["avwap_pivot_confirm_bars"] = 2
                assumptions = list(item.get("compiler_assumptions") or [])
                if not any(isinstance(record, dict) and record.get("target_rule") == "avwap_pivot_confirm_bars" for record in assumptions):
                    assumptions.append({
                        "target_rule": "avwap_pivot_confirm_bars",
                        "value": 2,
                        "source_requirement": "Use a causally confirmed AVWAP swing/handoff anchor.",
                        "rationale": "Two right-side bars are a starting research assumption, not an author-stated threshold.",
                        "confidence": 80.0,
                        "accepted_at": _utc_iso(),
                        "model": "native-rule-upgrade",
                        "accepted_by": "ai_autopilot",
                        "is_research_assumption": True,
                    })
                item["compiler_assumptions"] = assumptions[-150:]
                changed = True

        if "rising avwap" in text and rules.get("require_avwap_rising") is None:
            rules["require_avwap_rising"] = True
            explicit_migrations.append({"rule": "require_avwap_rising", "value": True, "basis": "Saved source text explicitly requires a rising AVWAP."})
            changed = True
        if "declining avwap" in text and rules.get("require_avwap_rising") is None:
            rules["require_avwap_rising"] = False
            explicit_migrations.append({"rule": "require_avwap_rising", "value": False, "basis": "Saved source text explicitly requires a declining AVWAP."})
            changed = True
        if any(phrase in text for phrase in ("above avwap", "above the avwap", "above anchored vwap", "above the anchored vwap")) and rules.get("require_price_above_avwap") is None:
            rules["require_price_above_avwap"] = True
            explicit_migrations.append({"rule": "require_price_above_avwap", "value": True, "basis": "Saved source text explicitly requires price above AVWAP."})
            changed = True
        if any(phrase in text for phrase in ("below avwap", "below the avwap", "below anchored vwap", "below the anchored vwap")) and rules.get("require_price_above_avwap") is None:
            rules["require_price_above_avwap"] = False
            explicit_migrations.append({"rule": "require_price_above_avwap", "value": False, "basis": "Saved source text explicitly requires price below AVWAP."})
            changed = True
        if "reclaim" in text and rules.get("avwap_reclaim") is None:
            rules["avwap_reclaim"] = True
            explicit_migrations.append({"rule": "avwap_reclaim", "value": True, "basis": "Saved source text explicitly describes an AVWAP reclaim."})
            changed = True
        if any(phrase in text for phrase in ("pullback", "pull back", "support")) and rules.get("require_avwap_pullback") is None:
            rules["require_avwap_pullback"] = True
            explicit_migrations.append({"rule": "require_avwap_pullback", "value": True, "basis": "Saved source text explicitly uses AVWAP as pullback/support structure."})
            changed = True
            if overrides.get("avwap_pullback_tolerance_pct") is None and rules.get("avwap_pullback_tolerance_pct") is None:
                overrides["avwap_pullback_tolerance_pct"] = 0.5
                assumptions = list(item.get("compiler_assumptions") or [])
                if not any(isinstance(record, dict) and record.get("target_rule") == "avwap_pullback_tolerance_pct" for record in assumptions):
                    assumptions.append({
                        "target_rule": "avwap_pullback_tolerance_pct",
                        "value": 0.5,
                        "source_requirement": "Price pulls back near/to AVWAP support.",
                        "rationale": "0.5% is a starting research tolerance, not an author-stated distance.",
                        "confidence": 75.0,
                        "accepted_at": _utc_iso(),
                        "model": "native-rule-upgrade",
                        "accepted_by": "ai_autopilot",
                        "is_research_assumption": True,
                    })
                item["compiler_assumptions"] = assumptions[-150:]
                changed = True

        avwap_exit_text = " ".join(str(value or "") for value in (*(item.get("exit_conditions") or []), *(item.get("risk_rules") or []))).casefold()
        if rules.get("exit_below_avwap") is None and re.search(r"(?:exit|sell|close)[^.]{0,80}(?:below|lose|loses)[^.]{0,40}(?:avwap|anchored vwap)", avwap_exit_text):
            rules["exit_below_avwap"] = True
            explicit_migrations.append({"rule": "exit_below_avwap", "value": True, "basis": "Saved source text explicitly exits on loss of AVWAP."})
            changed = True
        if rules.get("stop_below_avwap") is None and re.search(r"stop[^.]{0,80}below[^.]{0,40}(?:avwap|anchored vwap)", avwap_exit_text):
            rules["stop_below_avwap"] = True
            explicit_migrations.append({"rule": "stop_below_avwap", "value": True, "basis": "Saved source text explicitly places the stop below AVWAP."})
            changed = True
            if overrides.get("stop_avwap_buffer_pct") is None and rules.get("stop_avwap_buffer_pct") is None:
                overrides["stop_avwap_buffer_pct"] = 0.3
                changed = True

    item["machine_rules"] = rules
    if overrides:
        item["research_rule_overrides"] = normalize_machine_rules(overrides)
    if explicit_migrations:
        item["native_explicit_rule_migrations"] = explicit_migrations[-50:]
    item["native_rule_schema_version"] = NATIVE_RULE_SCHEMA_VERSION

    if changed:
        item["validation_status"] = "unvalidated"
        item["optimization_status"] = "not_run"
        item.pop("validated_rules", None)
        item.pop("validated_backtest_settings", None)
        item.pop("validated_at", None)

    # A legacy validation may have been awarded to a simplified machine version of
    # the source strategy. Once the fidelity audit detects defining logic that was
    # never represented, that validation can no longer describe the original strategy.
    if str(item.get("validation_status") or "").lower() == "validated":
        integrity = strategy_integrity_report(item)
        if str(integrity.get("status") or "") == "blocked":
            item["previous_validation_invalidated_by_integrity_audit"] = {
                "reason": "Defining source logic is not faithfully modeled by the deterministic backtester.",
                "missing_requirements": list(integrity.get("critical_missing_requirements") or []),
            }
            item["validation_status"] = "unvalidated"
            item["optimization_status"] = "not_run"
            item.pop("validated_rules", None)
            item.pop("validated_backtest_settings", None)
            item.pop("validated_at", None)

    # Methodology migration: trusted validation labels must also come from the
    # post-audit historical-data contract. Preserve the old evidence for review,
    # but never keep frozen validated rules active when the provenance is legacy
    # or unknown.
    if str(item.get("validation_status") or "").lower() == "validated":
        last_validation = (
            item.get("last_validation")
            if isinstance(item.get("last_validation"), dict)
            else {}
        )
        last_autonomous = (
            item.get("last_autonomous_research")
            if isinstance(item.get("last_autonomous_research"), dict)
            else {}
        )
        market_integrity = (
            last_validation.get("market_data_integrity")
            if isinstance(last_validation.get("market_data_integrity"), dict)
            else {}
        )
        reuse_audit = (
            last_validation.get("holdout_reuse_audit")
            if isinstance(last_validation.get("holdout_reuse_audit"), dict)
            else {}
        )
        manual_or_finder_current = (
            str(market_integrity.get("mode") or "")
            in {"raw_prices", "raw_prices_post_latest_split", "raw_prices_post_corporate_action"}
            and reuse_audit.get("pristine") is True
        )
        autonomous_reuse_audit = (
            last_autonomous.get("holdout_reuse_audit")
            if isinstance(last_autonomous.get("holdout_reuse_audit"), dict)
            else {}
        )
        autonomous_current = (
            int(last_autonomous.get("validation_method_version") or 0) >= 4
            and str(last_autonomous.get("market_data_integrity_contract") or "")
            == "split_safe_raw_v1"
            and autonomous_reuse_audit.get("pristine") is True
        )
        if not (manual_or_finder_current or autonomous_current):
            item["previous_validation_invalidated_by_methodology_upgrade"] = {
                "reason": (
                    "The saved validation predates the current split-safe raw-price and "
                    "holdout-exposure integrity contract."
                ),
                "required_action": "Re-run validation under the current methodology.",
            }
            item["validation_status"] = "unvalidated"
            item["optimization_status"] = "revalidation_required"
            item.pop("validated_rules", None)
            item.pop("validated_backtest_settings", None)
            item.pop("validated_at", None)
    return item


def canonicalize_strategy(
    strategy: dict[str, Any],
    *,
    source_id: str,
    source_type: str,
    source_title: str,
    source_author: str = "",
) -> dict[str, Any]:
    item = dict(strategy or {})
    name = str(item.get("name") or "Unnamed strategy").strip()
    category = str(item.get("category") or "Uncategorized").strip()
    result = {
        **item,
        "id": str(item.get("id") or strategy_fingerprint(source_id, name, category)),
        "schema_version": CANONICAL_STRATEGY_VERSION,
        "name": name,
        "category": category,
        "direction": str(item.get("direction") or "unclear").strip().lower(),
        "summary": str(item.get("summary") or "").strip(),
        "machine_rules": normalize_machine_rules(item.get("machine_rules")),
        "confidence": max(0.0, min(100.0, safe_float(item.get("confidence"), 0.0) or 0.0)),
        "approved": bool(item.get("approved", False)),
        "source_type": source_type,
        "source_id": source_id,
        "source_title": source_title,
        "source_author": source_author,
        "source_url": str(item.get("source_url") or "").strip(),
        "source_claim_status": str(
            item.get("source_claim_status")
            or (
                "unverified_source_claim"
                if str(source_type or "").strip().casefold()
                in {"youtube", "book_or_document", "research_source"}
                else "research_hypothesis"
            )
        ),
        "source_role": str(item.get("source_role") or "hypothesis_generator"),
        "validation_status": str(item.get("validation_status") or "unvalidated"),
        "optimization_status": str(item.get("optimization_status") or "not_run"),
        "created_at": str(item.get("created_at") or _utc_iso()),
    }
    for field in (
        "indicators",
        "entry_conditions",
        "exit_conditions",
        "risk_rules",
        "avoid_conditions",
        "market_context",
        "stock_selection",
        "unresolved_rules",
        "evidence",
    ):
        value = item.get(field)
        result[field] = list(value) if isinstance(value, list) else []
    result = upgrade_native_strategy_rules(result)
    # Strategy DNA is a reusable research fingerprint, not a claim of profitability.
    # It is deterministically inferred from source-extracted fields and explicit rules so
    # older strategies gain the same structure without needing to re-read the source.
    result["strategy_dna"] = infer_strategy_dna(result)
    return result


def canonicalize_existing_strategy(strategy: dict[str, Any]) -> dict[str, Any]:
    source_type = str(strategy.get("source_type") or "youtube").strip().lower()
    source_title = str(strategy.get("source_title") or "Existing Trading Lab strategy")
    source_url = str(strategy.get("source_url") or "")
    source_id = str(
        strategy.get("source_id")
        or (
            "yt-" + hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:20]
            if source_url
            else "legacy-" + hashlib.sha256(str(strategy.get("id") or strategy.get("name") or "").encode("utf-8")).hexdigest()[:20]
        )
    )
    return canonicalize_strategy(
        strategy,
        source_id=source_id,
        source_type=source_type,
        source_title=source_title,
        source_author=str(strategy.get("creator") or strategy.get("source_author") or ""),
    )


RULE_COMPILER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_requirement": {"type": "string"},
                    "target_rule": {
                        "type": "string",
                        "enum": [
                            name
                            for name in MACHINE_RULE_SCHEMA["properties"].keys()
                            if name != "scale_out_stages"
                        ],
                    },
                    "proposed_value": {"type": "string"},
                    "is_research_assumption": {"type": "boolean"},
                    "rationale": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "source_requirement",
                    "target_rule",
                    "proposed_value",
                    "is_research_assumption",
                    "rationale",
                    "confidence",
                ],
            },
        },
        "unmapped_requirements": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "suggestions", "unmapped_requirements"],
}

RULE_COMPILER_PROMPT = """You are a strategy-rule compiler for a trading research application.
The supplied strategy was extracted from an educational source.

Your goal is NOT to rewrite the source or claim the strategy is profitable. Identify qualitative
requirements that can reasonably be represented by the application's existing machine-rule fields.

Strict rules:
- Never replace an existing explicit machine rule from the source.
- If the source did not give an exact numeric/time threshold, any concrete threshold you propose is
  a RESEARCH ASSUMPTION. Set is_research_assumption=true.
- Do not claim an assumed threshold came from the author.
- Prefer leaving a requirement unmapped over forcing a bad proxy.
- Only use target_rule values provided by the schema.
- proposed_value must be a plain value string such as "3.0", "true", "15", or "10:30".
- Map an explicit previous-day-high breakout structure to previous_day_high_breakout=true.
- A qualitative requirement that the previous session was "extremely active", "high volume",
  or similar can defensibly map to min_previous_day_volume_ratio as a RESEARCH ASSUMPTION.
  Do not imply the chosen multiple came from the author.
- A qualitative "strong prior-day move" can map to min_previous_day_change_pct only as a
  RESEARCH ASSUMPTION when the context clearly refers to price appreciation.
- For EMA/moving-average setups, preserve explicit source periods in fast_ema_period,
  slow_ema_period, or trend_ema_period; never change an author-stated period into a different period.
- "Pull back near/to the EMA" can map to require_fast_ema_pullback=true. If the source did not
  state an exact distance, a pullback_touch_tolerance_pct value is a RESEARCH ASSUMPTION.
- "Rising EMA" can map to require_fast_ema_rising=true only when the source actually requires
  a rising/sloping-up fast average.
- "Stop slightly below the EMA" can map to stop_below_fast_ema=true; any numeric
  stop_ema_buffer_pct is a RESEARCH ASSUMPTION unless the source gave the exact buffer.
- Preserve anchored-VWAP logic when it is causal and source-defined. Use avwap_anchor_mode only
  for an identifiable anchor such as a confirmed swing low/high, higher-low/lower-high handoff,
  breakout bar, previous-day-high break, or explicit session minute. Numeric pivot confirmation,
  pullback tolerance, and stop buffers are RESEARCH ASSUMPTIONS unless the source states them.
- Preserve exit logic. An explicit "exit/close if VWAP is lost" can map to exit_below_vwap=true;
  an explicit "exit/close if the fast EMA is lost" can map to exit_below_fast_ema=true.
- A source-stated percentage trailing stop maps to trailing_stop_pct. If the source says only
  "trail the stop" without a percentage, a proposed trailing_stop_pct is a RESEARCH ASSUMPTION.
- A source-stated "move stop to breakeven at X R" maps to move_stop_to_breakeven_at_r. If the
  trigger is qualitative, any proposed R threshold is a RESEARCH ASSUMPTION.
- A source-stated single partial exit maps to scale_out_fraction_pct (percent of the original
  position) and scale_out_at_r (R-multiple for the first partial). If either value is qualitative
  or omitted, proposed values are RESEARCH ASSUMPTIONS and should be varied by the optimizer.
  Do not flatten a source-authored multi-stage scale-out sequence into one assumed partial; preserve
  that sequence in the extracted strategy so the staged backtester can execute it faithfully.
- Never pretend tape-based exits or discretionary momentum-failure selling are supported by a
  fixed target. Keep unsupported exit mechanics unmapped.
- Do not add require_pullback_breakout unless the source explicitly requires breakout/confirmation
  after the pullback.
- Keep tape-reading, Level 2, float, borrow, proprietary indicators, subjective catalyst quality,
  and other unsupported concepts in unmapped_requirements unless an existing rule is a defensible proxy.
- High-confidence suggestions may be auto-applied by AI Autopilot as clearly labeled research
  assumptions. They must never overwrite or masquerade as explicit source rules.

Return only JSON matching the supplied schema.
"""


def coerce_machine_rule_value(rule_name: str, raw_value: Any) -> Any:
    """Parse a compiler suggestion using the same rule types as normalize_machine_rules."""
    if rule_name not in MACHINE_RULE_SCHEMA["properties"]:
        raise AppError(f"Unsupported machine rule: {rule_name}")
    probe = {name: None for name in MACHINE_RULE_SCHEMA["properties"]}
    probe[rule_name] = raw_value
    parsed = normalize_machine_rules(probe).get(rule_name)
    if parsed is None:
        raise AppError(f"{raw_value!r} is not a valid value for {rule_name}.")
    return parsed


def effective_strategy_for_research(strategy: dict[str, Any]) -> dict[str, Any]:
    """Overlay explicitly accepted research assumptions without mutating source-extracted rules."""
    item = dict(strategy or {})
    source_rules = normalize_machine_rules(item.get("machine_rules"))
    overrides = item.get("research_rule_overrides")
    if isinstance(overrides, dict):
        for name, value in normalize_machine_rules(overrides).items():
            # Source-explicit values remain authoritative. Research assumptions only fill gaps.
            if source_rules.get(name) is None and value is not None:
                source_rules[name] = value
    item["machine_rules"] = source_rules
    item["using_research_overrides"] = bool(
        isinstance(overrides, dict)
        and any(value is not None for value in normalize_machine_rules(overrides).values())
    )
    return item


class GeminiRuleCompiler:
    """Suggest measurable proxies with transient retry and model fallback."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        *,
        fallback_api_key: str = "",
        fallback_model: str = DEFAULT_GEMINI_FALLBACK_MODEL,
    ):
        key = str(api_key or "").strip()
        if not key:
            raise AppError("Add GEMINI_API_KEY to Streamlit Secrets before using the Rule Compiler.")
        self.api_key = key
        self.fallback_api_key = str(fallback_api_key or "").strip()
        if self.fallback_api_key and self.fallback_api_key == self.api_key:
            raise AppError(
                "GEMINI_PAID_API_KEY must be a different key from a separate Google project."
            )
        self.model = str(model or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
        self.primary_model = self.model
        fallback_candidates = [
            str(fallback_model or "").strip(),
            *DEFAULT_GEMINI_ADDITIONAL_FALLBACK_MODELS,
        ]
        self.fallback_models: list[str] = []
        for candidate in fallback_candidates:
            candidate = str(candidate or "").strip()
            if candidate and candidate != self.primary_model and candidate not in self.fallback_models:
                self.fallback_models.append(candidate)
        self._fallback_model_index = 0
        self.model_fallback_used = False
        self.paid_fallback_used = False

    @property
    def headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _activate_model_fallback(self, error: Exception | str) -> bool:
        if not provider_temporarily_unavailable(error):
            return False
        while self._fallback_model_index < len(self.fallback_models):
            candidate = self.fallback_models[self._fallback_model_index]
            self._fallback_model_index += 1
            if candidate == self.model:
                continue
            self.model = candidate
            self.model_fallback_used = True
            return True
        return False

    def _activate_paid_fallback(self, error: Exception | str) -> bool:
        if (
            self.paid_fallback_used
            or not self.fallback_api_key
            or not provider_quota_reached(error)
        ):
            return False
        self.api_key = self.fallback_api_key
        self.paid_fallback_used = True
        return True

    def _activate_quota_model_fallback(self, error: Exception | str) -> bool:
        """Try another model immediately when the current model's request quota is exhausted."""
        if not provider_quota_reached(error):
            return False
        while self._fallback_model_index < len(self.fallback_models):
            candidate = self.fallback_models[self._fallback_model_index]
            self._fallback_model_index += 1
            if candidate == self.model:
                continue
            self.model = candidate
            self.model_fallback_used = True
            return True
        return False

    def _generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        transient_attempts = 0
        quota_attempts = 0
        while True:
            model_name = self.model.removeprefix("models/")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", model_name):
                raise AppError("GEMINI_MODEL must contain a valid Gemini model name.")
            try:
                return _json_request(
                    f"{GEMINI_GENERATE_CONTENT_URL}/{model_name}:generateContent",
                    self.headers,
                    method="POST",
                    payload=payload,
                    timeout=180,
                )
            except AppError as exc:
                message = str(exc)
                if provider_temporarily_unavailable(exc):
                    if transient_attempts < BOOK_TRANSIENT_RETRIES_PER_MODEL:
                        retry_wait = min(
                            5 * (2 ** transient_attempts),
                            BOOK_TRANSIENT_MAX_WAIT_SECONDS,
                        )
                        transient_attempts += 1
                        sleep(retry_wait)
                        continue
                    if self._activate_model_fallback(exc):
                        transient_attempts = 0
                        quota_attempts = 0
                        continue

                if provider_quota_reached(exc):
                    if self._activate_paid_fallback(exc):
                        transient_attempts = 0
                        quota_attempts = 0
                        continue
                    if self._activate_quota_model_fallback(exc):
                        transient_attempts = 0
                        quota_attempts = 0
                        continue
                    retry_delay = GeminiBookAnalyzer._quota_retry_delay(message)
                    if retry_delay is not None and quota_attempts < BOOK_QUOTA_RETRIES:
                        quota_attempts += 1
                        sleep(retry_delay)
                        continue
                raise

    def compile(self, strategy: dict[str, Any]) -> dict[str, Any]:
        source_rules = {
            key: value
            for key, value in normalize_machine_rules(strategy.get("machine_rules")).items()
            if value is not None
        }
        context = {
            "name": strategy.get("name"),
            "category": strategy.get("category"),
            "summary": strategy.get("summary"),
            "indicators": strategy.get("indicators") or [],
            "entry_conditions": strategy.get("entry_conditions") or [],
            "exit_conditions": strategy.get("exit_conditions") or [],
            "risk_rules": strategy.get("risk_rules") or [],
            "avoid_conditions": strategy.get("avoid_conditions") or [],
            "market_context": strategy.get("market_context") or [],
            "stock_selection": strategy.get("stock_selection") or [],
            "unresolved_rules": strategy.get("unresolved_rules") or [],
            "explicit_machine_rules": source_rules,
        }
        prompt = RULE_COMPILER_PROMPT + "\n\nSTRATEGY JSON:\n" + json.dumps(context, indent=2, default=str)
        response = self._generate(
            {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseFormat": {
                        "text": {"mimeType": "APPLICATION_JSON", "schema": RULE_COMPILER_SCHEMA}
                    }
                },
            }
        )
        raw = _extract_generate_content_text(response)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AppError("Gemini returned Rule Compiler output that was not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise AppError("Gemini returned an unexpected Rule Compiler format.")

        cleaned: list[dict[str, Any]] = []
        for suggestion in parsed.get("suggestions") or []:
            if not isinstance(suggestion, dict):
                continue
            target = str(suggestion.get("target_rule") or "").strip()
            if target not in MACHINE_RULE_SCHEMA["properties"] or target in source_rules:
                continue
            try:
                parsed_value = coerce_machine_rule_value(target, suggestion.get("proposed_value"))
            except AppError:
                continue
            cleaned.append(
                {
                    **suggestion,
                    "target_rule": target,
                    "parsed_value": parsed_value,
                    "confidence": max(
                        0.0,
                        min(100.0, safe_float(suggestion.get("confidence"), 0.0) or 0.0),
                    ),
                    "is_research_assumption": bool(suggestion.get("is_research_assumption", True)),
                }
            )
        parsed["suggestions"] = cleaned
        parsed["generated_at"] = _utc_iso()
        parsed["model"] = self.model
        parsed["primary_model"] = self.primary_model
        parsed["model_fallback_used"] = self.model_fallback_used
        parsed["paid_fallback_used"] = self.paid_fallback_used
        return parsed


SEMANTIC_BACKTEST_COVERAGE_GATE = 90.0


def strategy_semantic_coverage(strategy: dict[str, Any]) -> dict[str, Any]:
    """Measure how faithfully the deterministic model represents the source strategy.

    A strategy is not considered fully modeled merely because *some* machine rules
    exist. Defining entry, universe, execution, risk, and exit requirements all count.
    """
    effective = effective_strategy_for_research(strategy)
    rules = normalize_machine_rules(effective.get("machine_rules"))
    pieces = [
        strategy.get("name"),
        strategy.get("category"),
        strategy.get("summary"),
        *(strategy.get("indicators") or []),
        *(strategy.get("entry_conditions") or []),
        *(strategy.get("exit_conditions") or []),
        *(strategy.get("risk_rules") or []),
        *(strategy.get("avoid_conditions") or []),
        *(strategy.get("market_context") or []),
        *(strategy.get("stock_selection") or []),
        *(strategy.get("unresolved_rules") or []),
    ]
    text = " ".join(str(value or "") for value in pieces).casefold()
    exit_text = " ".join(str(value or "") for value in strategy.get("exit_conditions") or []).casefold()
    risk_text = " ".join(str(value or "") for value in strategy.get("risk_rules") or []).casefold()
    requirements: list[dict[str, Any]] = []

    def add(
        label: str,
        keys: tuple[str, ...] = (),
        *,
        any_key: bool = False,
        dimension: str = "entry",
        critical: bool = True,
        modeled_override: bool | None = None,
        limitation: str = "",
    ) -> None:
        if modeled_override is None:
            values = [rules.get(key) for key in keys]
            modeled = (
                any(value is not None for value in values)
                if any_key
                else bool(keys) and all(value is not None for value in values)
            )
        else:
            modeled = bool(modeled_override)
        requirements.append({
            "label": label,
            "rule_keys": list(keys),
            "modeled": bool(modeled),
            "dimension": dimension,
            "critical": bool(critical),
            "limitation": limitation,
        })

    # Entry / setup structure.
    if "vwap" in text and any(
        phrase in text for phrase in ("above vwap", "above its moving averages", "and vwap")
    ):
        add("Price / trend relationship to VWAP", ("above_vwap",), any_key=True)

    ema_mentions = sorted({
        int(match)
        for match in re.findall(r"\b(\d{1,3})\s*(?:-\s*)?ema\b", text, flags=re.IGNORECASE)
        if 2 <= int(match) <= 500
    })
    pullback_setup = bool(ema_mentions) and any(
        phrase in text
        for phrase in ("pullback", "pull back", "consolidates back", "tap the", "trade close to")
    )
    if pullback_setup:
        add(
            f"Pullback to the {ema_mentions[0]} EMA",
            ("fast_ema_period", "require_fast_ema_pullback"),
        )
        if any(phrase in text for phrase in ("close to", "near the", "near its", "around the")):
            add(
                "Objective proximity/tolerance for the EMA pullback",
                ("pullback_touch_tolerance_pct",),
            )

    if len(ema_mentions) >= 2 and any(
        phrase in text
        for phrase in ("above its moving averages", "above the moving averages", "trading above")
    ):
        add(
            f"Secondary EMA trend alignment ({ema_mentions[1]} EMA)",
            ("slow_ema_period", "require_price_above_slow_ema"),
        )

    trend_period = next((value for value in reversed(ema_mentions) if value >= 100), None)
    if trend_period is not None and (
        "below the 200 ema" in text
        or "above its moving averages" in text
        or "long-term" in text
    ):
        add(
            f"Long-term EMA trend filter ({trend_period} EMA)",
            ("trend_ema_period", "require_price_above_trend_ema"),
        )

    if re.search(r"(?:first|1st)[^\.]{0,30}(?:second|2nd)[^\.]{0,30}pullback", text):
        add("First/second pullback preference", ("max_pullback_number",))

    if re.search(r"stop[^\.]{0,80}below[^\.]{0,50}(?:\d{1,3}\s*)?ema", text):
        add(
            "EMA-anchored structural stop",
            ("stop_below_fast_ema",),
            dimension="risk",
        )
        if "slightly below" in text:
            add(
                "Objective buffer below the EMA for the structural stop",
                ("stop_ema_buffer_pct",),
                dimension="risk",
            )

    if any(
        phrase in risk_text
        for phrase in ("below pullback low", "below the pullback low", "structure stop", "technical stop")
    ) and not re.search(r"below[^\.]{0,50}(?:\d{1,3}\s*)?ema", risk_text):
        add(
            "Structure/pullback-low stop",
            dimension="risk",
            modeled_override=False,
            limitation="The backtester does not yet maintain the source-defined pullback/structure low as a dynamic stop level.",
        )
    if any(phrase in risk_text for phrase in ("stop below vwap", "vwap stop")):
        add(
            "VWAP-anchored stop",
            dimension="risk",
            modeled_override=False,
            limitation="A VWAP close-loss exit is supported, but an intrabar stop anchored directly to VWAP is not yet modeled.",
        )

    # Exit / position-management fidelity.
    if (
        re.search(r"(?:target|take profit|profit target)[^\.]{0,80}\b\d+(?:\.\d+)?\s*r\b", exit_text)
        or re.search(r"\b\d+(?:\.\d+)?\s*r\b[^\.]{0,80}(?:target|take profit|profit target)", exit_text)
    ):
        add(
            "Explicit R-multiple profit target",
            ("reward_risk",),
            dimension="exit",
        )
    if any(
        phrase in exit_text
        for phrase in ("sell at resistance", "exit at resistance", "target resistance", "sell into a resistance")
    ):
        add(
            "Structure/resistance profit target",
            dimension="exit",
            modeled_override=False,
            limitation="The backtester does not yet track a source-defined resistance level as a dynamic profit target.",
        )

    if any(phrase in exit_text for phrase in ("trailing stop", "trail the stop", "trail stop")):
        add(
            "Trailing-stop exit",
            ("trailing_stop_pct",),
            dimension="exit",
            limitation="A trailing stop needs an explicit percentage or a safely compiled research assumption.",
        )
    if any(
        phrase in exit_text
        for phrase in (
            "break even",
            "breakeven",
            "break-even",
            "move stop to entry",
            "move the stop to entry",
        )
    ):
        breakeven_keys = (
            ("move_stop_to_breakeven_after_scale_out",)
            if rules.get("move_stop_to_breakeven_after_scale_out") is True
            else ("move_stop_to_breakeven_at_r",)
        )
        add(
            "Move stop to breakeven",
            breakeven_keys,
            dimension="exit",
            limitation=(
                "The backtester needs either an explicit R trigger or an explicit instruction "
                "to move to breakeven after a partial exit."
            ),
        )
    if any(
        phrase in exit_text
        for phrase in ("exit below vwap", "lose vwap", "loses vwap", "breaks below vwap", "closes below vwap")
    ):
        add(
            "Exit when VWAP is lost",
            ("exit_below_vwap",),
            dimension="exit",
        )
    if any(
        phrase in exit_text
        for phrase in ("exit below the ema", "lose the ema", "loses the ema", "close below the ema", "closes below the ema")
    ):
        add(
            "Exit when the fast EMA is lost",
            ("fast_ema_period", "exit_below_fast_ema"),
            dimension="exit",
        )
    if any(phrase in exit_text for phrase in ("scale out", "scaling out", "partial profit", "take partial")):
        scale_keys = (
            ("scale_out_stages",)
            if rules.get("scale_out_stages")
            else ("scale_out_fraction_pct", "scale_out_at_r")
        )
        add(
            "Scale-out / partial-profit management",
            scale_keys,
            dimension="exit",
            limitation=(
                "Partial exits require explicit stage fractions and triggers. When the source "
                "omits them, the Lab may test clearly labeled research assumptions."
            ),
        )

    if "vwap" in exit_text and any(
        phrase in exit_text
        for phrase in ("trail under vwap", "trail below vwap", "trail the remainder under vwap", "trail the rest under vwap")
    ):
        add(
            "Trail remainder beneath VWAP",
            ("trail_below_vwap",),
            dimension="exit",
        )
    if "ema" in exit_text and any(
        phrase in exit_text
        for phrase in ("trail under the ema", "trail below the ema", "trail the remainder under the ema", "trail the rest under the ema")
    ):
        add(
            "Trail remainder beneath fast EMA",
            ("fast_ema_period", "trail_below_fast_ema"),
            dimension="exit",
        )
    if "anchored vwap" in exit_text and any(
        phrase in exit_text
        for phrase in ("trail under", "trail below", "trail the remainder", "trail the rest")
    ):
        add(
            "Trail remainder beneath anchored VWAP",
            ("avwap_anchor_mode", "trail_below_avwap"),
            dimension="exit",
        )
    if any(
        phrase in exit_text
        for phrase in ("momentum fades", "momentum fade", "momentum failure", "sell into strength", "selling into strength")
    ):
        add(
            "Discretionary momentum/strength exit",
            dimension="exit",
            modeled_override=False,
            limitation="Historical OHLCV alone does not yet reproduce the source's discretionary momentum-exit decision.",
        )

    if any(
        phrase in text
        for phrase in ("high of day break", "high-of-day break", "hod break", "break the high of day")
    ):
        add(
            "High-of-day breakout trigger",
            dimension="structure",
            modeled_override=False,
            limitation="The current generic lookback breakout is not the same as a causal session high-of-day breakout.",
        )
    if "premarket high" in text and any(
        phrase in text for phrase in ("break", "breakout", "over", "above")
    ):
        add(
            "Premarket-high breakout trigger",
            dimension="structure",
            modeled_override=False,
            limitation="The backtester does not yet expose the premarket session high as a dedicated breakout level.",
        )
    if any(phrase in text for phrase in ("short interest", "heavily shorted", "short squeeze")):
        add(
            "Historical short-interest / squeeze context",
            dimension="universe",
            modeled_override=False,
            limitation="Point-in-time historical short-interest data is not currently part of the backtest dataset.",
        )
    explicit_gap_pct = (
        re.search(
            r"(?:premarket\s+gap|gap(?:ped)?\s+up|gapper)[^\.]{0,60}\b\d+(?:\.\d+)?\s*%",
            text,
        )
        or re.search(
            r"\b\d+(?:\.\d+)?\s*%[^\.]{0,60}(?:premarket\s+gap|gap(?:ped)?\s+up|gapper)",
            text,
        )
    )
    if explicit_gap_pct:
        add(
            "Premarket gap-percentage filter",
            dimension="universe",
            modeled_override=False,
            limitation="The current day-change rule is not a dedicated prior-close-to-premarket-gap measurement.",
        )

    # Universe / execution requirements that the current historical dataset cannot reproduce.
    if any(phrase in text for phrase in ("low float", "low-float", "float under", "share float")):
        add(
            "Historical float filter",
            dimension="universe",
            modeled_override=False,
            limitation="Point-in-time historical float data is not currently part of the backtest dataset.",
        )
    if any(
        phrase in text
        for phrase in ("level 2", "level ii", "order book", "tape speed", "time and sales", "tape reading")
    ):
        add(
            "Level-2 / tape-reading confirmation",
            dimension="execution",
            modeled_override=False,
            limitation="Historical order-book/tape state is not currently available to the deterministic backtester.",
        )
    if any(phrase in text for phrase in ("anchored vwap", "avwap")):
        avwap_mode = rules.get("avwap_anchor_mode")
        add(
            "Anchored VWAP structure",
            ("avwap_anchor_mode",),
            dimension="structure",
            modeled_override=bool(avwap_mode),
            limitation="A causal AVWAP anchor has not yet been identified for this source strategy.",
        )
        if avwap_mode in {"swing_low", "swing_high", "higher_low_handoff", "lower_high_handoff"}:
            add(
                "Causal AVWAP pivot confirmation",
                ("avwap_pivot_confirm_bars",),
                dimension="structure",
                limitation="Swing/handoff anchors require an explicit or research-assumption confirmation window.",
            )
        if "rising avwap" in text or "declining avwap" in text:
            add("AVWAP trend direction", ("require_avwap_rising",), dimension="structure")
        if "reclaim" in text:
            add("Anchored VWAP reclaim", ("avwap_reclaim",), dimension="entry")
        if any(phrase in text for phrase in ("pullback", "pull back", "support")):
            add("Anchored VWAP pullback/support", ("require_avwap_pullback",), dimension="entry")
            add("Objective AVWAP pullback tolerance", ("avwap_pullback_tolerance_pct",), dimension="entry")
        if any(phrase in text for phrase in ("compression", "pinch", "multiple anchored vwap", "multiple avwap")):
            add(
                "Multi-anchor AVWAP compression structure",
                dimension="structure",
                modeled_override=False,
                limitation="AVWAP v1 models one causal anchor at a time; multi-anchor pinch/compression logic is intentionally not approximated.",
            )
        if any(phrase in text for phrase in ("ipo day-one", "ipo day one", "first trading day of an ipo")):
            add(
                "Historical IPO day-one context",
                dimension="universe",
                modeled_override=False,
                limitation="The historical engine does not yet have point-in-time IPO listing-date context.",
            )
        if any(phrase in text for phrase in ("multi-day avwap", "multi day avwap", "day two")):
            add(
                "Multi-day AVWAP persistence",
                dimension="structure",
                modeled_override=False,
                limitation="AVWAP v1 intentionally resets supported anchors by session and does not yet carry an anchor across trading days.",
            )
    if any(phrase in text for phrase in ("proprietary indicator", "custom indicator", "private indicator")):
        add(
            "Proprietary/custom indicator",
            dimension="structure",
            modeled_override=False,
            limitation="The source-defined proprietary indicator cannot be reproduced from the saved rule schema.",
        )

    total = len(requirements)
    modeled = sum(1 for item in requirements if item["modeled"])
    coverage = 100.0 if total == 0 else round(modeled / total * 100.0, 1)
    missing = [item for item in requirements if not item["modeled"]]
    critical_missing = [item for item in missing if item.get("critical")]
    dimension_summary: dict[str, dict[str, int]] = {}
    for item in requirements:
        bucket = dimension_summary.setdefault(
            str(item.get("dimension") or "other"),
            {"requirements": 0, "modeled": 0, "missing": 0},
        )
        bucket["requirements"] += 1
        if item["modeled"]:
            bucket["modeled"] += 1
        else:
            bucket["missing"] += 1

    return {
        "coverage_pct": coverage,
        "requirement_count": total,
        "modeled_count": modeled,
        "modeled_requirements": [item["label"] for item in requirements if item["modeled"]],
        "missing_requirements": [item["label"] for item in missing],
        "critical_missing_count": len(critical_missing),
        "critical_missing_requirements": [item["label"] for item in critical_missing],
        "requirements": requirements,
        "dimension_summary": dimension_summary,
        "gate_pct": SEMANTIC_BACKTEST_COVERAGE_GATE,
    }


def strategy_integrity_report(strategy: dict[str, Any]) -> dict[str, Any]:
    """Return a plain-language source-to-backtester fidelity verdict."""
    semantic = strategy_semantic_coverage(strategy)
    coverage = safe_float(semantic.get("coverage_pct"), 0.0) or 0.0
    critical_missing = int(semantic.get("critical_missing_count") or 0)
    if critical_missing:
        status = "blocked"
        label = "IMPORTANT LOGIC NOT MODELED"
    elif semantic.get("requirement_count") and coverage < SEMANTIC_BACKTEST_COVERAGE_GATE:
        status = "partial"
        label = "PARTIALLY MODELED"
    else:
        status = "faithful"
        label = "FULLY MODELED FOR CURRENT REQUIREMENTS"

    return {
        "status": status,
        "label": label,
        "coverage_pct": coverage,
        "requirement_count": int(semantic.get("requirement_count") or 0),
        "modeled_count": int(semantic.get("modeled_count") or 0),
        "critical_missing_count": critical_missing,
        "missing_requirements": list(semantic.get("missing_requirements") or []),
        "critical_missing_requirements": list(semantic.get("critical_missing_requirements") or []),
        "requirements": list(semantic.get("requirements") or []),
        "dimension_summary": dict(semantic.get("dimension_summary") or {}),
        "gate_pct": semantic.get("gate_pct"),
        "note": (
            "This is a fidelity check, not a profitability score. It asks whether the historical "
            "backtester is actually executing the important strategy described by the source."
        ),
    }



PAPER_EXECUTION_UNSUPPORTED_DYNAMIC_EXITS = {
    "max_hold_minutes": "Maximum holding-time exit",
    "trailing_stop_pct": "Trailing-stop management",
    "move_stop_to_breakeven_at_r": "Move-to-breakeven management",
    "scale_out_fraction_pct": "Partial-profit / scale-out management",
    "scale_out_at_r": "Partial-profit / scale-out management",
    "scale_out_stages": "Multi-stage partial-profit management",
    "move_stop_to_breakeven_after_scale_out": "Post-partial breakeven management",
    "trail_below_vwap": "VWAP-trailing stop management",
    "trail_below_fast_ema": "Fast-EMA-trailing stop management",
    "trail_below_avwap": "Anchored-VWAP-trailing stop management",
    "exit_below_vwap": "VWAP-loss exit",
    "exit_below_fast_ema": "Fast-EMA-loss exit",
    "exit_below_avwap": "Anchored-VWAP-loss exit",
}


def paper_execution_fidelity(strategy: dict[str, Any]) -> dict[str, Any]:
    """Check whether Paper Auto can reproduce the deterministic research lifecycle.

    The research backtester is intraday-only: every surviving position is flattened
    at the final available candle of the session. The current Streamlit Paper Auto
    path submits an Alpaca bracket only when the user refreshes the page; it is not
    a persistent position manager and therefore cannot guarantee that session-end
    flatten, timed exits, or other dynamic management will occur.
    """
    effective = effective_strategy_for_live(strategy)
    rules = normalize_machine_rules(effective.get("machine_rules"))
    unsupported = [
        label
        for rule_name, label in PAPER_EXECUTION_UNSUPPORTED_DYNAMIC_EXITS.items()
        if rules.get(rule_name) is not None and rules.get(rule_name) is not False
    ]
    if rules.get("avwap_anchor_mode") is not None:
        unsupported.append("Anchored VWAP signal/management parity")

    # This is a universal backtest/live mismatch today, not a strategy-specific
    # optional feature: the deterministic engine never carries overnight.
    unsupported.append("Guaranteed end-of-session flattening")
    unsupported = list(dict.fromkeys(unsupported))

    if rules.get("reward_risk") is None:
        unsupported.append("Fixed bracket profit target")

    return {
        "status": "blocked",
        "label": "PAPER EXECUTION DOES NOT FULLY MATCH BACKTEST",
        "unsupported_management": list(dict.fromkeys(unsupported)),
        "reason": (
            "The current Paper Auto path is an entry helper, not a persistent trade manager. "
            "It cannot yet guarantee the same intraday position lifecycle used by the backtester, "
            "including mandatory session-end flattening"
            + (
                " and the strategy-specific management listed above."
                if len(unsupported) > 1
                else "."
            )
        ),
        "research_backtest_forces_session_flat": True,
        "paper_runner_persistent_manager": False,
    }


def research_readiness(strategy: dict[str, Any]) -> dict[str, Any]:
    """Describe whether a strategy is mechanically testable without implying that it has edge."""
    effective = effective_strategy_for_research(strategy)
    semantic = strategy_semantic_coverage(strategy)
    rules = {
        key: value
        for key, value in normalize_machine_rules(effective.get("machine_rules")).items()
        if value is not None
    }
    # Readiness must count only rules the historical signal evaluator actually
    # enforces. Market-spread limits require quote history and therefore fail
    # closed instead of being treated as executable via a fixed-cost proxy.
    non_entry_fields = {
        "stop_loss_pct",
        "reward_risk",
        "max_hold_minutes",
        "trailing_stop_pct",
        "move_stop_to_breakeven_at_r",
        "scale_out_fraction_pct",
        "scale_out_at_r",
        "scale_out_stages",
        "move_stop_to_breakeven_after_scale_out",
        "trail_below_vwap",
        "trail_below_fast_ema",
        "trail_below_avwap",
        "exit_below_vwap",
        "exit_below_fast_ema",
        "exit_below_avwap",
        "max_spread_pct",
        "stop_below_avwap",
        "stop_below_fast_ema",
        "fast_ema_period",
        "slow_ema_period",
        "trend_ema_period",
        "avwap_anchor_mode",
        "avwap_pivot_confirm_bars",
        "avwap_anchor_session_minute",
        "avwap_pullback_tolerance_pct",
        "pullback_touch_tolerance_pct",
        "stop_avwap_buffer_pct",
        "stop_ema_buffer_pct",
    }
    false_is_meaningful = {
        "above_vwap",
        "require_price_above_avwap",
        "require_avwap_rising",
        "require_price_above_fast_ema",
        "require_price_above_slow_ema",
        "require_price_above_trend_ema",
    }
    entry_rules = [
        key
        for key, value in rules.items()
        if key not in non_entry_fields
        and not (isinstance(value, bool) and value is False and key not in false_is_meaningful)
    ]
    unsupported_rules = [
        key for key in ("max_spread_pct",)
        if rules.get(key) is not None
    ]
    evidence_count = len(
        [item for item in strategy.get("evidence") or [] if isinstance(item, dict)]
    )
    unresolved_count = len([item for item in strategy.get("unresolved_rules") or [] if str(item).strip()])
    assumption_count = len(
        [
            value
            for value in normalize_machine_rules(strategy.get("research_rule_overrides")).values()
            if value is not None
        ]
    )
    explicit_count = len(
        [
            value
            for value in normalize_machine_rules(strategy.get("machine_rules")).values()
            if value is not None
        ]
    )

    score = 10.0
    score += min(45.0, len(entry_rules) * 12.0)
    score += min(20.0, evidence_count * 4.0)
    score += min(15.0, explicit_count * 3.0)
    if assumption_count:
        score += min(10.0, assumption_count * 2.0)
    score -= min(25.0, unresolved_count * 2.5)
    if semantic["requirement_count"]:
        score = score * 0.65 + semantic["coverage_pct"] * 0.35
    score = round(max(0.0, min(100.0, score)), 1)

    if not entry_rules:
        label = "needs_translation"
        note = "No objective entry/filter rule is available to the deterministic backtester yet."
    elif evidence_count == 0 and str(strategy.get("source_type") or "").lower() == "book_or_document":
        label = "needs_evidence_review"
        note = "Machine rules exist, but no source evidence reference was retained for this document strategy."
    elif unsupported_rules:
        label = "partially_modeled"
        note = (
            "At least one defining rule is not enforceable with the historical data used by "
            "the deterministic backtester: " + ", ".join(unsupported_rules) + "."
        )
    elif semantic.get("critical_missing_count"):
        label = "partially_modeled"
        note = (
            "At least one defining source requirement is not represented by the deterministic "
            "backtester. Treating this as the original strategy would be misleading."
        )
    elif (
        semantic["requirement_count"]
        and semantic["coverage_pct"] < SEMANTIC_BACKTEST_COVERAGE_GATE
    ):
        label = "partially_modeled"
        note = (
            "The backtester can enforce some rules, but too much of the setup's defining logic "
            "is still missing from the executable model."
        )
    elif unresolved_count >= max(3, len(entry_rules) * 2):
        label = "partially_testable"
        note = "The strategy can be backtested, but many source requirements remain qualitative or unavailable."
    else:
        label = "ready_for_backtest"
        note = (
            "The strategy has objective entry/filter rules and enough of its defining setup "
            "is represented by the deterministic backtester."
        )

    return {
        "label": label,
        "score": score,
        "entry_rule_count": len(entry_rules),
        "explicit_rule_count": explicit_count,
        "research_assumption_count": assumption_count,
        "evidence_count": evidence_count,
        "unresolved_count": unresolved_count,
        "semantic_coverage_pct": semantic["coverage_pct"],
        "semantic_requirement_count": semantic["requirement_count"],
        "semantic_modeled_count": semantic["modeled_count"],
        "semantic_modeled_requirements": semantic["modeled_requirements"],
        "semantic_missing_requirements": semantic["missing_requirements"],
        "semantic_critical_missing_count": semantic.get("critical_missing_count", 0),
        "semantic_critical_missing_requirements": semantic.get("critical_missing_requirements", []),
        "semantic_dimension_summary": semantic.get("dimension_summary", {}),
        "semantic_coverage_gate_pct": semantic["gate_pct"],
        "note": note,
    }


def _assumption_test_values(rule_name: str, value: Any) -> list[Any]:
    """Build a small, auditable neighborhood around an AI research assumption.

    These are hypotheses for the optimizer, not author-attributed rules. Every
    candidate is normalized through the machine-rule schema before it can be
    tested.
    """
    try:
        parsed = coerce_machine_rule_value(rule_name, value)
    except AppError:
        return []

    if isinstance(parsed, bool):
        return [parsed]

    if isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
        numeric = float(parsed)
        raw_candidates: list[float] = []
        if numeric == 0:
            raw_candidates = [0.0, 0.1, 0.25, 0.5]
        else:
            factors = (0.50, 0.75, 1.00, 1.25, 1.50)
            raw_candidates = [numeric * factor for factor in factors]

        candidates: list[Any] = []
        for raw in raw_candidates:
            try:
                normalized = coerce_machine_rule_value(rule_name, raw)
            except AppError:
                continue
            if normalized not in candidates:
                candidates.append(normalized)
        return candidates[:5]

    return [parsed]


def apply_compiler_suggestions(
    strategy: dict[str, Any],
    compiled: dict[str, Any],
    *,
    minimum_confidence: float = 65.0,
) -> dict[str, Any]:
    """Auto-apply defensible compiler suggestions as research assumptions, never as author rules."""
    item = dict(strategy or {})
    explicit = normalize_machine_rules(item.get("machine_rules"))
    current_overrides = {
        key: value
        for key, value in normalize_machine_rules(item.get("research_rule_overrides")).items()
        if value is not None
    }
    ai_candidate_options = {
        str(key): list(values)
        for key, values in (item.get("ai_candidate_rule_options") or {}).items()
        if isinstance(values, list)
    }
    assumption_log = list(item.get("compiler_assumptions") or [])
    applied: list[dict[str, Any]] = []
    skipped_low_confidence = 0

    for suggestion in compiled.get("suggestions") or []:
        if not isinstance(suggestion, dict):
            continue
        confidence = max(0.0, min(100.0, safe_float(suggestion.get("confidence"), 0.0) or 0.0))
        if confidence < float(minimum_confidence):
            skipped_low_confidence += 1
            continue
        target = str(suggestion.get("target_rule") or "").strip()
        if target not in MACHINE_RULE_SCHEMA["properties"]:
            continue
        if explicit.get(target) is not None:
            continue
        value = suggestion.get("parsed_value")
        if value is None:
            try:
                value = coerce_machine_rule_value(target, suggestion.get("proposed_value"))
            except AppError:
                continue
        current_overrides[target] = value

        # Keep AI-generated hypotheses separate from exact source-supported
        # alternatives. The optimizer tests the AI seed plus a small nearby
        # neighborhood before generic parameter exploration.
        test_values = _assumption_test_values(target, value)
        existing_values = ai_candidate_options.setdefault(target, [])
        for candidate_value in test_values:
            if candidate_value not in existing_values:
                existing_values.append(candidate_value)
        ai_candidate_options[target] = existing_values[:7]

        record = {
            "target_rule": target,
            "value": value,
            "test_values": list(ai_candidate_options.get(target) or []),
            "source_requirement": suggestion.get("source_requirement"),
            "rationale": suggestion.get("rationale"),
            "confidence": confidence,
            "accepted_at": compiled.get("generated_at") or _utc_iso(),
            "model": compiled.get("model"),
            "accepted_by": "ai_autopilot",
            "is_research_assumption": True,
            "test_policy": "optimizer_then_walk_forward_holdout",
        }
        applied.append(record)

        # Keep the log idempotent if Streamlit reruns the same compiler result.
        signature = (
            str(record.get("target_rule") or ""),
            repr(record.get("value")),
            str(record.get("source_requirement") or ""),
            str(record.get("model") or ""),
        )
        prior_signatures = {
            (
                str(existing.get("target_rule") or ""),
                repr(existing.get("value")),
                str(existing.get("source_requirement") or ""),
                str(existing.get("model") or ""),
            )
            for existing in assumption_log
            if isinstance(existing, dict)
        }
        if signature not in prior_signatures:
            assumption_log.append(record)

    if applied:
        item["research_rule_overrides"] = current_overrides
        item["ai_candidate_rule_options"] = ai_candidate_options
        item["compiler_assumptions"] = assumption_log[-150:]
        # Any executable-rule change invalidates a previously frozen validation result.
        item["validation_status"] = "unvalidated"
        item["optimization_status"] = "not_run"
        item.pop("validated_rules", None)
        item.pop("validated_backtest_settings", None)
        item.pop("validated_at", None)

    item["autopilot_preparation"] = {
        "prepared_at": _utc_iso(),
        "model": compiled.get("model"),
        "compiler_summary": compiled.get("summary") or "",
        "suggestions_considered": len(compiled.get("suggestions") or []),
        "suggestions_auto_applied": len(applied),
        "assumption_rules_queued_for_testing": len(
            {
                str(record.get("target_rule") or "")
                for record in applied
                if record.get("target_rule")
            }
        ),
        "assumption_test_values_queued": sum(
            len(record.get("test_values") or [])
            for record in applied
        ),
        "minimum_confidence": float(minimum_confidence),
        "skipped_low_confidence": skipped_low_confidence,
        "unmapped_requirements": list(compiled.get("unmapped_requirements") or []),
    }
    item["research_readiness"] = research_readiness(item)
    return item


def prepare_strategies_with_ai(
    strategies: list[dict[str, Any]],
    compiler: GeminiRuleCompiler,
    *,
    minimum_confidence: float = 65.0,
    progress_callback=None,
) -> list[dict[str, Any]]:
    """Run the Rule Compiler across extracted strategies so manual proxy selection is optional."""
    prepared: list[dict[str, Any]] = []
    total = len(strategies)
    for index, strategy in enumerate(strategies, start=1):
        if progress_callback:
            progress_callback(index, total, str(strategy.get("name") or "Unnamed strategy"))

        readiness_before = research_readiness(strategy)
        if readiness_before.get("label") == "ready_for_backtest":
            # The Rule Compiler's purpose is to fill testability gaps. Calling Gemini again
            # for a strategy that already has an enforceable entry/filter rule adds latency
            # and quota usage without being necessary to start research.
            item = dict(strategy)
            item["autopilot_preparation"] = {
                "prepared_at": _utc_iso(),
                "model": "",
                "compiler_skipped": True,
                "skip_reason": "Already machine-testable from source-extracted rules.",
                "suggestions_auto_applied": 0,
            }
            item["research_readiness"] = readiness_before
            prepared.append(item)
            continue

        try:
            compiled = compiler.compile(strategy)
            item = apply_compiler_suggestions(
                strategy,
                compiled,
                minimum_confidence=minimum_confidence,
            )
        except Exception as exc:
            item = dict(strategy)
            item["autopilot_preparation"] = {
                "prepared_at": _utc_iso(),
                "model": getattr(compiler, "model", ""),
                "error": str(exc),
                "suggestions_auto_applied": 0,
            }
            item["research_readiness"] = research_readiness(item)
        prepared.append(item)
    return prepared


class GeminiBookAnalyzer:
    """Chunked document extractor: 3.6 bulk reader, 3.7 specialist, 3.5/2.5 reliability fallbacks."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_GEMINI_BOOK_MODEL,
        *,
        fallback_api_key: str = "",
        fallback_model: str = "gemini-3.5-flash",
        specialist_model: str = DEFAULT_GEMINI_BOOK_SPECIALIST_MODEL,
    ):
        key = str(api_key or "").strip()
        if not key:
            raise AppError("Add GEMINI_API_KEY to Streamlit Secrets before analyzing a book or document.")
        self.api_key = key
        self.fallback_api_key = str(fallback_api_key or "").strip()
        if self.fallback_api_key and self.fallback_api_key == self.api_key:
            raise AppError(
                "GEMINI_PAID_API_KEY must be a different key from a separate Google project."
            )
        self.model = str(model or DEFAULT_GEMINI_BOOK_MODEL).strip() or DEFAULT_GEMINI_BOOK_MODEL
        self.primary_model = self.model
        self.specialist_model = (
            str(specialist_model or DEFAULT_GEMINI_BOOK_SPECIALIST_MODEL).strip()
            or DEFAULT_GEMINI_BOOK_SPECIALIST_MODEL
        )
        fallback_candidates = [
            str(fallback_model or "").strip(),
            *DEFAULT_GEMINI_BOOK_FALLBACK_MODELS,
        ]
        self.fallback_models: list[str] = []
        for candidate in fallback_candidates:
            candidate = str(candidate or "").strip()
            if (
                candidate
                and candidate != self.primary_model
                and candidate != self.specialist_model
                and candidate not in self.fallback_models
            ):
                self.fallback_models.append(candidate)
        self._fallback_model_index = 0
        self.model_fallback_used = False
        self.specialist_used = False
        self.specialist_sections: list[int] = []
        self.paid_fallback_used = False

    @property
    def headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _emit_progress(callback, index: int, total: int, message: str) -> None:
        if callback is None:
            return
        try:
            callback(index, total, message)
        except TypeError:
            callback(index, total)

    def _activate_model_fallback(self, error: Exception | str) -> bool:
        if not provider_temporarily_unavailable(error):
            return False
        while self._fallback_model_index < len(self.fallback_models):
            candidate = self.fallback_models[self._fallback_model_index]
            self._fallback_model_index += 1
            if candidate == self.model:
                continue
            self.model = candidate
            self.model_fallback_used = True
            return True
        return False

    def _activate_paid_fallback(self, error: Exception | str) -> bool:
        if (
            self.paid_fallback_used
            or not self.fallback_api_key
            or not provider_quota_reached(error)
        ):
            return False
        self.api_key = self.fallback_api_key
        self.paid_fallback_used = True
        return True

    def _activate_quota_model_fallback(self, error: Exception | str) -> bool:
        """Try another model immediately when the current model's request quota is exhausted."""
        if not provider_quota_reached(error):
            return False
        while self._fallback_model_index < len(self.fallback_models):
            candidate = self.fallback_models[self._fallback_model_index]
            self._fallback_model_index += 1
            if candidate == self.model:
                continue
            self.model = candidate
            self.model_fallback_used = True
            return True
        return False

    @staticmethod
    def _quota_retry_delay(message: str) -> int | None:
        lowered = message.lower()
        if not any(marker in lowered for marker in ("rate limit", "quota", "resource_exhausted", "usage")):
            return None
        if any(marker in lowered for marker in ("per day", "per_day", "perday", "daily")):
            return None
        match = re.search(
            r"retry\s+in\s+([0-9]+(?:\.[0-9]+)?)\s*s(?:ec(?:ond)?s?)?\b",
            message,
            re.IGNORECASE,
        )
        if match is None:
            return 5
        seconds = max(1, int(float(match.group(1)) + 0.999))
        return seconds if seconds <= BOOK_QUOTA_MAX_WAIT_SECONDS else None

    @staticmethod
    def _cache_directory(source_id: str, focus: str) -> Path:
        root = Path(os.environ.get("YOUTUBE_STRATEGY_DATA_DIR", ".youtube_strategy_data"))
        focus_hash = hashlib.sha256(str(focus or "").encode("utf-8")).hexdigest()[:16]
        return root / "trading-intelligence-book-cache" / (
            f"v{BOOK_ANALYSIS_CACHE_VERSION}-{source_id}-{focus_hash}"
        )

    @staticmethod
    def _read_cached_chunk(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        analysis = payload.get("analysis")
        return analysis if isinstance(analysis, dict) else None

    @staticmethod
    def _write_cached_chunk(
        path: Path,
        *,
        analysis: dict[str, Any],
        model: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        payload = {
            "analysis": analysis,
            "model": model,
            "saved_at": _utc_iso(),
        }
        temporary.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _clear_cache(directory: Path) -> None:
        if not directory.exists():
            return
        for path in directory.glob("section-*.json"):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass

    def _analyze_chunk(
        self,
        chunk: str,
        *,
        title: str,
        author: str,
        chunk_number: int,
        chunk_count: int,
        focus: str = "",
    ) -> dict[str, Any]:
        model_name = self.model.removeprefix("models/")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", model_name):
            raise AppError("GEMINI_MODEL must contain a valid Gemini model name.")

        prompt = (
            BOOK_EXTRACTION_PROMPT
            + f"\n\nSource title: {title or 'Untitled source'}"
            + f"\nSource author/creator: {author or 'Unknown'}"
            + f"\nThis is chunk {chunk_number} of {chunk_count}."
        )
        if focus.strip():
            prompt += "\nUser research focus: " + focus.strip()[:3000]
        prompt += "\n\nSOURCE CHUNK:\n" + chunk

        generation_config: dict[str, Any] = {
            "responseFormat": {
                "text": {"mimeType": "APPLICATION_JSON", "schema": BOOK_ANALYSIS_SCHEMA}
            }
        }
        # Book extraction is mostly structured reading rather than open-ended problem solving.
        # Use low thinking on Gemini 3.x to reduce latency and timeout risk. Gemini 2.5 uses
        # its own thinking-budget parameter, so leave that family on its API default.
        if model_name.startswith("gemini-3"):
            generation_config["thinkingConfig"] = {"thinkingLevel": "low"}

        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        response = _json_request(
            f"{GEMINI_GENERATE_CONTENT_URL}/{model_name}:generateContent",
            self.headers,
            method="POST",
            payload=payload,
            timeout=300,
        )
        raw = _extract_generate_content_text(response)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AppError("Gemini returned document analysis that was not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise AppError("Gemini returned an unexpected document-analysis format.")
        return parsed

    @staticmethod
    def _analysis_needs_specialist(analysis: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        strategies = [
            item for item in analysis.get("strategies") or []
            if isinstance(item, dict)
        ]
        if not strategies:
            return False, reasons

        confidences = [
            safe_float(item.get("confidence"), 100.0) or 0.0
            for item in strategies
        ]
        low_confidence = [
            item for item, confidence in zip(strategies, confidences)
            if confidence < BOOK_SPECIALIST_CONFIDENCE_THRESHOLD
        ]
        unresolved_values = [
            str(value).strip()
            for item in strategies
            for value in item.get("unresolved_rules") or []
            if str(value).strip()
        ]
        unresolved_count = len(unresolved_values)
        conflict_markers = (
            "conflict",
            "contradict",
            "different explicit",
            "unclear",
            "ambiguous",
            "cannot determine",
        )
        conflict_like = [
            value
            for value in unresolved_values
            if any(marker in value.casefold() for marker in conflict_markers)
        ]
        weakly_quantified = [
            item
            for item in strategies
            if (item.get("entry_conditions") or item.get("risk_rules"))
            and not any(
                value is not None
                for value in normalize_machine_rules(item.get("machine_rules")).values()
            )
        ]
        evidence_gaps = [
            item for item in strategies
            if (item.get("entry_conditions") or item.get("exit_conditions"))
            and not [e for e in item.get("evidence") or [] if isinstance(e, dict)]
        ]
        average_confidence = sum(confidences) / len(confidences) if confidences else 100.0

        if low_confidence:
            reasons.append(f"{len(low_confidence)} low-confidence strategy extraction(s)")
        if unresolved_count >= BOOK_SPECIALIST_UNRESOLVED_THRESHOLD:
            reasons.append(f"{unresolved_count} unresolved source requirements")
        if conflict_like:
            reasons.append(f"{len(conflict_like)} conflicting or ambiguous source requirement(s)")
        if weakly_quantified and (unresolved_count >= 2 or average_confidence < 80.0):
            reasons.append(
                f"{len(weakly_quantified)} difficult-to-formalize strategy extraction(s)"
            )
        if evidence_gaps and (low_confidence or unresolved_count >= 2):
            reasons.append(f"{len(evidence_gaps)} strategy extraction(s) missing retained evidence")

        return bool(reasons), reasons

    def _specialist_review_chunk(
        self,
        chunk: str,
        *,
        primary_analysis: dict[str, Any],
        title: str,
        author: str,
        chunk_number: int,
        chunk_count: int,
        focus: str,
        progress_callback,
    ) -> dict[str, Any]:
        needs_specialist, reasons = self._analysis_needs_specialist(primary_analysis)
        if not needs_specialist or self.specialist_model == self.primary_model:
            return primary_analysis

        previous_model = self.model
        previous_fallback_index = self._fallback_model_index
        self.model = self.specialist_model
        self._fallback_model_index = len(self.fallback_models)
        self._emit_progress(
            progress_callback,
            chunk_number,
            chunk_count,
            f"Section {chunk_number} needs deeper interpretation ({'; '.join(reasons)}). "
            f"Escalating just this section to {self.specialist_model}…",
        )
        try:
            specialist = self._analyze_chunk(
                chunk,
                title=title,
                author=author,
                chunk_number=chunk_number,
                chunk_count=chunk_count,
                focus=focus,
            )
            self.specialist_used = True
            if chunk_number not in self.specialist_sections:
                self.specialist_sections.append(chunk_number)
            return self._merge_chunk_analyses([primary_analysis, specialist])
        except AppError as exc:
            self._emit_progress(
                progress_callback,
                chunk_number,
                chunk_count,
                f"{self.specialist_model} could not complete the specialist review. "
                f"Keeping the successful {self.primary_model} extraction for section {chunk_number}.",
            )
            return primary_analysis
        finally:
            self.model = previous_model
            self._fallback_model_index = previous_fallback_index

    def _analyze_chunk_resilient(
        self,
        chunk: str,
        *,
        title: str,
        author: str,
        chunk_number: int,
        chunk_count: int,
        focus: str,
        progress_callback,
    ) -> dict[str, Any]:
        transient_attempts = 0
        quota_attempts = 0

        while True:
            try:
                return self._analyze_chunk(
                    chunk,
                    title=title,
                    author=author,
                    chunk_number=chunk_number,
                    chunk_count=chunk_count,
                    focus=focus,
                )
            except AppError as exc:
                message = str(exc)

                if provider_temporarily_unavailable(exc):
                    if transient_attempts < BOOK_TRANSIENT_RETRIES_PER_MODEL:
                        retry_wait = min(
                            5 * (2 ** transient_attempts),
                            BOOK_TRANSIENT_MAX_WAIT_SECONDS,
                        )
                        transient_attempts += 1
                        self._emit_progress(
                            progress_callback,
                            chunk_number,
                            chunk_count,
                            f"Gemini {self.model} is temporarily overloaded. Retrying section "
                            f"{chunk_number} of {chunk_count} in {retry_wait}s "
                            f"({transient_attempts}/{BOOK_TRANSIENT_RETRIES_PER_MODEL})…",
                        )
                        sleep(retry_wait)
                        continue

                    failed_model = self.model
                    if self._activate_model_fallback(exc):
                        transient_attempts = 0
                        quota_attempts = 0
                        self._emit_progress(
                            progress_callback,
                            chunk_number,
                            chunk_count,
                            f"{failed_model} is still overloaded. Switching section "
                            f"{chunk_number} of {chunk_count} to backup model {self.model}…",
                        )
                        continue

                if provider_quota_reached(exc):
                    # Do not burn a full provider cooldown before trying resources that may
                    # have independent quota. Preserve the preferred model by trying the
                    # separately configured paid-project key first, then alternate models.
                    if self._activate_paid_fallback(exc):
                        transient_attempts = 0
                        quota_attempts = 0
                        self._emit_progress(
                            progress_callback,
                            chunk_number,
                            chunk_count,
                            f"Gemini request limit reached. Switching section {chunk_number} of "
                            f"{chunk_count} immediately to the backup API project…",
                        )
                        continue

                    failed_model = self.model
                    if self._activate_quota_model_fallback(exc):
                        transient_attempts = 0
                        quota_attempts = 0
                        self._emit_progress(
                            progress_callback,
                            chunk_number,
                            chunk_count,
                            f"{failed_model} request limit reached. Trying section {chunk_number} of "
                            f"{chunk_count} immediately with backup model {self.model}…",
                        )
                        continue

                    retry_delay = self._quota_retry_delay(message)
                    if retry_delay is not None and quota_attempts < BOOK_QUOTA_RETRIES:
                        quota_attempts += 1
                        # After all immediate alternatives are exhausted, cool down once and
                        # retry the preferred model. Repeated minute-long waits made book
                        # ingestion appear hung and did not improve durable progress.
                        self._reset_model_fallback_chain()
                        self._emit_progress(
                            progress_callback,
                            chunk_number,
                            chunk_count,
                            f"All Gemini routes are temporarily rate-limited. Cooling down once for "
                            f"{retry_delay}s, then retrying section {chunk_number} of {chunk_count}…",
                        )
                        sleep(retry_delay)
                        continue

                raise

    @staticmethod
    def _timeout_like(error: Exception | str) -> bool:
        message = str(error).lower()
        return any(
            marker in message
            for marker in (
                "timed out",
                "timeout",
                "provider could not be reached",
                "connection reset",
                "connection aborted",
                "remote end closed connection",
            )
        )

    def _reset_model_fallback_chain(self) -> None:
        self.model = self.primary_model
        self._fallback_model_index = 0

    @staticmethod
    def _split_chunk_for_retry(chunk: str) -> list[str]:
        text = str(chunk or "").strip()
        if len(text) < 2:
            return [text] if text else []
        midpoint = len(text) // 2
        candidates = []
        for separator in ("\n\n", "\n", ". "):
            left = text.rfind(separator, max(0, midpoint - 5000), midpoint + 1)
            right = text.find(separator, midpoint, min(len(text), midpoint + 5000))
            if left > 0:
                candidates.append(left + len(separator))
            if right > 0:
                candidates.append(right + len(separator))
        split_at = min(candidates, key=lambda value: abs(value - midpoint)) if candidates else midpoint
        overlap = min(BOOK_ADAPTIVE_SPLIT_OVERLAP, max(0, split_at // 8))
        first = text[:split_at].strip()
        second = text[max(0, split_at - overlap):].strip()
        return [piece for piece in (first, second) if piece]

    @classmethod
    def _merge_chunk_analyses(cls, analyses: list[dict[str, Any]]) -> dict[str, Any]:
        summaries: list[str] = []
        detected_title = ""
        detected_author = ""
        strategies_by_key: dict[str, dict[str, Any]] = {}
        for analysis in analyses:
            summary = str(analysis.get("source_summary") or "").strip()
            if summary and summary not in summaries:
                summaries.append(summary)
            if not detected_title:
                detected_title = str(analysis.get("detected_title") or "").strip()
            if not detected_author:
                detected_author = str(analysis.get("detected_author") or "").strip()
            for raw in analysis.get("strategies") or []:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("name") or "Unnamed strategy").strip()
                category = str(raw.get("category") or "Uncategorized").strip()
                key = re.sub(r"[^a-z0-9]+", " ", f"{name} {category}".casefold()).strip()
                if not key:
                    continue
                if key in strategies_by_key:
                    strategies_by_key[key] = cls._merge_strategy(strategies_by_key[key], raw)
                else:
                    strategies_by_key[key] = dict(raw)
        return {
            "source_summary": " ".join(summaries)[:12000],
            "detected_title": detected_title,
            "detected_author": detected_author,
            "strategies": list(strategies_by_key.values()),
        }

    def _analyze_chunk_with_adaptive_split(
        self,
        chunk: str,
        *,
        title: str,
        author: str,
        chunk_number: int,
        chunk_count: int,
        focus: str,
        progress_callback,
        split_depth: int = 0,
    ) -> dict[str, Any]:
        try:
            primary_analysis = self._analyze_chunk_resilient(
                chunk,
                title=title,
                author=author,
                chunk_number=chunk_number,
                chunk_count=chunk_count,
                focus=focus,
                progress_callback=progress_callback,
            )
            return self._specialist_review_chunk(
                chunk,
                primary_analysis=primary_analysis,
                title=title,
                author=author,
                chunk_number=chunk_number,
                chunk_count=chunk_count,
                focus=focus,
                progress_callback=progress_callback,
            )
        except AppError as exc:
            if (
                not self._timeout_like(exc)
                or split_depth >= BOOK_ADAPTIVE_SPLIT_MAX_DEPTH
                or len(chunk) < BOOK_ADAPTIVE_SPLIT_MIN_CHARACTERS
            ):
                raise

            pieces = self._split_chunk_for_retry(chunk)
            if len(pieces) < 2:
                raise
            self._emit_progress(
                progress_callback,
                chunk_number,
                chunk_count,
                f"Section {chunk_number} is still timing out. Splitting it into "
                f"{len(pieces)} smaller pieces automatically…",
            )
            analyses: list[dict[str, Any]] = []
            for piece_index, piece in enumerate(pieces, start=1):
                self._reset_model_fallback_chain()
                self._emit_progress(
                    progress_callback,
                    chunk_number,
                    chunk_count,
                    f"Analyzing smaller piece {piece_index} of {len(pieces)} for "
                    f"section {chunk_number}…",
                )
                analyses.append(
                    self._analyze_chunk_with_adaptive_split(
                        piece,
                        title=title,
                        author=author,
                        chunk_number=chunk_number,
                        chunk_count=chunk_count,
                        focus=focus,
                        progress_callback=progress_callback,
                        split_depth=split_depth + 1,
                    )
                )
            return self._merge_chunk_analyses(analyses)

    @staticmethod
    def _merge_string_lists(left: list[Any], right: list[Any], maximum: int = 120) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in list(left or []) + list(right or []):
            text = str(value or "").strip()
            marker = text.casefold()
            if text and marker not in seen:
                result.append(text)
                seen.add(marker)
            if len(result) >= maximum:
                break
        return result

    @classmethod
    def _merge_strategy(cls, previous: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = dict(previous)
        for field in (
            "indicators",
            "entry_conditions",
            "exit_conditions",
            "risk_rules",
            "avoid_conditions",
            "market_context",
            "stock_selection",
            "unresolved_rules",
        ):
            merged[field] = cls._merge_string_lists(previous.get(field) or [], incoming.get(field) or [])

        evidence = list(previous.get("evidence") or [])
        seen = {
            (str(item.get("location") or ""), str(item.get("description") or ""))
            for item in evidence
            if isinstance(item, dict)
        }
        for item in incoming.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("location") or ""), str(item.get("description") or ""))
            if key not in seen:
                evidence.append(item)
                seen.add(key)
        merged["evidence"] = evidence[:180]

        old_rules = normalize_machine_rules(previous.get("machine_rules"))
        new_rules = normalize_machine_rules(incoming.get("machine_rules"))
        conflicts: list[str] = []
        for key, value in new_rules.items():
            if value is None:
                continue
            if old_rules.get(key) is None:
                old_rules[key] = value
            elif old_rules.get(key) != value:
                conflicts.append(
                    f"Source gives different explicit values for {key}: "
                    f"{old_rules.get(key)} versus {value}. Review evidence before testing."
                )
        merged["machine_rules"] = old_rules
        merged["unresolved_rules"] = cls._merge_string_lists(
            merged.get("unresolved_rules") or [], conflicts
        )

        old_summary = str(previous.get("summary") or "").strip()
        new_summary = str(incoming.get("summary") or "").strip()
        if new_summary and new_summary.casefold() not in old_summary.casefold():
            merged["summary"] = (old_summary + " " + new_summary).strip()[:6000]
        merged["confidence"] = max(
            safe_float(previous.get("confidence"), 0.0) or 0.0,
            safe_float(incoming.get("confidence"), 0.0) or 0.0,
        )
        return merged

    def analyze(
        self,
        text: str,
        *,
        title: str,
        author: str = "",
        focus: str = "",
        progress_callback=None,
        checkpoint_callback=None,
        resume_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resume_version = (
            int(safe_float(resume_state.get("checkpoint_version"), 0) or 0)
            if isinstance(resume_state, dict)
            else 0
        )
        resume_completed = (
            int(safe_float(resume_state.get("completed_sections"), 0) or 0)
            if isinstance(resume_state, dict)
            else 0
        )
        # Preserve the old 28k section map only when a previous run actually completed
        # something. Zero-progress runs (the common quota-failure case) immediately benefit
        # from the new larger-section plan.
        legacy_resume = (
            resume_version == LEGACY_BOOK_ANALYSIS_CACHE_VERSION
            and resume_completed > 0
        )
        if legacy_resume:
            chunk_target = LEGACY_CHUNK_CHARACTERS
            chunk_overlap = LEGACY_CHUNK_OVERLAP
            active_checkpoint_version = LEGACY_BOOK_ANALYSIS_CACHE_VERSION
        else:
            # Gemini's context window is much larger than our old 28k-character chunks.
            # Aim for at most ~6 primary requests per book and let adaptive splitting handle
            # the rare section that is genuinely too large or slow.
            dynamic_target = (len(text) + BOOK_TARGET_MAX_CHUNKS - 1) // BOOK_TARGET_MAX_CHUNKS
            chunk_target = max(DEFAULT_CHUNK_CHARACTERS, dynamic_target)
            chunk_overlap = DEFAULT_CHUNK_OVERLAP
            active_checkpoint_version = BOOK_ANALYSIS_CACHE_VERSION

        chunks = chunk_source_text(
            text,
            target_chars=chunk_target,
            overlap_chars=chunk_overlap,
        )
        if not chunks:
            raise AppError("There was no readable source text to analyze.")
        # Source identity must survive title/author edits between resumed runs.
        # Metadata can improve display labels, but the same content remains the same source.
        cache_source_id = source_fingerprint("", "", text)
        cache_directory = self._cache_directory(cache_source_id, focus)
        strategies_by_key: dict[str, dict[str, Any]] = {}
        summaries: list[str] = []
        detected_titles: list[str] = []
        detected_authors: list[str] = []
        completed_section_indices: set[int] = set()
        completed_sections = 0
        models_used: list[str] = []
        failed_sections: dict[int, str] = {}
        quota_circuit_open = False

        if isinstance(resume_state, dict):
            resume_chunk_count = int(safe_float(resume_state.get("chunk_count"), 0) or 0)
            if (
                resume_version == active_checkpoint_version
                and resume_chunk_count == len(chunks)
            ):
                for value in resume_state.get("completed_section_indices") or []:
                    index = int(safe_float(value, 0) or 0)
                    if 1 <= index <= len(chunks):
                        completed_section_indices.add(index)
                completed_sections = len(completed_section_indices)

                existing_summary = str(resume_state.get("summary") or "").strip()
                if existing_summary:
                    summaries.append(existing_summary)
                existing_title = str(
                    resume_state.get("detected_title")
                    or resume_state.get("title")
                    or ""
                ).strip()
                existing_author = str(
                    resume_state.get("detected_author")
                    or resume_state.get("author")
                    or ""
                ).strip()
                if existing_title and existing_title != "Uploaded source":
                    detected_titles.append(existing_title)
                if existing_author:
                    detected_authors.append(existing_author)
                for raw in resume_state.get("strategies") or []:
                    if not isinstance(raw, dict):
                        continue
                    name = str(raw.get("name") or "Unnamed strategy").strip()
                    category = str(raw.get("category") or "Uncategorized").strip()
                    key = re.sub(r"[^a-z0-9]+", " ", f"{name} {category}".casefold()).strip()
                    if key:
                        strategies_by_key[key] = dict(raw)

        def build_snapshot() -> dict[str, Any]:
            resolved_title = str(title or "").strip() or (
                detected_titles[0] if detected_titles else "Uploaded source"
            )
            resolved_author = str(author or "").strip() or (
                detected_authors[0] if detected_authors else ""
            )
            # Keep the source/strategy identity stable across checkpoints even if Gemini
            # learns the real title/author later in the book.
            source_id = cache_source_id
            strategies = [
                canonicalize_strategy(
                    item,
                    source_id=source_id,
                    source_type="book_or_document",
                    source_title=resolved_title,
                    source_author=resolved_author,
                )
                for item in strategies_by_key.values()
            ]
            return {
                "id": source_id,
                "source_type": "book_or_document",
                "source_claim_status": "unverified_source_claim",
                "source_role": "hypothesis_generator",
                "title": resolved_title,
                "author": resolved_author,
                "detected_title": detected_titles[0] if detected_titles else "",
                "detected_author": detected_authors[0] if detected_authors else "",
                "summary": " ".join(summaries)[:12000],
                "analyzed_at": _utc_iso(),
                "model": self.model,
                "primary_model": self.primary_model,
                "specialist_model": self.specialist_model,
                "specialist_used": self.specialist_used,
                "specialist_sections": sorted(self.specialist_sections),
                "models_used": models_used or [self.model],
                "model_fallback_used": self.model_fallback_used,
                "paid_fallback_used": self.paid_fallback_used,
                "chunk_count": len(chunks),
                "checkpoint_version": active_checkpoint_version,
                "chunk_target_characters": chunk_target,
                "completed_section_indices": sorted(completed_section_indices),
                "completed_sections": completed_sections,
                "analysis_incomplete": (
                    completed_sections < len(chunks) or bool(failed_sections)
                ),
                "failed_sections": [
                    {"section": index, "error": message}
                    for index, message in sorted(failed_sections.items())
                ],
                "strategies": strategies,
            }

        def emit_checkpoint() -> None:
            if checkpoint_callback is None or completed_sections <= 0:
                return
            checkpoint_callback(build_snapshot())

        def consume_analysis(analysis: dict[str, Any]) -> None:
            if self.model not in models_used:
                models_used.append(self.model)

            summary = str(analysis.get("source_summary") or "").strip()
            if summary and summary not in summaries:
                summaries.append(summary)
            detected_title = str(analysis.get("detected_title") or "").strip()
            detected_author = str(analysis.get("detected_author") or "").strip()
            if detected_title and detected_title not in detected_titles:
                detected_titles.append(detected_title)
            if detected_author and detected_author not in detected_authors:
                detected_authors.append(detected_author)

            for raw in analysis.get("strategies") or []:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("name") or "Unnamed strategy").strip()
                category = str(raw.get("category") or "Uncategorized").strip()
                key = re.sub(r"[^a-z0-9]+", " ", f"{name} {category}".casefold()).strip()
                if not key:
                    continue
                if key in strategies_by_key:
                    strategies_by_key[key] = self._merge_strategy(strategies_by_key[key], raw)
                else:
                    strategies_by_key[key] = dict(raw)

        def analyze_section(index: int, chunk: str, *, retry_pass: bool = False) -> bool:
            nonlocal completed_sections, quota_circuit_open
            if not retry_pass and index in completed_section_indices:
                self._emit_progress(
                    progress_callback,
                    index,
                    len(chunks),
                    f"Using durable saved section {index} of {len(chunks)}…",
                )
                return True

            cache_path = cache_directory / f"section-{index:03d}.json"
            if not retry_pass:
                cached = self._read_cached_chunk(cache_path)
                if cached is not None:
                    if index not in completed_section_indices:
                        completed_section_indices.add(index)
                        completed_sections = len(completed_section_indices)
                    self._emit_progress(
                        progress_callback,
                        index,
                        len(chunks),
                        f"Resuming saved section {index} of {len(chunks)}…",
                    )
                    consume_analysis(cached)
                    failed_sections.pop(index, None)
                    emit_checkpoint()
                    return True

            if retry_pass:
                self._reset_model_fallback_chain()
                self._emit_progress(
                    progress_callback,
                    index,
                    len(chunks),
                    f"Automatically retrying previously failed section {index} of {len(chunks)}…",
                )
            else:
                self._emit_progress(
                    progress_callback,
                    index,
                    len(chunks),
                    f"Analyzing source section {index} of {len(chunks)} with {self.model}…",
                )

            try:
                analysis = self._analyze_chunk_with_adaptive_split(
                    chunk,
                    title=title,
                    author=author,
                    chunk_number=index,
                    chunk_count=len(chunks),
                    focus=focus,
                    progress_callback=progress_callback,
                )
            except AppError as exc:
                failed_sections[index] = str(exc)
                if provider_quota_reached(exc):
                    quota_circuit_open = True
                    self._emit_progress(
                        progress_callback,
                        index,
                        len(chunks),
                        f"All Gemini routes are still rate-limited after failover/cooldown. "
                        f"Pausing at section {index} of {len(chunks)} instead of wasting more requests. "
                        "Any completed sections are already saved.",
                    )
                else:
                    self._emit_progress(
                        progress_callback,
                        index,
                        len(chunks),
                        f"Section {index} is still unavailable. Saving progress and continuing "
                        "with the rest of the source before one final automatic retry…",
                    )
                return False

            self._write_cached_chunk(
                cache_path,
                analysis=analysis,
                model=self.model,
            )
            if index not in completed_section_indices:
                completed_section_indices.add(index)
                completed_sections = len(completed_section_indices)
            failed_sections.pop(index, None)
            consume_analysis(analysis)
            emit_checkpoint()
            return True

        for index, chunk in enumerate(chunks, start=1):
            analyze_section(index, chunk)
            if quota_circuit_open:
                break

        if failed_sections and not quota_circuit_open:
            self._emit_progress(
                progress_callback,
                min(failed_sections),
                len(chunks),
                f"Retrying {len(failed_sections)} temporarily failed source "
                f"section{'s' if len(failed_sections) != 1 else ''} automatically…",
            )
            for index in list(failed_sections):
                analyze_section(index, chunks[index - 1], retry_pass=True)

        if completed_sections == 0:
            details = next(iter(failed_sections.values()), "Gemini did not return any usable sections.")
            if quota_circuit_open:
                raise AppError(
                    "The book upload and PDF text extraction succeeded, but every configured Gemini route "
                    "is currently rate-limited. The app stopped after one cooldown instead of repeatedly "
                    f"waiting through the whole book. Latest provider error: {details}"
                )
            raise AppError(
                "Gemini could not analyze any part of this source after model fallback, smaller-section "
                f"retry, and a second pass. Latest provider error: {details}"
            )

        result = build_snapshot()
        result["analysis_incomplete"] = bool(failed_sections)
        if not failed_sections:
            self._clear_cache(cache_directory)
        return result


def merge_ingestion_checkpoint_strategies(
    existing: list[dict[str, Any]],
    additions: list[dict[str, Any]],
    *,
    source_id: str,
    replace_source: bool,
) -> list[dict[str, Any]]:
    """Keep progressive book checkpoints current without clobbering completed curated strategies."""
    source_id = str(source_id or "")
    if not replace_source:
        return merge_strategies(existing, additions)

    retained = [
        dict(item)
        for item in existing
        if isinstance(item, dict)
        and str(item.get("source_id") or "") != source_id
    ]
    incoming = [
        dict(item)
        for item in additions
        if isinstance(item, dict) and item.get("id")
    ]
    return retained + incoming


def merge_strategies(
    existing: list[dict[str, Any]],
    additions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {str(item.get("id")): dict(item) for item in existing if isinstance(item, dict) and item.get("id")}
    for item in additions:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        prior = by_id.get(str(item["id"]))
        if prior:
            preserved = dict(prior)
            preserved["latest_extraction"] = dict(item)
            preserved["latest_extracted_at"] = _utc_iso()
            by_id[str(item["id"])] = preserved
        else:
            by_id[str(item["id"])] = dict(item)
    return list(by_id.values())



def effective_strategy_for_live(strategy: dict[str, Any]) -> dict[str, Any]:
    """Use frozen validated rules live; otherwise use current reviewed research rules."""
    item = effective_strategy_for_research(strategy)
    validated = item.get("validated_rules")
    if (
        str(item.get("validation_status") or "").lower() == "validated"
        and isinstance(validated, dict)
    ):
        item["machine_rules"] = normalize_machine_rules(validated)
        item["using_validated_rules"] = True
    else:
        item["using_validated_rules"] = False
    return item
