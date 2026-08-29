"""Print a safe aggregate fidelity audit for the durable private Trading Lab library.

This script is intended for GitHub Actions. It reads the configured private backup,
then prints only aggregate counts, example strategy names, and coarse AVWAP anchor
classifications. It never prints tokens, raw source text, or the full private library.
"""

from __future__ import annotations

import base64
from collections import defaultdict
import json
import os
import re
from typing import Any

import requests

from trading_intelligence_core import strategy_integrity_report, upgrade_native_strategy_rules
from trading_strategy_dna import is_family_source_strategy


def _required(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _github_json(url: str, token: str) -> dict[str, Any]:
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub returned an unexpected backup response.")
    return payload


def load_private_library() -> dict[str, Any]:
    repository = _required("GITHUB_BACKUP_REPOSITORY")
    token = _required("GITHUB_BACKUP_TOKEN")
    branch = str(os.environ.get("GITHUB_BACKUP_BRANCH") or "main").strip() or "main"
    path = str(
        os.environ.get("GITHUB_BACKUP_PATH")
        or "trading-intelligence-lab/intelligence_library.json"
    ).strip()

    from urllib.parse import quote

    owner_repo = "/".join(quote(piece, safe="") for piece in repository.split("/", 1))
    path_encoded = "/".join(quote(piece, safe="") for piece in path.split("/"))
    contents_url = (
        f"https://api.github.com/repos/{owner_repo}/contents/{path_encoded}"
        f"?ref={quote(branch, safe='')}"
    )
    metadata = _github_json(contents_url, token)
    encoded = metadata.get("content")
    encoding = str(metadata.get("encoding") or "").lower()

    if isinstance(encoded, str) and encoded.strip() and encoding == "base64":
        raw = base64.b64decode(encoded)
    else:
        git_url = str(metadata.get("git_url") or "").strip()
        if not git_url:
            raise RuntimeError("Backup metadata did not provide readable content or a Git blob URL.")
        blob = _github_json(git_url, token)
        blob_content = blob.get("content")
        if not isinstance(blob_content, str) or not blob_content.strip():
            raise RuntimeError("Git blob response did not contain backup content.")
        raw = base64.b64decode(blob_content)

    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Private backup root is not a JSON object.")
    return payload


def strategy_text(strategy: dict[str, Any]) -> str:
    pieces: list[Any] = [
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
    return " ".join(str(value or "") for value in pieces).casefold()


def avwap_tags(strategy: dict[str, Any]) -> list[str]:
    """Return coarse, non-sensitive anchor/mechanism labels from saved strategy text."""
    text = strategy_text(strategy)
    tags: list[str] = []

    checks = (
        ("ipo_day_one", ("ipo day-one", "ipo day one", "first trading day", "second minute")),
        ("multi_day", ("multi-day", "multi day", "day two", "day 2", "several days")),
        ("handoff_higher_low", ("handoff", "higher low", "higher-low", "trend acceleration")),
        ("swing_low", ("swing low", "pivot low", "major low")),
        ("swing_high", ("swing high", "pivot high", "major high")),
        ("previous_day_level", ("previous day high", "previous-day high", "prior day high", "previous day low", "previous-day low")),
        ("session_open", ("session open", "market open", "opening print", "opening price")),
        ("breakout_event", ("breakout", "break above", "range break", "high of day", "hod")),
        ("catalyst_event", ("catalyst", "news", "earnings", "press release", "offering")),
        ("reclaim", ("reclaim", "cross back above", "regain")),
        ("pullback_support", ("pullback", "support", "bounce", "rising avwap", "rising anchored vwap")),
        ("compression_pinch", ("compression", "pinch", "coiling", "squeeze between")),
        ("short_squeeze", ("short squeeze", "short-squeeze", "heavily shorted")),
    )
    for label, phrases in checks:
        if any(phrase in text for phrase in phrases):
            tags.append(label)

    if re.search(r"anchor(?:ed|ing)?\s+(?:the\s+)?(?:vwap|avwap)?\s*(?:at|to|from|on)\b", text):
        tags.append("explicit_anchor_language")
    if not tags:
        tags.append("unspecified")
    return tags


def main() -> None:
    library = load_private_library()
    all_strategies = [item for item in library.get("strategies") or [] if isinstance(item, dict)]
    source_strategies = [item for item in all_strategies if is_family_source_strategy(item)]

    gap_buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "strategy_count": 0,
            "sources": set(),
            "areas": set(),
            "examples": [],
            "limitation": "",
        }
    )
    blocked = 0
    partial = 0
    faithful = 0
    avwap_records: list[dict[str, Any]] = []
    avwap_tag_counts: dict[str, int] = defaultdict(int)

    for raw in source_strategies:
        strategy = upgrade_native_strategy_rules(raw)
        report = strategy_integrity_report(strategy)
        status = str(report.get("status") or "")
        if status == "blocked":
            blocked += 1
        elif status == "partial":
            partial += 1
        else:
            faithful += 1

        name = str(strategy.get("name") or "Unnamed strategy")
        source = str(
            strategy.get("source_title")
            or strategy.get("source_id")
            or strategy.get("source_type")
            or "Unknown source"
        )
        has_avwap_gap = False
        for item in report.get("requirements") or []:
            if not isinstance(item, dict) or item.get("modeled") or not item.get("critical"):
                continue
            label = str(item.get("label") or "Unmodeled requirement")
            bucket = gap_buckets[label]
            bucket["strategy_count"] += 1
            bucket["sources"].add(source)
            bucket["areas"].add(str(item.get("dimension") or "other"))
            if len(bucket["examples"]) < 3 and name not in bucket["examples"]:
                bucket["examples"].append(name)
            if not bucket["limitation"]:
                bucket["limitation"] = str(item.get("limitation") or "")
            if label == "Anchored VWAP structure":
                has_avwap_gap = True

        if has_avwap_gap:
            tags = avwap_tags(strategy)
            avwap_records.append({"name": name, "tags": tags})
            for tag in set(tags):
                avwap_tag_counts[tag] += 1

    ranked = sorted(
        gap_buckets.items(),
        key=lambda pair: (
            -int(pair[1]["strategy_count"]),
            -len(pair[1]["sources"]),
            pair[0],
        ),
    )

    print("STRATEGY_INTEGRITY_AUDIT_V2")
    print(f"SOURCE_STRATEGIES={len(source_strategies)}")
    print(f"FAITHFUL={faithful}")
    print(f"PARTIAL={partial}")
    print(f"BLOCKED={blocked}")
    print(f"UNIQUE_CRITICAL_GAPS={len(ranked)}")
    print("TOP_GAPS_BEGIN")
    for index, (label, data) in enumerate(ranked[:30], start=1):
        areas = ",".join(sorted(data["areas"]))
        examples = " | ".join(data["examples"])
        limitation = str(data["limitation"]).replace("\n", " ").strip()
        print(
            f"{index:02d}. {label} || strategies={data['strategy_count']} "
            f"|| sources={len(data['sources'])} || areas={areas} "
            f"|| examples={examples} || limitation={limitation}"
        )
    print("TOP_GAPS_END")

    print("AVWAP_TAG_COUNTS_BEGIN")
    for tag, count in sorted(avwap_tag_counts.items(), key=lambda pair: (-pair[1], pair[0])):
        print(f"{tag}={count}")
    print("AVWAP_TAG_COUNTS_END")
    print("AVWAP_STRATEGIES_BEGIN")
    for record in sorted(avwap_records, key=lambda item: item["name"]):
        print(f"{record['name']} || tags={','.join(record['tags'])}")
    print("AVWAP_STRATEGIES_END")


if __name__ == "__main__":
    main()
