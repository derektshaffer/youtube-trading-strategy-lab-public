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

CANONICAL_STRATEGY_VERSION = 1
BOOK_ANALYSIS_CACHE_VERSION = 2
BOOK_TRANSIENT_RETRIES_PER_MODEL = 3
BOOK_TRANSIENT_MAX_WAIT_SECONDS = 30
BOOK_QUOTA_RETRIES = 2
BOOK_QUOTA_MAX_WAIT_SECONDS = 90
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_SOURCE_CHARACTERS = 2_000_000
DEFAULT_CHUNK_CHARACTERS = 42_000
DEFAULT_CHUNK_OVERLAP = 1_500

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
                        "enum": list(MACHINE_RULE_SCHEMA["properties"].keys()),
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
- Keep tape-reading, Level 2, float, borrow, proprietary indicators, subjective catalyst quality,
  and other unsupported concepts in unmapped_requirements unless an existing rule is a defensible proxy.
- The user must explicitly accept suggestions later. Nothing here changes the strategy automatically.

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
                    retry_delay = GeminiBookAnalyzer._quota_retry_delay(message)
                    if retry_delay is not None and quota_attempts < BOOK_QUOTA_RETRIES:
                        quota_attempts += 1
                        sleep(retry_delay)
                        continue
                    if self._activate_paid_fallback(exc):
                        transient_attempts = 0
                        quota_attempts = 0
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


def research_readiness(strategy: dict[str, Any]) -> dict[str, Any]:
    """Describe whether a strategy is mechanically testable without implying that it has edge."""
    effective = effective_strategy_for_research(strategy)
    rules = {
        key: value
        for key, value in normalize_machine_rules(effective.get("machine_rules")).items()
        if value is not None
    }
    non_entry_fields = {
        "stop_loss_pct",
        "reward_risk",
        "max_hold_minutes",
    }
    entry_rules = [key for key in rules if key not in non_entry_fields]
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
    score = round(max(0.0, min(100.0, score)), 1)

    if not entry_rules:
        label = "needs_translation"
        note = "No objective entry/filter rule is available to the deterministic backtester yet."
    elif evidence_count == 0 and str(strategy.get("source_type") or "").lower() == "book_or_document":
        label = "needs_evidence_review"
        note = "Machine rules exist, but no source evidence reference was retained for this document strategy."
    elif unresolved_count > max(6, len(entry_rules) * 3):
        label = "partially_testable"
        note = "The strategy can be backtested, but many source requirements remain qualitative or unavailable."
    else:
        label = "ready_for_backtest"
        note = "The strategy has at least one objective entry/filter rule that the backtester can enforce."

    return {
        "label": label,
        "score": score,
        "entry_rule_count": len(entry_rules),
        "explicit_rule_count": explicit_count,
        "research_assumption_count": assumption_count,
        "evidence_count": evidence_count,
        "unresolved_count": unresolved_count,
        "note": note,
    }


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
        record = {
            "target_rule": target,
            "value": value,
            "source_requirement": suggestion.get("source_requirement"),
            "rationale": suggestion.get("rationale"),
            "confidence": confidence,
            "accepted_at": compiled.get("generated_at") or _utc_iso(),
            "model": compiled.get("model"),
            "accepted_by": "ai_autopilot",
            "is_research_assumption": True,
        }
        applied.append(record)
        assumption_log.append(record)

    if applied:
        item["research_rule_overrides"] = current_overrides
        item["compiler_assumptions"] = assumption_log[-150:]
        # Any executable-rule change invalidates a previously frozen validation result.
        item["validation_status"] = "unvalidated"
        item.pop("validated_rules", None)
        item.pop("validated_backtest_settings", None)
        item.pop("validated_at", None)

    item["autopilot_preparation"] = {
        "prepared_at": _utc_iso(),
        "model": compiled.get("model"),
        "compiler_summary": compiled.get("summary") or "",
        "suggestions_considered": len(compiled.get("suggestions") or []),
        "suggestions_auto_applied": len(applied),
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
    """Chunked document strategy extractor with retry, fallback, and section resume."""

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
            raise AppError("Add GEMINI_API_KEY to Streamlit Secrets before analyzing a book or document.")
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

                retry_delay = self._quota_retry_delay(message)
                if (
                    provider_quota_reached(exc)
                    and retry_delay is not None
                    and quota_attempts < BOOK_QUOTA_RETRIES
                ):
                    quota_attempts += 1
                    self._emit_progress(
                        progress_callback,
                        chunk_number,
                        chunk_count,
                        f"Gemini request limit reached. Retrying section {chunk_number} of "
                        f"{chunk_count} in {retry_delay}s ({quota_attempts}/{BOOK_QUOTA_RETRIES})…",
                    )
                    sleep(retry_delay)
                    continue

                if self._activate_paid_fallback(exc):
                    transient_attempts = 0
                    quota_attempts = 0
                    self._emit_progress(
                        progress_callback,
                        chunk_number,
                        chunk_count,
                        f"Free Gemini quota reached. Continuing section {chunk_number} of "
                        f"{chunk_count} with the backup API key…",
                    )
                    continue

                raise

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
        cache_source_id = source_fingerprint(title, author, text)
        cache_directory = self._cache_directory(cache_source_id, focus)
        strategies_by_key: dict[str, dict[str, Any]] = {}
        summaries: list[str] = []
        detected_titles: list[str] = []
        detected_authors: list[str] = []
        completed_sections = 0
        models_used: list[str] = []

        for index, chunk in enumerate(chunks, start=1):
            cache_path = cache_directory / f"section-{index:03d}.json"
            cached = self._read_cached_chunk(cache_path)
            if cached is not None:
                analysis = cached
                completed_sections += 1
                self._emit_progress(
                    progress_callback,
                    index,
                    len(chunks),
                    f"Resuming saved section {index} of {len(chunks)}…",
                )
            else:
                self._emit_progress(
                    progress_callback,
                    index,
                    len(chunks),
                    f"Analyzing source section {index} of {len(chunks)} with {self.model}…",
                )
                try:
                    analysis = self._analyze_chunk_resilient(
                        chunk,
                        title=title,
                        author=author,
                        chunk_number=index,
                        chunk_count=len(chunks),
                        focus=focus,
                        progress_callback=progress_callback,
                    )
                except AppError as exc:
                    message = str(exc)
                    saved = index - 1
                    if saved:
                        message += (
                            f" {saved} completed section{'s' if saved != 1 else ''} "
                            f"{'were' if saved != 1 else 'was'} saved. "
                            "Press Analyze again with the same source/title/focus to resume."
                        )
                    raise AppError(
                        f"Section {index} of {len(chunks)} could not be analyzed: {message}"
                    ) from exc
                self._write_cached_chunk(
                    cache_path,
                    analysis=analysis,
                    model=self.model,
                )
                completed_sections += 1

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

        resolved_title = str(title or "").strip() or (
            detected_titles[0] if detected_titles else "Uploaded source"
        )
        resolved_author = str(author or "").strip() or (
            detected_authors[0] if detected_authors else ""
        )
        source_id = source_fingerprint(resolved_title, resolved_author, text)
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
        result = {
            "id": source_id,
            "source_type": "book_or_document",
            "title": resolved_title,
            "author": resolved_author,
            "detected_title": detected_titles[0] if detected_titles else "",
            "detected_author": detected_authors[0] if detected_authors else "",
            "summary": " ".join(summaries)[:12000],
            "analyzed_at": _utc_iso(),
            "model": self.model,
            "primary_model": self.primary_model,
            "models_used": models_used or [self.model],
            "model_fallback_used": self.model_fallback_used,
            "paid_fallback_used": self.paid_fallback_used,
            "chunk_count": len(chunks),
            "completed_sections": completed_sections,
            "strategies": strategies,
        }
        self._clear_cache(cache_directory)
        return result


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
