"""Point-in-time catalyst intelligence for Trading Intelligence Lab.

Historical news is timestamped and merged into price bars without look-ahead:
a bar can only see articles published at or before that bar's timestamp.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
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
        "FDA / clinical catalyst",
        9.0,
        (
            "fda approval",
            "fda clears",
            "fda clearance",
            "breakthrough therapy",
            "fast track designation",
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
        9.0,
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
        7.0,
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
        "earnings / guidance",
        6.0,
        (
            "earnings",
            "quarterly results",
            "financial results",
            "raises guidance",
            "raised guidance",
            "revenue guidance",
            "beats estimates",
            "record revenue",
            "profit",
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
        strongest = max(matches, key=lambda item: abs(float(item["score"])))
        category = strongest["category"]
        score = float(strongest["score"])
        keywords = strongest["keywords"]
    else:
        category = "generic / unclassified news"
        score = 0.0
        keywords = []

    published = _article_timestamp(article)
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
        "is_specific_catalyst": bool(score),
        "is_positive": score > 0,
        "is_negative": score < 0,
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
