"""Experimental explosive-stock discovery engine.

This module intentionally stays separate from the validated strategy workflow.
It reuses trusted market-data, feature, catalyst, and cache infrastructure, but
its profile score is a transparent research heuristic until historical outcome
validation replaces the hand-set weights.

The live question is:
    "Does this stock currently resemble names that can make unusually large
    upside moves, and is that potential beginning to activate?"

It does NOT answer:
    "What is the probability this stock will rise X%?"
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from market_features import build_market_features
from trading_catalyst_core import classify_catalyst, rank_catalyst_evidence
from youtube_strategy_engine import (
    ET,
    AlpacaMarketData,
    AppError,
    average_completed_daily_volume,
    parse_symbols,
    safe_float,
    snapshot_metrics,
    utc_now,
)

EXPLOSIVE_MODEL_VERSION = "explosive-profile-v0.1"
DEFAULT_HISTORY_DAYS = 90
DEFAULT_NEWS_HOURS = 72
MAX_EXPLOSIVE_SCAN_SYMBOLS = 100

FORWARD_HORIZONS = (1, 3, 5, 10, 20)
EXPLOSION_THRESHOLDS_PCT = (30.0, 50.0, 100.0, 200.0)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _as_et_timestamp(row: dict[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(row.get("t") or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=ET)
    return value.astimezone(ET)


def _session_start(now: datetime | None = None) -> datetime:
    current = (now or utc_now()).astimezone(ET)
    session_day = current.date()
    if current.hour * 60 + current.minute < 4 * 60:
        session_day -= timedelta(days=1)
    while session_day.weekday() >= 5:
        session_day -= timedelta(days=1)
    return datetime.combine(session_day, datetime.min.time(), tzinfo=ET).replace(hour=4)


def completed_intraday_rows(
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Use completed one-minute candles only; never score the still-forming bar."""
    current = (now or utc_now()).astimezone(ET)
    cutoff = current.replace(second=0, microsecond=0)
    completed: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        timestamp = _as_et_timestamp(row)
        if timestamp is None or timestamp >= cutoff:
            continue
        completed.append(row)
    completed.sort(key=lambda item: str(item.get("t") or ""))
    return completed


def completed_daily_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize only the completed daily bars supplied by the caller."""
    cleaned: list[dict[str, float | str]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        close = safe_float(row.get("c"))
        high = safe_float(row.get("h"))
        low = safe_float(row.get("l"))
        volume = safe_float(row.get("v"))
        if close is None or high is None or low is None or close <= 0 or high <= 0 or low <= 0:
            continue
        cleaned.append(
            {
                "t": str(row.get("t") or ""),
                "close": close,
                "high": high,
                "low": low,
                "volume": max(0.0, volume or 0.0),
            }
        )
    cleaned.sort(key=lambda item: str(item["t"]))
    if not cleaned:
        return {
            "bar_count": 0,
            "largest_single_day_gain_pct": None,
            "runner_days_20pct": 0,
            "runner_days_30pct": 0,
            "recent_10d_range_pct": None,
            "compression_ratio_5v20": None,
            "previous_day_volume_ratio": None,
            "distance_from_60d_high_pct": None,
            "average_dollar_volume_20d": None,
        }

    returns: list[float] = []
    for previous, current in zip(cleaned, cleaned[1:]):
        prior_close = float(previous["close"])
        if prior_close > 0:
            returns.append((float(current["close"]) / prior_close - 1.0) * 100.0)

    range_pcts = [
        ((float(item["high"]) / float(item["low"])) - 1.0) * 100.0
        for item in cleaned
        if float(item["low"]) > 0
    ]
    last_5_ranges = range_pcts[-5:]
    baseline_ranges = range_pcts[-25:-5]
    last_5_range = _mean(last_5_ranges)
    baseline_range = _mean(baseline_ranges)
    compression_ratio = (
        last_5_range / baseline_range
        if last_5_range is not None and baseline_range is not None and baseline_range > 0
        else None
    )

    recent_10 = cleaned[-10:]
    recent_low = min(float(item["low"]) for item in recent_10)
    recent_high = max(float(item["high"]) for item in recent_10)
    recent_10_range = (
        (recent_high / recent_low - 1.0) * 100.0 if recent_low > 0 else None
    )

    recent_60 = cleaned[-60:]
    high_60 = max(float(item["high"]) for item in recent_60)
    last_close = float(cleaned[-1]["close"])
    distance_from_high = (
        (high_60 - last_close) / high_60 * 100.0 if high_60 > 0 else None
    )

    baseline_volume_rows = cleaned[-21:-1]
    baseline_volume = _mean([float(item["volume"]) for item in baseline_volume_rows])
    previous_day_volume_ratio = (
        float(cleaned[-1]["volume"]) / baseline_volume
        if baseline_volume is not None and baseline_volume > 0
        else None
    )

    dollar_rows = cleaned[-20:]
    average_dollar_volume = _mean(
        [float(item["close"]) * float(item["volume"]) for item in dollar_rows]
    )

    return {
        "bar_count": len(cleaned),
        "largest_single_day_gain_pct": max(returns[-60:]) if returns else None,
        "runner_days_20pct": sum(value >= 20.0 for value in returns[-60:]),
        "runner_days_30pct": sum(value >= 30.0 for value in returns[-60:]),
        "recent_10d_range_pct": recent_10_range,
        "compression_ratio_5v20": compression_ratio,
        "previous_day_volume_ratio": previous_day_volume_ratio,
        "distance_from_60d_high_pct": distance_from_high,
        "average_dollar_volume_20d": average_dollar_volume,
    }


def catalyst_profile(
    news_items: list[dict[str, Any]],
    sec_items: list[dict[str, Any]] | None = None,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Build catalyst upside evidence and structural-risk evidence separately."""
    classified_news = [
        classify_catalyst(item)
        for item in news_items or []
        if isinstance(item, dict)
    ]
    evidence = rank_catalyst_evidence(
        classified_news,
        sec_items or [],
        as_of=as_of or utc_now(),
    )
    specific = [item for item in evidence if item.get("is_specific_catalyst")]
    fresh = [
        item
        for item in specific
        if str(item.get("freshness") or "") in {"breaking", "fresh", "recent"}
    ]
    positive = [item for item in fresh if item.get("is_positive")]
    structural_risk = [item for item in fresh if item.get("is_structural_risk")]
    dilution = [item for item in structural_risk if item.get("is_dilution_risk")]

    strongest_positive = max(
        (safe_float(item.get("effective_score"), 0.0) or 0.0 for item in positive),
        default=0.0,
    )
    strongest_negative = min(
        (safe_float(item.get("effective_score"), 0.0) or 0.0 for item in fresh),
        default=0.0,
    )
    return {
        "evidence": evidence,
        "specific_count": len(specific),
        "fresh_specific_count": len(fresh),
        "fresh_positive_count": len(positive),
        "fresh_structural_risk_count": len(structural_risk),
        "fresh_dilution_count": len(dilution),
        "strongest_positive_score": strongest_positive,
        "strongest_negative_score": strongest_negative,
        "top_evidence": evidence[:8],
    }


def _volume_attention_points(metrics: dict[str, Any], features: dict[str, Any]) -> tuple[float, list[str]]:
    points = 0.0
    reasons: list[str] = []
    rvol = safe_float(metrics.get("relative_volume"))
    if rvol is not None:
        if rvol >= 8:
            points += 18
            reasons.append(f"Exceptional relative volume ({rvol:.1f}×).")
        elif rvol >= 4:
            points += 14
            reasons.append(f"Very high relative volume ({rvol:.1f}×).")
        elif rvol >= 2:
            points += 9
            reasons.append(f"Elevated relative volume ({rvol:.1f}×).")
        elif rvol >= 1.25:
            points += 4

    acceleration = safe_float(features.get("volume_acceleration_ratio"))
    if acceleration is not None:
        if acceleration >= 3:
            points += 7
            reasons.append(f"Intraday volume is accelerating sharply ({acceleration:.1f}× recent/prior).")
        elif acceleration >= 1.5:
            points += 4
            reasons.append(f"Intraday volume is accelerating ({acceleration:.1f}×).")
    return min(25.0, points), reasons


def _activation_points(metrics: dict[str, Any], features: dict[str, Any]) -> tuple[float, list[str]]:
    points = 0.0
    reasons: list[str] = []
    day_change = safe_float(metrics.get("day_change_pct"))
    if day_change is not None:
        if 8 <= day_change <= 35:
            points += 8
            reasons.append(f"Strong upside price activation ({day_change:+.1f}% today).")
        elif 3 <= day_change < 8:
            points += 5
        elif 35 < day_change <= 60:
            points += 5
            reasons.append("Large move is already underway; extension risk is rising.")

    if metrics.get("above_vwap"):
        points += 4
        reasons.append("Price is above session VWAP.")

    distance_high = safe_float(metrics.get("distance_from_high_pct"))
    if distance_high is not None and distance_high <= 5:
        points += 4
        reasons.append("Price is holding near the current session high.")

    if features.get("consolidation_then_expansion_up") is True:
        points += 4
        reasons.append("A causal consolidation-to-expansion pattern is active.")
    elif features.get("breakout_above_last_swing_high") is True:
        points += 4
        reasons.append("Price is above the last confirmed swing high.")

    return min(20.0, points), reasons


def _stored_energy_points(daily: dict[str, Any], features: dict[str, Any]) -> tuple[float, list[str]]:
    points = 0.0
    reasons: list[str] = []

    compression = safe_float(daily.get("compression_ratio_5v20"))
    if compression is not None:
        if compression <= 0.55:
            points += 7
            reasons.append("Recent daily ranges are tightly compressed versus the prior baseline.")
        elif compression <= 0.75:
            points += 4
            reasons.append("Recent daily volatility is moderately compressed.")

    base_ratio = safe_float(features.get("base_range_atr_ratio"))
    if base_ratio is not None and base_ratio <= 2.5:
        points += 4

    if features.get("uptrend_structure") is True:
        points += 4
        reasons.append("Confirmed intraday structure is HH/HL.")
    elif features.get("pullback_higher_low") is True:
        points += 2

    previous_volume_ratio = safe_float(daily.get("previous_day_volume_ratio"))
    if previous_volume_ratio is not None and previous_volume_ratio >= 1.5:
        points += 3
        reasons.append("The previous completed session already showed elevated volume.")

    return min(20.0, points), reasons


def _historical_explosiveness_points(daily: dict[str, Any]) -> tuple[float, list[str]]:
    points = 0.0
    reasons: list[str] = []
    largest = safe_float(daily.get("largest_single_day_gain_pct"))
    runners20 = int(safe_float(daily.get("runner_days_20pct"), 0) or 0)
    runners30 = int(safe_float(daily.get("runner_days_30pct"), 0) or 0)
    if largest is not None and largest >= 50:
        points += 7
        reasons.append("This ticker has produced a 50%+ single-day gain in the recent sample.")
    elif largest is not None and largest >= 30:
        points += 5
        reasons.append("This ticker has produced a 30%+ single-day gain in the recent sample.")
    elif largest is not None and largest >= 20:
        points += 3

    points += min(4.0, runners20 * 1.5)
    points += min(4.0, runners30 * 2.0)
    return min(15.0, points), reasons


def _catalyst_points(catalyst: dict[str, Any]) -> tuple[float, list[str]]:
    points = 0.0
    reasons: list[str] = []
    fresh_specific = int(catalyst.get("fresh_specific_count") or 0)
    fresh_positive = int(catalyst.get("fresh_positive_count") or 0)
    strongest_positive = safe_float(catalyst.get("strongest_positive_score"), 0.0) or 0.0

    if fresh_specific:
        points += 6
        reasons.append(f"{fresh_specific} fresh/recent specific catalyst item(s) detected.")
    if fresh_positive:
        points += 5
        reasons.append(f"{fresh_positive} fresh positive catalyst item(s) detected.")
    if strongest_positive >= 6:
        points += 7
    elif strongest_positive >= 3:
        points += 4
    return min(20.0, points), reasons


def _risk_score(
    metrics: dict[str, Any],
    catalyst: dict[str, Any],
) -> tuple[float, list[str]]:
    risk = 0.0
    warnings: list[str] = []

    spread = safe_float(metrics.get("spread_pct"))
    if spread is not None:
        if spread >= 5:
            risk += 35
            warnings.append(f"Very wide spread ({spread:.2f}%).")
        elif spread >= 3:
            risk += 25
            warnings.append(f"Wide spread ({spread:.2f}%).")
        elif spread >= 1.5:
            risk += 12

    dollar_volume = safe_float(metrics.get("dollar_volume"), 0.0) or 0.0
    if dollar_volume < 250_000:
        risk += 25
        warnings.append("Very low current dollar volume.")
    elif dollar_volume < 1_000_000:
        risk += 12
        warnings.append("Thin current dollar volume.")

    price = safe_float(metrics.get("price"))
    if price is not None and price < 1:
        risk += 12
        warnings.append("Sub-$1 price increases microcap execution/manipulation risk.")

    dilution_count = int(catalyst.get("fresh_dilution_count") or 0)
    structural_count = int(catalyst.get("fresh_structural_risk_count") or 0)
    if dilution_count:
        risk += min(45.0, 30.0 + 5.0 * (dilution_count - 1))
        warnings.append("Fresh offering/dilution evidence is present.")
    elif structural_count:
        risk += min(30.0, 18.0 + 4.0 * (structural_count - 1))
        warnings.append("Fresh structural-risk evidence is present.")

    day_change = safe_float(metrics.get("day_change_pct"))
    vwap_distance = safe_float(metrics.get("vwap_distance_pct"))
    if day_change is not None and day_change >= 50:
        risk += 12
        warnings.append("The stock is already extremely extended on the day.")
    if vwap_distance is not None and vwap_distance >= 15:
        risk += 12
        warnings.append("Price is far above VWAP; chase/reversal risk is elevated.")

    return _clamp(risk), warnings


def _liquidity_quality_points(metrics: dict[str, Any]) -> tuple[float, list[str]]:
    points = 0.0
    reasons: list[str] = []
    dollar_volume = safe_float(metrics.get("dollar_volume"), 0.0) or 0.0
    spread = safe_float(metrics.get("spread_pct"))
    if dollar_volume >= 20_000_000:
        points += 9
    elif dollar_volume >= 5_000_000:
        points += 7
    elif dollar_volume >= 1_000_000:
        points += 4

    if spread is not None:
        if spread <= 0.5:
            points += 6
        elif spread <= 1.0:
            points += 4
        elif spread <= 2.0:
            points += 2

    if points >= 10:
        reasons.append("Liquidity is relatively strong for an explosive-move candidate.")
    return min(15.0, points), reasons


def activation_state(
    metrics: dict[str, Any],
    features: dict[str, Any],
    *,
    profile_score: float,
    risk_score: float,
) -> str:
    rvol = safe_float(metrics.get("relative_volume"), 0.0) or 0.0
    day_change = safe_float(metrics.get("day_change_pct"), 0.0) or 0.0
    vwap_distance = safe_float(metrics.get("vwap_distance_pct"), 0.0) or 0.0
    distance_high = safe_float(metrics.get("distance_from_high_pct"))
    acceleration = safe_float(features.get("volume_acceleration_ratio"), 0.0) or 0.0

    if day_change >= 45 or vwap_distance >= 15:
        return "EXTENDED / CHASE RISK"
    if (
        profile_score >= 65
        and risk_score < 60
        and rvol >= 3
        and day_change >= 8
        and metrics.get("above_vwap")
        and (distance_high is None or distance_high <= 7)
    ):
        return "ACTIVE"
    if (
        profile_score >= 50
        and risk_score < 70
        and rvol >= 2
        and (acceleration >= 1.5 or features.get("consolidation_then_expansion_up") is True)
    ):
        return "IGNITING"
    if profile_score >= 45:
        return "EARLY WATCH"
    return "LOW / INCOMPLETE"


def score_explosive_profile(
    metrics: dict[str, Any],
    daily_profile: dict[str, Any],
    market_features: dict[str, Any],
    catalyst: dict[str, Any],
) -> dict[str, Any]:
    """Transparent v0 heuristic. The score is NOT a probability."""
    features = dict(market_features.get("features") or market_features or {})

    component_builders = (
        ("volume_attention", _volume_attention_points(metrics, features)),
        ("price_activation", _activation_points(metrics, features)),
        ("stored_energy", _stored_energy_points(daily_profile, features)),
        ("historical_explosiveness", _historical_explosiveness_points(daily_profile)),
        ("catalyst", _catalyst_points(catalyst)),
        ("liquidity_quality", _liquidity_quality_points(metrics)),
    )
    components = {name: round(points, 2) for name, (points, _) in component_builders}
    reasons: list[str] = []
    for _, (_, component_reasons) in component_builders:
        reasons.extend(component_reasons)

    raw_score = sum(components.values())
    risk, warnings = _risk_score(metrics, catalyst)
    # Risk is intentionally visible separately. Only a modest adjustment is made
    # here so a dangerous stock can still be recognized as explosive while being
    # clearly flagged as dangerous.
    adjusted = _clamp(raw_score - risk * 0.20)
    state = activation_state(
        metrics,
        features,
        profile_score=adjusted,
        risk_score=risk,
    )
    return {
        "model_version": EXPLOSIVE_MODEL_VERSION,
        "profile_score": round(adjusted, 1),
        "raw_profile_score": round(_clamp(raw_score), 1),
        "risk_score": round(risk, 1),
        "activation_state": state,
        "components": components,
        "reasons": reasons[:10],
        "warnings": warnings[:10],
        "score_is_probability": False,
        "validation_status": "experimental_unvalidated",
    }


def build_explosive_candidate(
    symbol: str,
    snapshot: dict[str, Any],
    daily_rows: list[dict[str, Any]],
    intraday_rows: list[dict[str, Any]],
    news_items: list[dict[str, Any]],
    *,
    sec_items: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    average_volume = average_completed_daily_volume(daily_rows)
    metrics = snapshot_metrics(
        symbol,
        snapshot,
        average_daily_volume=average_volume,
        news_items=news_items,
        now=now,
    )
    if metrics is None:
        return None

    completed_intraday = completed_intraday_rows(intraday_rows, now=now)
    market_features = build_market_features(completed_intraday)
    daily = completed_daily_profile(daily_rows)
    catalysts = catalyst_profile(news_items, sec_items, as_of=now or utc_now())
    scoring = score_explosive_profile(metrics, daily, market_features, catalysts)

    missing = list(market_features.get("missing_data") or [])
    # These inputs are intentionally not fabricated from price data.
    missing.extend(["float", "market_cap"])
    return {
        "symbol": symbol,
        "metrics": metrics,
        "daily_profile": daily,
        "market_features": market_features,
        "catalyst_profile": catalysts,
        "structural_supply": {
            "float": None,
            "market_cap": None,
            "status": "provider_not_connected",
        },
        "missing_data": sorted(set(missing)),
        **scoring,
    }


def scan_explosive_candidates(
    market: AlpacaMarketData,
    symbols: list[str],
    *,
    progress: Callable[[str], None] | None = None,
    history_days: int = DEFAULT_HISTORY_DAYS,
    news_hours: int = DEFAULT_NEWS_HOURS,
    sec_items_by_symbol: dict[str, list[dict[str, Any]]] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Scan a bounded candidate universe with shared snapshot/history/news calls."""
    clean = parse_symbols(symbols)
    if not clean:
        return []
    if len(clean) > MAX_EXPLOSIVE_SCAN_SYMBOLS:
        raise AppError(
            f"Explosive Stock Scanner supports up to {MAX_EXPLOSIVE_SCAN_SYMBOLS} symbols per run."
        )

    reference = now or utc_now()
    if progress:
        progress(f"Loading snapshots for {len(clean)} candidates…")
    snapshots = market.snapshots(clean)

    history_end = AlpacaMarketData._history_cache_cutoff_utc(reference)
    history_start = history_end - timedelta(days=max(30, int(history_days)))
    if progress:
        progress("Loading completed daily history…")
    daily = market.bars(
        clean,
        start=history_start,
        end=history_end,
        timeframe="1Day",
        max_pages=16,
    )

    if progress:
        progress("Loading current-session price/volume structure…")
    try:
        intraday = market.bars(
            clean,
            start=_session_start(reference),
            end=reference,
            timeframe="1Min",
            feed=market.live_feed,
            max_pages=12,
        )
    except AppError:
        intraday = {symbol: [] for symbol in clean}

    if progress:
        progress("Checking fresh catalyst evidence…")
    try:
        news = market.news(clean, hours=max(1, int(news_hours)))
    except AppError:
        news = {}

    results: list[dict[str, Any]] = []
    sec_lookup = sec_items_by_symbol or {}
    for symbol in clean:
        snapshot = snapshots.get(symbol)
        if not snapshot:
            continue
        candidate = build_explosive_candidate(
            symbol,
            snapshot,
            daily.get(symbol, []),
            intraday.get(symbol, []),
            news.get(symbol, []),
            sec_items=sec_lookup.get(symbol, []),
            now=reference,
        )
        if candidate is not None:
            results.append(candidate)

    state_rank = {
        "ACTIVE": 5,
        "IGNITING": 4,
        "EARLY WATCH": 3,
        "EXTENDED / CHASE RISK": 2,
        "LOW / INCOMPLETE": 1,
    }
    results.sort(
        key=lambda item: (
            state_rank.get(str(item.get("activation_state") or ""), 0),
            safe_float(item.get("profile_score"), 0.0) or 0.0,
            -(safe_float(item.get("risk_score"), 100.0) if safe_float(item.get("risk_score"), 100.0) is not None else 100.0),
            safe_float((item.get("metrics") or {}).get("relative_volume"), 0.0) or 0.0,
        ),
        reverse=True,
    )
    return results


def forward_explosion_labels(
    rows: list[dict[str, Any]],
    anchor_index: int,
    *,
    horizons: tuple[int, ...] = FORWARD_HORIZONS,
    thresholds_pct: tuple[float, ...] = EXPLOSION_THRESHOLDS_PCT,
) -> dict[str, Any]:
    """Create future-only labels for historical training/evaluation.

    Features must be built from rows[:anchor_index + 1]. This helper is called
    separately so future highs can never leak into the feature payload.
    """
    if anchor_index < 0 or anchor_index >= len(rows):
        raise ValueError("anchor_index is outside the supplied daily-bar history")
    anchor_close = safe_float((rows[anchor_index] or {}).get("c"))
    if anchor_close is None or anchor_close <= 0:
        raise ValueError("anchor row needs a positive close")

    labels: dict[str, Any] = {}
    for horizon in horizons:
        future = rows[anchor_index + 1 : anchor_index + 1 + max(1, int(horizon))]
        highs = [
            safe_float(item.get("h"))
            for item in future
            if isinstance(item, dict) and safe_float(item.get("h")) is not None
        ]
        max_return = (
            (max(highs) / anchor_close - 1.0) * 100.0
            if highs
            else None
        )
        labels[f"max_forward_return_{horizon}d_pct"] = max_return
        for threshold in thresholds_pct:
            key = f"hit_{int(threshold)}pct_within_{horizon}d"
            labels[key] = bool(max_return is not None and max_return >= threshold)
    return labels
