"""Point-in-time catalyst intelligence for Trading Intelligence Lab.

Historical news is timestamped and merged into price bars without look-ahead:
a bar can only see articles published at or before that bar's timestamp.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Callable

import pandas as pd

from youtube_strategy_engine import AlpacaMarketData, AppError, isoformat_utc, parse_symbols, safe_float


CATALYST_RULES: tuple[tuple[str, float, tuple[str, ...]], ...] = (
    (
        "bankruptcy / severe distress",
        -10.0,
        ("bankruptcy", "chapter 11", "chapter 7", "going concern", "liquidation"),
    ),
    (
        "offering / dilution risk",
        -8.0,
        (
            "public offering",
            "registered direct",
            "at-the-market",
            "atm offering",
            "private placement",
            "warrant exercise",
            "securities purchase agreement",
            "stock offering",
        ),
    ),
    (
        "delisting / reverse split risk",
        -8.0,
        (
            "reverse stock split",
            "reverse split",
            "delisting",
            "nasdaq deficiency",
            "noncompliance",
            "minimum bid price",
        ),
    ),
    (
        "legal / regulatory risk",
        -6.0,
        ("investigation", "subpoena", "lawsuit", "class action", "sec charges", "fraud"),
    ),
    (
        "FDA approval / clearance",
        9.0,
        (
            "fda approval",
            "fda approves",
            "fda clears",
            "fda clearance",
            "breakthrough therapy",
            "fast track designation",
        ),
    ),
    (
        "clinical trial update",
        0.0,
        (
            "phase 1",
            "phase 2",
            "phase 3",
            "clinical trial",
            "trial results",
            "primary endpoint",
        ),
    ),
    (
        "merger / acquisition",
        0.0,
        (
            "merger",
            "acquisition",
            "acquire",
            "acquired by",
            "buyout",
            "takeover",
            "strategic alternatives",
        ),
    ),
    (
        "major commercial deal",
        6.0,
        (
            "purchase order",
            "contract award",
            "awarded contract",
            "selected by",
            "strategic partnership",
            "collaboration agreement",
            "licensing agreement",
        ),
    ),
    (
        "positive earnings / guidance",
        6.0,
        (
            "raises guidance",
            "raised guidance",
            "beats estimates",
            "beat estimates",
            "record revenue",
            "record quarterly revenue",
        ),
    ),
    (
        "earnings / financial results",
        0.0,
        (
            "earnings",
            "quarterly results",
            "financial results",
            "revenue guidance",
        ),
    ),
    (
        "regulatory / patent milestone",
        4.0,
        ("510(k)", "patent granted", "receives patent", "regulatory approval", "regulatory clearance"),
    ),
    (
        "analyst catalyst",
        3.0,
        ("upgraded", "upgrade", "price target raised", "raises price target", "initiates coverage"),
    ),
)

def _article_timestamp(article: dict[str, Any]) -> datetime | None:
    raw = article.get("created_at") or article.get("published_at") or article.get("updated_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def classify_catalyst(article: dict[str, Any]) -> dict[str, Any]:
    """Conservative keyword taxonomy reused from the momentum scanner."""
    headline = str(article.get("headline") or "").strip()
    summary = str(article.get("summary") or "").strip()
    text = f"{headline} {summary}".casefold()
    matches: list[dict[str, Any]] = []
    for category, score, keywords in CATALYST_RULES:
        found = [keyword for keyword in keywords if keyword.casefold() in text]
        if found:
            matches.append({"category": category, "score": score, "keywords": found})

    if matches:
        strongest = max(
            matches,
            key=lambda item: (
                abs(float(item["score"])),
                len(item.get("keywords") or []),
            ),
        )
        category = strongest["category"]
        score = float(strongest["score"])
        keywords = strongest["keywords"]
    else:
        category = "generic / unclassified news"
        score = 0.0
        keywords = []

    published = _article_timestamp(article)
    fingerprint_text = re.sub(r"[^a-z0-9]+", " ", headline.casefold()).strip()
    return {
        "headline": headline,
        "summary": summary,
        "source": article.get("source"),
        "url": article.get("url"),
        "symbols": list(article.get("symbols") or []),
        "published_at": isoformat_utc(published) if published is not None else None,
        "category": category,
        "score": score,
        "keywords": keywords,
        "is_specific_catalyst": bool(matches),
        "is_directional_hint": bool(matches) and score != 0,
        "direction_requires_context": bool(matches) and score == 0,
        "is_positive": score > 0,
        "is_negative": score < 0,
        "is_dilution_risk": category == "offering / dilution risk",
        "is_structural_risk": category in {
            "offering / dilution risk",
            "delisting / reverse split risk",
            "bankruptcy / severe distress",
            "legal / regulatory risk",
        },
        "evidence_type": "news",
        "source_quality": "news",
        "fingerprint": fingerprint_text,
    }


def catalyst_freshness(
    published_at: Any,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Return explicit age/freshness without claiming the market has or has not priced it in."""
    published = _article_timestamp({"published_at": published_at})
    reference = as_of or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    reference = reference.astimezone(timezone.utc)
    if published is None:
        return {
            "age_hours": None,
            "freshness": "unknown",
            "freshness_weight": 0.0,
        }
    age_hours = max(0.0, (reference - published).total_seconds() / 3600.0)
    if age_hours <= 2:
        label, weight = "breaking", 1.0
    elif age_hours <= 24:
        label, weight = "fresh", 0.9
    elif age_hours <= 72:
        label, weight = "recent", 0.65
    elif age_hours <= 168:
        label, weight = "aging", 0.4
    else:
        label, weight = "stale", 0.2
    return {
        "age_hours": age_hours,
        "freshness": label,
        "freshness_weight": weight,
    }


def rank_catalyst_evidence(
    news_items: list[dict[str, Any]],
    sec_items: list[dict[str, Any]] | None = None,
    *,
    as_of: datetime | None = None,
) -> list[dict[str, Any]]:
    """Combine deterministic news + SEC evidence with freshness and duplicate awareness."""
    reference = as_of or datetime.now(timezone.utc)
    combined: list[dict[str, Any]] = []
    for raw in [*(news_items or []), *((sec_items or []))]:
        item = dict(raw)
        freshness = catalyst_freshness(item.get("published_at"), as_of=reference)
        item.update(freshness)
        score = safe_float(item.get("score"), 0.0) or 0.0
        source_weight = 1.0 if str(item.get("evidence_type") or "") == "sec_filing" else 0.85
        item["effective_score"] = score * float(freshness["freshness_weight"]) * source_weight
        fingerprint = str(item.get("fingerprint") or "").strip()
        if not fingerprint:
            headline = str(item.get("headline") or item.get("primaryDocDescription") or "").casefold()
            fingerprint = re.sub(r"[^a-z0-9]+", " ", headline).strip()
        item["fingerprint"] = fingerprint
        combined.append(item)

    combined.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    seen: set[str] = set()
    for item in combined:
        fingerprint = str(item.get("fingerprint") or "")
        if fingerprint and fingerprint in seen:
            item["novelty"] = "repeat"
            item["novelty_weight"] = 0.5
            item["effective_score"] = (safe_float(item.get("effective_score"), 0.0) or 0.0) * 0.5
        else:
            item["novelty"] = "new"
            item["novelty_weight"] = 1.0
            if fingerprint:
                seen.add(fingerprint)

    combined.sort(
        key=lambda item: (
            abs(safe_float(item.get("effective_score"), 0.0) or 0.0),
            str(item.get("published_at") or ""),
        ),
        reverse=True,
    )
    return combined


def catalyst_intelligence_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    specific = [item for item in items if item.get("is_specific_catalyst")]
    dilution = [item for item in specific if item.get("is_dilution_risk")]
    fresh = [
        item for item in specific
        if str(item.get("freshness") or "") in {"breaking", "fresh"}
    ]
    return {
        "evidence_items": len(items),
        "specific_catalysts": len(specific),
        "fresh_specific_catalysts": len(fresh),
        "dilution_risks": len(dilution),
        "positive_catalysts": sum(1 for item in specific if item.get("is_positive")),
        "negative_catalysts": sum(1 for item in specific if item.get("is_negative")),
    }


def historical_news(
    market: AlpacaMarketData,
    symbols: list[str],
    *,
    start: datetime,
    end: datetime,
    max_pages: int = 60,
    progress: Callable[[int], None] | None = None,
) -> list[dict[str, Any]]:
    """Retrieve paginated Alpaca historical news for a bounded symbol/date window."""
    clean = parse_symbols(symbols)
    if not clean:
        return []
    if end <= start:
        raise AppError("Historical news end time must be after start time.")

    articles: list[dict[str, Any]] = []
    token: str | None = None
    seen: set[str] = set()
    for page in range(max(1, int(max_pages))):
        params: dict[str, Any] = {
            "symbols": ",".join(clean),
            "start": isoformat_utc(start),
            "end": isoformat_utc(end),
            "sort": "asc",
            "limit": 50,
            "include_content": "false",
        }
        if token:
            params["page_token"] = token
        response = market._get("/v1beta1/news", params)
        batch = response.get("news") or []
        articles.extend(item for item in batch if isinstance(item, dict))
        if progress:
            progress(page + 1)
        next_token = response.get("next_page_token")
        if not next_token:
            break
        token = str(next_token)
        if token in seen:
            raise AppError("Alpaca returned a repeated historical-news pagination token.")
        seen.add(token)
    else:
        raise AppError(
            "Historical news exceeded the safe pagination limit. Use a shorter historical window."
        )

    deduped: list[dict[str, Any]] = []
    identities: set[str] = set()
    for article in articles:
        identity = str(article.get("id") or article.get("url") or (
            str(article.get("created_at")) + "|" + str(article.get("headline"))
        ))
        if identity in identities:
            continue
        identities.add(identity)
        deduped.append(article)
    return deduped


def enrich_bars_with_point_in_time_catalysts(
    rows: list[dict[str, Any]],
    articles: list[dict[str, Any]],
    *,
    lookback_hours: float = 24.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Attach catalyst context to every bar using only already-published articles."""
    if not rows:
        return [], {
            "articles": 0,
            "specific_catalysts": 0,
            "positive_catalysts": 0,
            "negative_catalysts": 0,
        }

    classified = []
    for raw in articles:
        item = classify_catalyst(raw)
        published = _article_timestamp(raw)
        if published is not None:
            classified.append((published, item))
    classified.sort(key=lambda pair: pair[0])

    lookback = timedelta(hours=max(1.0, float(lookback_hours)))
    active: deque[tuple[datetime, dict[str, Any]]] = deque()
    pointer = 0
    enriched: list[dict[str, Any]] = []

    for raw_bar in rows:
        bar = dict(raw_bar)
        raw_timestamp = bar.get("t") or bar.get("timestamp")
        try:
            bar_time = pd.to_datetime(raw_timestamp, utc=True).to_pydatetime()
        except Exception:
            bar_time = None

        if bar_time is None:
            bar["catalyst_data_available"] = True
            bar["has_catalyst"] = False
            bar["catalyst_score"] = 0.0
            enriched.append(bar)
            continue

        while pointer < len(classified) and classified[pointer][0] <= bar_time:
            active.append(classified[pointer])
            pointer += 1
        cutoff = bar_time - lookback
        while active and active[0][0] < cutoff:
            active.popleft()

        specific = [item for _, item in active if item.get("is_specific_catalyst")]
        best = max(specific, key=lambda item: abs(safe_float(item.get("score"), 0.0) or 0.0), default=None)
        positive = [item for item in specific if (safe_float(item.get("score"), 0.0) or 0.0) > 0]
        negative = [item for item in specific if (safe_float(item.get("score"), 0.0) or 0.0) < 0]

        bar["catalyst_data_available"] = True
        bar["has_catalyst"] = bool(specific)
        bar["has_positive_catalyst"] = bool(positive)
        bar["has_negative_catalyst"] = bool(negative)
        bar["catalyst_score"] = safe_float((best or {}).get("score"), 0.0) or 0.0
        bar["catalyst_category"] = (best or {}).get("category")
        bar["catalyst_headline"] = (best or {}).get("headline")
        bar["catalyst_published_at"] = (best or {}).get("published_at")
        enriched.append(bar)

    classified_only = [item for _, item in classified if item.get("is_specific_catalyst")]
    return enriched, {
        "articles": len(classified),
        "specific_catalysts": len(classified_only),
        "positive_catalysts": sum(1 for item in classified_only if item.get("is_positive")),
        "negative_catalysts": sum(1 for item in classified_only if item.get("is_negative")),
        "lookback_hours": float(lookback_hours),
    }
