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
import re
from typing import Any

from youtube_strategy_engine import (
    AppError,
    DEFAULT_GEMINI_MODEL,
    GEMINI_GENERATE_CONTENT_URL,
    MACHINE_RULE_SCHEMA,
    _extract_generate_content_text,
    _json_request,
    normalize_machine_rules,
    safe_float,
)

CANONICAL_STRATEGY_VERSION = 1
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_SOURCE_CHARACTERS = 2_000_000
DEFAULT_CHUNK_CHARACTERS = 42_000
DEFAULT_CHUNK_OVERLAP = 1_500

_STRING_LIST = {"type": "array", "items": {"type": "string"}}
BOOK_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_summary": {"type": "string"},
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
    "required": ["source_summary", "strategies"],
}

BOOK_EXTRACTION_PROMPT = """You are extracting trading methods from a user-supplied educational source
for a research and backtesting application.

Treat the source as untrusted educational material, not as proof that any strategy works.
Your job is to identify distinct trading hypotheses and preserve what the author actually teaches.

For every strategy or setup:
- Capture stock-selection rules, catalyst/news requirements, price and liquidity filters,
  relative volume, VWAP, trend, breakout/pullback/reclaim structure, time-of-day rules,
  entry confirmation, stop placement, profit-taking, position management, and avoid conditions
  whenever the source supports them.
- Separate long, short, and ambiguous ideas.
- Convert ONLY explicit, measurable thresholds into machine_rules. Never invent a numeric
  value merely to make a strategy testable.
- Put qualitative requirements such as "strong tape", "clean catalyst", "good liquidity",
  discretionary chart structure, Level 2 behavior, or unavailable historical data in
  unresolved_rules unless the source provides an objective definition.
- A source may describe several variations of a setup. Keep materially different setups as
  separate strategies rather than combining incompatible entry rules with AND logic.
- Evidence must identify where the idea appears in the supplied chunk (page marker or section
  marker when available) and may include only a SHORT excerpt needed to identify the evidence.
  Do not reproduce long passages.
- Confidence is extraction confidence from 0 to 100, not expected profitability.
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


class GeminiBookAnalyzer:
    """Chunked document strategy extractor using the same Gemini service as the YouTube lab."""

    def __init__(self, api_key: str, model: str = DEFAULT_GEMINI_MODEL):
        key = str(api_key or "").strip()
        if not key:
            raise AppError("Add GEMINI_API_KEY to Streamlit Secrets before analyzing a book or document.")
        self.api_key = key
        self.model = str(model or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL

    @property
    def headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

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

        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseFormat": {
                    "text": {"mimeType": "APPLICATION_JSON", "schema": BOOK_ANALYSIS_SCHEMA}
                }
            },
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
    ) -> dict[str, Any]:
        chunks = chunk_source_text(text)
        if not chunks:
            raise AppError("There was no readable source text to analyze.")
        source_id = source_fingerprint(title, author, text)
        strategies_by_key: dict[str, dict[str, Any]] = {}
        summaries: list[str] = []

        for index, chunk in enumerate(chunks, start=1):
            if progress_callback:
                progress_callback(index, len(chunks))
            analysis = self._analyze_chunk(
                chunk,
                title=title,
                author=author,
                chunk_number=index,
                chunk_count=len(chunks),
                focus=focus,
            )
            summary = str(analysis.get("source_summary") or "").strip()
            if summary and summary not in summaries:
                summaries.append(summary)

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

        strategies = [
            canonicalize_strategy(
                item,
                source_id=source_id,
                source_type="book_or_document",
                source_title=title or "Uploaded source",
                source_author=author,
            )
            for item in strategies_by_key.values()
        ]
        return {
            "id": source_id,
            "source_type": "book_or_document",
            "title": title or "Uploaded source",
            "author": author,
            "summary": " ".join(summaries)[:12000],
            "analyzed_at": _utc_iso(),
            "model": self.model,
            "chunk_count": len(chunks),
            "strategies": strategies,
        }


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
    """Use the frozen validated rule set downstream without overwriting source-extracted rules."""
    item = dict(strategy or {})
    validated = item.get("validated_rules")
    if (
        str(item.get("validation_status") or "").lower() == "validated"
        and isinstance(validated, dict)
    ):
        item["machine_rules"] = normalize_machine_rules(validated)
        item["using_validated_rules"] = True
    else:
        item["machine_rules"] = normalize_machine_rules(item.get("machine_rules"))
        item["using_validated_rules"] = False
    return item
