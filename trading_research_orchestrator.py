"""Persistent autonomous research orchestration for Trading Intelligence Lab.

This module separates three different jobs that should never be conflated:

1. Gemini Flash performs high-volume grounded web research and proposes hypotheses.
2. Gemini Pro reviews difficult/conflicting hypotheses and decides whether they are
   worth deterministic testing. Pro never marks a strategy validated.
3. The deterministic Trading Lab backtester/validator decides whether an idea has
   historical evidence strong enough to advance.

The durable queue lives inside the private strategy library so cloud workers can
continue while the Streamlit session and the user's computer are offline.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from typing import Any

from youtube_strategy_engine import (
    AppError,
    GEMINI_INTERACTIONS_URL,
    MACHINE_RULE_SCHEMA,
    _extract_interaction_text,
    _json_request,
    normalize_machine_rules,
    provider_model_unavailable,
    provider_quota_reached,
    provider_temporarily_unavailable,
    safe_float,
)

UTC = timezone.utc

DEFAULT_GEMINI_BULK_RESEARCH_MODEL = "gemini-3.7-flash"
DEFAULT_GEMINI_BULK_FALLBACK_MODEL = "gemini-3.6-flash"
DEFAULT_GEMINI_SPECIALIST_MODEL = "gemini-3.1-pro-preview"
DEFAULT_GEMINI_SPECIALIST_FALLBACK_MODEL = "gemini-2.5-pro"

DEFAULT_RESEARCH_TOPICS = (
    "Small-cap momentum: relative volume, liquidity, float, and continuation behavior",
    "Catalyst momentum: news type, timing, volume response, and intraday follow-through",
    "VWAP reclaim and VWAP pullback behavior in high-relative-volume stocks",
    "EMA pullback continuation: trend quality, pullback depth, and breakout confirmation",
    "Opening-range and prior-day-high breakouts: confirmation versus failed breakouts",
    "Volume acceleration and price expansion: when rising volume predicts continuation",
    "Market regime dependence: when momentum rules change across volatility and index regimes",
    "Execution reality: spread, slippage, liquidity, and position-size effects on momentum edges",
    "Cross-stock generalization: which stock characteristics make the same setup behave similarly",
    "Time-of-day effects in intraday momentum and catalyst-driven stocks",
)

SOURCE_TYPE_SCORES = {
    "primary_regulatory": 95,
    "exchange_official": 92,
    "academic_peer_reviewed": 90,
    "academic_preprint": 82,
    "official_company": 78,
    "institutional_research": 76,
    "reputable_financial_media": 68,
    "practitioner_education": 52,
    "forum_social": 28,
    "unknown": 35,
}

SOURCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "url": {"type": "string"},
        "source_type": {
            "type": "string",
            "enum": list(SOURCE_TYPE_SCORES),
        },
        "published_at": {"type": "string"},
        "support_summary": {"type": "string"},
    },
    "required": ["id", "title", "url", "source_type", "published_at", "support_summary"],
}

HYPOTHESIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "category": {"type": "string"},
        "direction": {"type": "string", "enum": ["long", "short", "both", "unclear"]},
        "statement": {"type": "string"},
        "why_it_might_work": {"type": "string"},
        "market_scope": {"type": "string"},
        "machine_rules": MACHINE_RULE_SCHEMA,
        "unresolved_rules": {"type": "array", "items": {"type": "string"}},
        "supporting_source_ids": {"type": "array", "items": {"type": "string"}},
        "contradicting_source_ids": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "novelty": {"type": "number"},
    },
    "required": [
        "name",
        "category",
        "direction",
        "statement",
        "why_it_might_work",
        "market_scope",
        "machine_rules",
        "unresolved_rules",
        "supporting_source_ids",
        "contradicting_source_ids",
        "confidence",
        "novelty",
    ],
}

GROUNDED_RESEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "sources": {"type": "array", "items": SOURCE_SCHEMA},
        "hypotheses": {"type": "array", "items": HYPOTHESIS_SCHEMA},
        "contradictions": {"type": "array", "items": {"type": "string"}},
        "follow_up_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "title",
        "summary",
        "sources",
        "hypotheses",
        "contradictions",
        "follow_up_questions",
    ],
}

SPECIALIST_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["promote_to_validation", "keep_researching", "reject"],
        },
        "reason": {"type": "string"},
        "confidence": {"type": "number"},
        "revised_hypothesis": HYPOTHESIS_SCHEMA,
        "risk_flags": {"type": "array", "items": {"type": "string"}},
        "follow_up_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "decision",
        "reason",
        "confidence",
        "revised_hypothesis",
        "risk_flags",
        "follow_up_questions",
    ],
}

GROUNDING_SYSTEM_PROMPT = """You are the high-throughput research worker for a quantitative
trading research lab. Use Google Search to investigate the assigned topic.

Research rules:
- Prefer primary, regulatory, exchange, academic, official-company, and institutional sources.
- Practitioner articles and forums may generate ideas but are weak evidence. Label them honestly.
- Do not treat source agreement as proof of profitability.
- Separate empirical evidence from opinion and marketing.
- Look for contradictory evidence, regime dependence, survivorship bias, look-ahead bias,
  data-snooping risk, liquidity limitations, and execution-cost limitations.
- Turn claims into TESTABLE hypotheses where possible.
- Only put a value in machine_rules when the searched evidence explicitly supports a measurable
  threshold or structural boolean. Never invent numbers just to make a hypothesis testable.
- The deterministic backtester, untouched holdout, walk-forward testing, cost stress, parameter
  stability, and cross-stock validation decide whether an idea works. You do not validate strategies.
- Focus on evidence that could improve intraday momentum/catalyst research.
"""

SPECIALIST_SYSTEM_PROMPT = """You are the specialist reasoning reviewer for a quantitative
trading research lab. You receive a grounded research packet produced by a faster research model.

Your role is adversarial:
- Find contradictions, weak causal stories, source-quality problems, hidden data-snooping,
  survivorship bias, regime dependence, execution assumptions, and over-specific thresholds.
- Consolidate duplicate ideas when appropriate.
- Preserve source-authored/measured thresholds; do not invent a numeric rule.
- A hypothesis may be promoted only to DETERMINISTIC VALIDATION, never directly to live trading.
- If evidence is promising but incomplete, keep researching and write specific follow-up questions.
- If the idea is not falsifiable or is mostly unsupported opinion, reject it.
"""


def utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def ensure_research_collections(library: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(library or {})
    for key in (
        "research_queue",
        "external_research_runs",
        "research_hypotheses",
        "research_worker_runs",
    ):
        if not isinstance(data.get(key), list):
            data[key] = []
    if not isinstance(data.get("research_system"), dict):
        data["research_system"] = {}
    return data


def source_quality_score(source: dict[str, Any]) -> int:
    kind = str(source.get("source_type") or "unknown").strip().lower()
    score = int(SOURCE_TYPE_SCORES.get(kind, SOURCE_TYPE_SCORES["unknown"]))
    url = str(source.get("url") or "").casefold()
    if any(domain in url for domain in (".gov/", "sec.gov", "federalreserve.gov")):
        score = max(score, 95)
    if any(domain in url for domain in (".edu/", "doi.org", "ssrn.com", "arxiv.org")):
        score = max(score, 80)
    if any(domain in url for domain in ("reddit.com", "x.com/", "twitter.com", "stocktwits.com")):
        score = min(score, 30)
    return max(0, min(100, score))


def _normalized_rules(raw: Any) -> dict[str, Any]:
    return {
        key: value
        for key, value in normalize_machine_rules(raw).items()
        if value is not None
    }


def _job_id(job_type: str, dedupe_key: str, created_at: str) -> str:
    material = f"{job_type}|{dedupe_key}|{created_at}".encode("utf-8")
    return "rq-" + hashlib.sha256(material).hexdigest()[:22]


def enqueue_research_job(
    library: dict[str, Any],
    job_type: str,
    payload: dict[str, Any] | None = None,
    *,
    priority: int = 50,
    dedupe_key: str = "",
    max_attempts: int = 3,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    data = ensure_research_collections(library)
    kind = str(job_type or "").strip()
    if kind not in {"web_research", "specialist_review", "autonomous_validation"}:
        raise AppError(f"Unsupported research job type: {kind or 'blank'}")
    dedupe = str(dedupe_key or "").strip()
    if dedupe:
        for existing in data["research_queue"]:
            if (
                str(existing.get("dedupe_key") or "") == dedupe
                and str(existing.get("status") or "") in {"queued", "running", "retry"}
            ):
                return data, None
    created_at = utc_iso()
    job = {
        "id": _job_id(kind, dedupe, created_at),
        "type": kind,
        "status": "queued",
        "priority": max(0, min(100, int(priority))),
        "created_at": created_at,
        "updated_at": created_at,
        "attempts": 0,
        "max_attempts": max(1, int(max_attempts)),
        "dedupe_key": dedupe,
        "payload": dict(payload or {}),
        "worker_id": None,
        "started_at": None,
        "completed_at": None,
        "next_attempt_at": None,
        "last_error": None,
        "result_ref": None,
    }
    data["research_queue"] = [job, *data["research_queue"]][:300]
    return data, job


def claim_next_research_job(
    library: dict[str, Any],
    worker_id: str,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    data = ensure_research_collections(library)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    eligible: list[dict[str, Any]] = []
    for job in data["research_queue"]:
        if str(job.get("status") or "") not in {"queued", "retry"}:
            continue
        next_at = _parse_iso(job.get("next_attempt_at"))
        if next_at and next_at > current:
            continue
        eligible.append(job)
    if not eligible:
        return data, None
    eligible.sort(
        key=lambda item: (
            int(item.get("priority") or 0),
            -int(item.get("attempts") or 0),
            str(item.get("created_at") or ""),
        ),
        reverse=True,
    )
    chosen_id = str(eligible[0].get("id") or "")
    claimed: dict[str, Any] | None = None
    now_text = current.isoformat().replace("+00:00", "Z")
    updated_queue: list[dict[str, Any]] = []
    for raw in data["research_queue"]:
        item = dict(raw)
        if str(item.get("id") or "") == chosen_id:
            item["status"] = "running"
            item["worker_id"] = str(worker_id or "cloud-worker")
            item["started_at"] = now_text
            item["updated_at"] = now_text
            item["attempts"] = int(item.get("attempts") or 0) + 1
            claimed = item
        updated_queue.append(item)
    data["research_queue"] = updated_queue
    return data, claimed


def finish_research_job(
    library: dict[str, Any],
    job_id: str,
    *,
    result_ref: str = "",
) -> dict[str, Any]:
    data = ensure_research_collections(library)
    now = utc_iso()
    data["research_queue"] = [
        {
            **item,
            "status": "complete",
            "updated_at": now,
            "completed_at": now,
            "last_error": None,
            "result_ref": str(result_ref or "") or None,
        }
        if str(item.get("id") or "") == str(job_id or "")
        else item
        for item in data["research_queue"]
    ]
    return data


def fail_research_job(
    library: dict[str, Any],
    job_id: str,
    error: Exception | str,
    *,
    retry_delay_minutes: int = 30,
) -> dict[str, Any]:
    data = ensure_research_collections(library)
    now = datetime.now(UTC)
    message = str(error)[:1800]
    updated: list[dict[str, Any]] = []
    for raw in data["research_queue"]:
        item = dict(raw)
        if str(item.get("id") or "") != str(job_id or ""):
            updated.append(item)
            continue
        attempts = int(item.get("attempts") or 0)
        max_attempts = int(item.get("max_attempts") or 3)
        retry = attempts < max_attempts
        item["status"] = "retry" if retry else "failed"
        item["updated_at"] = now.isoformat().replace("+00:00", "Z")
        item["last_error"] = message
        item["next_attempt_at"] = (
            (now + timedelta(minutes=max(1, int(retry_delay_minutes)))).isoformat().replace("+00:00", "Z")
            if retry else None
        )
        updated.append(item)
    data["research_queue"] = updated
    return data


def research_queue_status(library: dict[str, Any]) -> dict[str, int]:
    data = ensure_research_collections(library)
    counts = {name: 0 for name in ("queued", "running", "retry", "complete", "failed")}
    for item in data["research_queue"]:
        status = str(item.get("status") or "")
        if status in counts:
            counts[status] += 1
    counts["active"] = counts["queued"] + counts["running"] + counts["retry"]
    return counts


def seed_continuous_research_cycle(
    library: dict[str, Any],
    *,
    topics: tuple[str, ...] | list[str] = DEFAULT_RESEARCH_TOPICS,
    cycle_date: str | None = None,
    maximum_topics: int = 10,
) -> tuple[dict[str, Any], int]:
    data = ensure_research_collections(library)
    day = str(cycle_date or datetime.now(UTC).date().isoformat())
    system = dict(data.get("research_system") or {})
    if str(system.get("last_seeded_cycle") or "") == day:
        return data, 0
    added = 0
    for topic in list(topics)[: max(1, int(maximum_topics))]:
        data, job = enqueue_research_job(
            data,
            "web_research",
            {"topic": str(topic), "cycle_date": day, "origin": "continuous_research_cycle"},
            priority=55,
            dedupe_key=f"web:{day}:{str(topic).casefold()}",
        )
        if job:
            added += 1
    system["last_seeded_cycle"] = day
    system["last_seeded_at"] = utc_iso()
    system["topics_seeded"] = added
    system.setdefault("mode", "continuous")
    data["research_system"] = system
    return data, added


def _extract_step_sources(response: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            url = str(value.get("url") or value.get("uri") or "").strip()
            title = str(value.get("title") or value.get("display_name") or "").strip()
            if url.startswith(("http://", "https://")) and url not in seen:
                found.append({"url": url, "title": title})
                seen.add(url)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(response.get("steps") or [])
    return found[:80]


class GeminiResearchRouter:
    """Route cheap/high-volume research to Flash and difficult review to Pro."""

    def __init__(
        self,
        api_key: str,
        *,
        paid_api_key: str = "",
        bulk_model: str = DEFAULT_GEMINI_BULK_RESEARCH_MODEL,
        bulk_fallback_model: str = DEFAULT_GEMINI_BULK_FALLBACK_MODEL,
        specialist_model: str = DEFAULT_GEMINI_SPECIALIST_MODEL,
        specialist_fallback_model: str = DEFAULT_GEMINI_SPECIALIST_FALLBACK_MODEL,
    ):
        self.api_key = str(api_key or "").strip()
        self.paid_api_key = str(paid_api_key or "").strip()
        if not self.api_key:
            raise AppError("Add GEMINI_API_KEY before running autonomous web research.")
        if self.paid_api_key and self.paid_api_key == self.api_key:
            raise AppError("GEMINI_PAID_API_KEY must come from a separate paid Google project.")
        self.bulk_model = str(bulk_model or DEFAULT_GEMINI_BULK_RESEARCH_MODEL).strip()
        self.bulk_fallback_model = str(
            bulk_fallback_model or DEFAULT_GEMINI_BULK_FALLBACK_MODEL
        ).strip()
        self.specialist_model = str(
            specialist_model or DEFAULT_GEMINI_SPECIALIST_MODEL
        ).strip()
        self.specialist_fallback_model = str(
            specialist_fallback_model or DEFAULT_GEMINI_SPECIALIST_FALLBACK_MODEL
        ).strip()

    @staticmethod
    def _headers(key: str) -> dict[str, str]:
        return {
            "x-goog-api-key": key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _interaction(
        self,
        *,
        model: str,
        fallback_model: str,
        input_text: str,
        response_schema: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], str, bool]:
        models = [model]
        if fallback_model and fallback_model not in models:
            models.append(fallback_model)
        keys = [(self.api_key, False)]
        if self.paid_api_key:
            keys.append((self.paid_api_key, True))
        last_error: Exception | None = None
        for chosen_model in models:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", chosen_model.removeprefix("models/")):
                continue
            for key, paid_used in keys:
                payload: dict[str, Any] = {
                    "model": chosen_model,
                    "input": input_text,
                    "response_format": {
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": response_schema,
                    },
                    "store": False,
                }
                if tools:
                    payload["tools"] = tools
                try:
                    response = _json_request(
                        GEMINI_INTERACTIONS_URL,
                        self._headers(key),
                        method="POST",
                        payload=payload,
                        timeout=300,
                    )
                    if not isinstance(response, dict):
                        raise AppError("Gemini returned an unexpected research response.")
                    return response, chosen_model, paid_used
                except AppError as exc:
                    last_error = exc
                    if not (
                        provider_quota_reached(exc)
                        or provider_temporarily_unavailable(exc)
                        or provider_model_unavailable(exc)
                    ):
                        raise
                    continue
        if last_error:
            raise last_error
        raise AppError("No configured Gemini research model was available.")

    @staticmethod
    def _parse_structured_response(response: dict[str, Any], label: str) -> dict[str, Any]:
        text = _extract_interaction_text(response)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AppError(f"Gemini {label} returned invalid structured JSON.") from exc
        if not isinstance(parsed, dict):
            raise AppError(f"Gemini {label} returned an unexpected structured response.")
        return parsed

    def grounded_research(
        self,
        topic: str,
        *,
        existing_context: str = "",
    ) -> dict[str, Any]:
        focus = str(topic or "").strip()
        if not focus:
            raise AppError("Autonomous research needs a topic.")
        prompt = (
            GROUNDING_SYSTEM_PROMPT
            + "\n\nRESEARCH TOPIC:\n"
            + focus[:5000]
        )
        if str(existing_context or "").strip():
            prompt += "\n\nPRIOR LAB CONTEXT TO CHALLENGE, NOT ASSUME TRUE:\n" + str(existing_context)[:12000]
        response, model, paid_used = self._interaction(
            model=self.bulk_model,
            fallback_model=self.bulk_fallback_model,
            input_text=prompt,
            response_schema=GROUNDED_RESEARCH_SCHEMA,
            tools=[{"type": "google_search", "search_types": ["web_search"]}],
        )
        parsed = self._parse_structured_response(response, "grounded research")
        sources: list[dict[str, Any]] = []
        for index, raw in enumerate(parsed.get("sources") or [], start=1):
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["id"] = str(item.get("id") or f"s{index}")
            item["source_quality_score"] = source_quality_score(item)
            sources.append(item)
        parsed["sources"] = sources
        parsed["model_role"] = "bulk_research"
        parsed["model"] = model
        parsed["paid_fallback_used"] = paid_used
        parsed["interaction_id"] = response.get("id")
        parsed["retrieved_sources"] = _extract_step_sources(response)
        parsed["generated_at"] = utc_iso()
        return parsed

    def specialist_review(
        self,
        hypothesis: dict[str, Any],
        *,
        research_run: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        packet = {
            "hypothesis": hypothesis,
            "research_summary": (research_run or {}).get("summary"),
            "sources": (research_run or {}).get("sources") or [],
            "contradictions": (research_run or {}).get("contradictions") or [],
        }
        prompt = SPECIALIST_SYSTEM_PROMPT + "\n\nRESEARCH PACKET:\n" + json.dumps(
            packet, indent=2, default=str
        )[:90000]
        response, model, paid_used = self._interaction(
            model=self.specialist_model,
            fallback_model=self.specialist_fallback_model,
            input_text=prompt,
            response_schema=SPECIALIST_REVIEW_SCHEMA,
        )
        parsed = self._parse_structured_response(response, "specialist review")
        parsed["model_role"] = "specialist_reasoning"
        parsed["model"] = model
        parsed["paid_fallback_used"] = paid_used
        parsed["interaction_id"] = response.get("id")
        parsed["generated_at"] = utc_iso()
        return parsed


def _hypothesis_quality(
    hypothesis: dict[str, Any],
    source_map: dict[str, dict[str, Any]],
) -> int:
    ids = [
        str(value or "")
        for value in hypothesis.get("supporting_source_ids") or []
        if str(value or "")
    ]
    scores = [
        int(source_map[source_id].get("source_quality_score") or 0)
        for source_id in ids
        if source_id in source_map
    ]
    if not scores:
        return 25
    scores.sort(reverse=True)
    top = scores[:3]
    average = sum(top) / len(top)
    corroboration_bonus = min(10, max(0, len(set(ids)) - 1) * 4)
    contradiction_penalty = min(12, len(hypothesis.get("contradicting_source_ids") or []) * 3)
    return max(0, min(100, round(average + corroboration_bonus - contradiction_penalty)))


def merge_grounded_research(
    library: dict[str, Any],
    research: dict[str, Any],
    *,
    topic: str,
    origin_job_id: str = "",
) -> tuple[dict[str, Any], str, list[str]]:
    data = ensure_research_collections(library)
    generated_at = str(research.get("generated_at") or utc_iso())
    run_id = "web-" + hashlib.sha256(
        f"{topic}|{generated_at}|{research.get('interaction_id') or ''}".encode("utf-8")
    ).hexdigest()[:22]
    sources = [dict(item) for item in research.get("sources") or [] if isinstance(item, dict)]
    source_map = {str(item.get("id") or ""): item for item in sources}
    hypotheses: list[dict[str, Any]] = []
    hypothesis_ids: list[str] = []
    for index, raw in enumerate(research.get("hypotheses") or [], start=1):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        name = str(item.get("name") or f"Research hypothesis {index}").strip()
        statement = str(item.get("statement") or "").strip()
        hypothesis_id = "hyp-" + hashlib.sha256(
            f"{run_id}|{name}|{statement}".encode("utf-8")
        ).hexdigest()[:22]
        item["id"] = hypothesis_id
        item["research_run_id"] = run_id
        item["topic"] = str(topic)
        item["created_at"] = generated_at
        item["updated_at"] = generated_at
        item["status"] = "awaiting_specialist"
        item["machine_rules"] = _normalized_rules(item.get("machine_rules"))
        item["source_quality_score"] = _hypothesis_quality(item, source_map)
        item["specialist_review"] = None
        item["strategy_id"] = None
        hypotheses.append(item)
        hypothesis_ids.append(hypothesis_id)

    run = {
        "id": run_id,
        "kind": "grounded_web_research",
        "topic": str(topic),
        "generated_at": generated_at,
        "origin_job_id": str(origin_job_id or "") or None,
        "title": research.get("title"),
        "summary": research.get("summary"),
        "sources": sources,
        "retrieved_sources": research.get("retrieved_sources") or [],
        "contradictions": research.get("contradictions") or [],
        "follow_up_questions": research.get("follow_up_questions") or [],
        "hypothesis_ids": hypothesis_ids,
        "model": research.get("model"),
        "model_role": research.get("model_role"),
        "paid_fallback_used": bool(research.get("paid_fallback_used")),
        "interaction_id": research.get("interaction_id"),
    }
    existing_runs = [
        item for item in data["external_research_runs"]
        if str(item.get("id") or "") != run_id
    ]
    data["external_research_runs"] = [run, *existing_runs][:120]

    existing_hypotheses = {
        str(item.get("id") or ""): item
        for item in data["research_hypotheses"]
        if isinstance(item, dict)
    }
    for hypothesis in hypotheses:
        existing_hypotheses[str(hypothesis["id"])] = hypothesis
    data["research_hypotheses"] = list(existing_hypotheses.values())[-500:]

    # Pro is intentionally reserved for the strongest/hardest discoveries rather
    # than being used as a high-volume reader. Rank every hypothesis, then send only
    # the top two from each grounded research run to specialist review.
    ranked_for_specialist = sorted(
        hypotheses,
        key=lambda item: (
            int(item.get("source_quality_score") or 0) * 0.55
            + (safe_float(item.get("confidence"), 0.0) or 0.0) * 0.35
            + (safe_float(item.get("novelty"), 0.0) or 0.0) * 0.10
        ),
        reverse=True,
    )
    specialist_ids = {
        str(item.get("id") or "")
        for item in ranked_for_specialist[:2]
    }
    refreshed_hypotheses: list[dict[str, Any]] = []
    for hypothesis in data["research_hypotheses"]:
        item = dict(hypothesis)
        hypothesis_id = str(item.get("id") or "")
        if hypothesis_id in specialist_ids:
            quality = int(item.get("source_quality_score") or 0)
            confidence = safe_float(item.get("confidence"), 0.0) or 0.0
            priority = int(max(35, min(90, quality * 0.55 + confidence * 0.45)))
            data, _ = enqueue_research_job(
                data,
                "specialist_review",
                {
                    "hypothesis_id": hypothesis_id,
                    "research_run_id": run_id,
                },
                priority=priority,
                dedupe_key=f"specialist:{hypothesis_id}",
            )
        elif str(item.get("research_run_id") or "") == run_id:
            item["status"] = "research_backlog"
        refreshed_hypotheses.append(item)
    data["research_hypotheses"] = refreshed_hypotheses
    return data, run_id, hypothesis_ids


def find_research_hypothesis(library: dict[str, Any], hypothesis_id: str) -> dict[str, Any] | None:
    for item in ensure_research_collections(library)["research_hypotheses"]:
        if str(item.get("id") or "") == str(hypothesis_id or ""):
            return dict(item)
    return None


def find_external_research_run(library: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    for item in ensure_research_collections(library)["external_research_runs"]:
        if str(item.get("id") or "") == str(run_id or ""):
            return dict(item)
    return None


def materialize_research_strategy(
    hypothesis: dict[str, Any],
    *,
    reviewed: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    source = dict((reviewed or {}).get("revised_hypothesis") or hypothesis)
    rules = _normalized_rules(source.get("machine_rules"))
    if not rules:
        return None
    hypothesis_id = str(hypothesis.get("id") or "")
    strategy_id = "webresearch-" + hashlib.sha256(
        f"{hypothesis_id}|{json.dumps(rules, sort_keys=True, default=str)}".encode("utf-8")
    ).hexdigest()[:18]
    quality = int(hypothesis.get("source_quality_score") or 0)
    review_confidence = safe_float((reviewed or {}).get("confidence"), 0.0) or 0.0
    extraction_confidence = safe_float(source.get("confidence"), 0.0) or 0.0
    confidence = round(min(100.0, quality * 0.55 + max(review_confidence, extraction_confidence) * 0.45), 1)
    evidence = [
        {
            "location": f"Grounded web research hypothesis {hypothesis_id}",
            "description": str(source.get("statement") or hypothesis.get("statement") or "")[:700],
            "source_excerpt": "",
        }
    ]
    return {
        "id": strategy_id,
        "name": str(source.get("name") or hypothesis.get("name") or "Autonomous research hypothesis")[:160],
        "category": str(source.get("category") or hypothesis.get("category") or "research")[:100],
        "direction": str(source.get("direction") or hypothesis.get("direction") or "long"),
        "summary": str(source.get("statement") or hypothesis.get("statement") or "")[:3000],
        "indicators": [],
        "entry_conditions": [],
        "exit_conditions": [],
        "risk_rules": [],
        "avoid_conditions": [],
        "market_context": [str(source.get("market_scope") or "")] if source.get("market_scope") else [],
        "stock_selection": [],
        "unresolved_rules": list(source.get("unresolved_rules") or [])[:50],
        "machine_rules": rules,
        "confidence": confidence,
        "evidence": evidence,
        "source_type": "autonomous_web_research",
        "source_id": hypothesis_id,
        "source_title": "Autonomous grounded research",
        "research_hypothesis_id": hypothesis_id,
        "research_run_id": hypothesis.get("research_run_id"),
        "research_source_quality_score": quality,
        "validation_status": "unvalidated",
        "optimization_status": "not_run",
        "approved": False,
        "paper_validation_status": "not_ready",
        "created_at": utc_iso(),
    }


def apply_specialist_review(
    library: dict[str, Any],
    hypothesis_id: str,
    review: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    data = ensure_research_collections(library)
    decision = str(review.get("decision") or "keep_researching")
    strategy_id: str | None = None
    updated_hypotheses: list[dict[str, Any]] = []
    target: dict[str, Any] | None = None
    for raw in data["research_hypotheses"]:
        item = dict(raw)
        if str(item.get("id") or "") == str(hypothesis_id or ""):
            item["specialist_review"] = dict(review)
            item["updated_at"] = str(review.get("generated_at") or utc_iso())
            item["status"] = {
                "promote_to_validation": "queued_for_validation",
                "keep_researching": "needs_more_research",
                "reject": "rejected",
            }.get(decision, "needs_more_research")
            target = item
        updated_hypotheses.append(item)
    if target is None:
        raise AppError("The specialist-review hypothesis is no longer in the research library.")

    if decision == "promote_to_validation":
        strategy = materialize_research_strategy(target, reviewed=review)
        if strategy is not None and int(target.get("source_quality_score") or 0) >= 45:
            strategy_id = str(strategy["id"])
            existing = [
                dict(item)
                for item in data.get("strategies") or []
                if isinstance(item, dict) and str(item.get("id") or "") != strategy_id
            ]
            data["strategies"] = [strategy, *existing]
            target["strategy_id"] = strategy_id
            target["status"] = "queued_for_validation"
            updated_hypotheses = [
                target if str(item.get("id") or "") == str(hypothesis_id or "") else item
                for item in updated_hypotheses
            ]
            data, _ = enqueue_research_job(
                data,
                "autonomous_validation",
                {"origin_hypothesis_id": hypothesis_id},
                priority=45,
                dedupe_key="autonomous_validation:pending_web_research",
            )
        else:
            target["status"] = "needs_more_research"
            target["specialist_review"] = {
                **dict(review),
                "promotion_blocked": (
                    "No objective machine-testable rules survived specialist review."
                    if strategy is None
                    else "Source-quality score was below the validation queue threshold."
                ),
            }
            updated_hypotheses = [
                target if str(item.get("id") or "") == str(hypothesis_id or "") else item
                for item in updated_hypotheses
            ]

    if decision == "keep_researching":
        for question in list(review.get("follow_up_questions") or [])[:2]:
            text = str(question or "").strip()
            if not text:
                continue
            data, _ = enqueue_research_job(
                data,
                "web_research",
                {
                    "topic": text,
                    "origin": "specialist_follow_up",
                    "parent_hypothesis_id": hypothesis_id,
                },
                priority=60,
                dedupe_key=f"followup:{hypothesis_id}:{text.casefold()}",
            )

    data["research_hypotheses"] = updated_hypotheses
    return data, strategy_id


def sync_hypothesis_validation_results(
    library: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Mirror deterministic validation outcomes back onto research hypotheses."""
    data = ensure_research_collections(library)
    strategies_by_id = {
        str(item.get("id") or ""): item
        for item in data.get("strategies") or []
        if isinstance(item, dict)
    }
    outcome_by_hypothesis: dict[str, dict[str, Any]] = {}
    for result in report.get("results") or []:
        if not isinstance(result, dict):
            continue
        strategy_id = str(result.get("strategy_id") or "")
        strategy = strategies_by_id.get(strategy_id) or {}
        hypothesis_id = str(strategy.get("research_hypothesis_id") or "")
        if not hypothesis_id:
            continue
        outcome_by_hypothesis[hypothesis_id] = {
            "validation_status": str(result.get("validation_status") or "research_only"),
            "global_score": result.get("global_score"),
            "anchor_symbol": result.get("anchor_symbol"),
            "candidate_symbols": result.get("candidate_symbols") or [],
            "gate_reasons": result.get("gate_reasons") or [],
            "generated_at": report.get("generated_at") or utc_iso(),
        }

    if not outcome_by_hypothesis:
        return data

    updated: list[dict[str, Any]] = []
    for raw in data["research_hypotheses"]:
        item = dict(raw)
        hypothesis_id = str(item.get("id") or "")
        outcome = outcome_by_hypothesis.get(hypothesis_id)
        if outcome:
            item["validation_summary"] = outcome
            item["updated_at"] = str(outcome.get("generated_at") or utc_iso())
            item["status"] = (
                "validated"
                if outcome.get("validation_status") == "validated"
                else "historically_rejected_or_unconfirmed"
            )
        updated.append(item)
    data["research_hypotheses"] = updated
    return data


def record_worker_run(
    library: dict[str, Any],
    *,
    worker_id: str,
    job_id: str,
    job_type: str,
    status: str,
    detail: str = "",
) -> dict[str, Any]:
    data = ensure_research_collections(library)
    record = {
        "id": "worker-" + hashlib.sha256(
            f"{worker_id}|{job_id}|{utc_iso()}".encode("utf-8")
        ).hexdigest()[:22],
        "worker_id": str(worker_id),
        "job_id": str(job_id),
        "job_type": str(job_type),
        "status": str(status),
        "detail": str(detail)[:1200],
        "generated_at": utc_iso(),
    }
    data["research_worker_runs"] = [record, *data["research_worker_runs"]][:300]
    system = dict(data.get("research_system") or {})
    system["last_worker_at"] = record["generated_at"]
    system["last_worker_id"] = str(worker_id)
    system["last_worker_status"] = str(status)
    data["research_system"] = system
    return data
