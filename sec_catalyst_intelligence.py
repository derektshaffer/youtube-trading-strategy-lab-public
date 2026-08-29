"""Deterministic SEC EDGAR evidence for Trading Intelligence Lab.

The SEC layer intentionally does not infer that a filing is bullish or bearish beyond
narrow, auditable form/item semantics. It preserves the filing form, item numbers,
acceptance time, accession number, and primary-document URL as evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import gzip
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from youtube_strategy_engine import AppError, isoformat_utc, parse_symbols, safe_float


SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
SEC_MAX_RECENT_FILINGS = 1000

# The SEC currently publishes a fair-access ceiling of 10 requests/second. This
# client performs only two sequential requests for a normal ticker lookup + recent
# filing history and deliberately has no concurrency.
SEC_FAIR_ACCESS_REQUESTS_PER_SECOND = 10


def _parse_timestamp(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    # SEC acceptanceDateTime is commonly 20260829164512 or ISO-like.
    if re.fullmatch(r"\d{14}", text):
        try:
            return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sec_json(url: str, user_agent: str, *, timeout: int = 35) -> Any:
    agent = str(user_agent or "").strip()
    if not agent:
        raise AppError(
            "SEC filing intelligence needs SEC_USER_AGENT in Streamlit Secrets. "
            "Use a descriptive app/company name plus a real contact email, as required by SEC fair-access guidance."
        )
    request = Request(
        url,
        headers={
            "User-Agent": agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            if str(response.headers.get("Content-Encoding") or "").casefold() == "gzip":
                body = gzip.decompress(body)
            return json.loads(body.decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        if exc.code in {403, 429}:
            raise AppError(
                f"SEC EDGAR denied or rate-limited the request ({exc.code}). "
                "Verify SEC_USER_AGENT and retry later. " + body
            ) from exc
        raise AppError(f"SEC EDGAR request failed ({exc.code}): {body or 'No details supplied.'}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise AppError(f"SEC EDGAR could not be reached: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AppError("SEC EDGAR returned invalid JSON.") from exc


@dataclass(frozen=True)
class SecCompany:
    ticker: str
    cik: str
    name: str


class SecEdgarClient:
    """Small sequential client for public SEC ticker mapping and submission history."""

    def __init__(self, user_agent: str):
        self.user_agent = str(user_agent or "").strip()
        if not self.user_agent:
            raise AppError(
                "SEC filing intelligence needs SEC_USER_AGENT in Streamlit Secrets. "
                "Use a descriptive app/company name plus a real contact email."
            )
        self._ticker_map: dict[str, SecCompany] | None = None

    def ticker_map(self) -> dict[str, SecCompany]:
        if self._ticker_map is not None:
            return self._ticker_map
        raw = _sec_json(SEC_TICKER_MAP_URL, self.user_agent)
        if not isinstance(raw, dict):
            raise AppError("SEC ticker mapping returned an unexpected response.")
        mapping: dict[str, SecCompany] = {}
        for item in raw.values():
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker") or "").strip().upper()
            cik_number = item.get("cik_str")
            if not ticker or cik_number is None:
                continue
            try:
                cik = f"{int(cik_number):010d}"
            except (TypeError, ValueError):
                continue
            mapping[ticker] = SecCompany(
                ticker=ticker,
                cik=cik,
                name=str(item.get("title") or "").strip(),
            )
        self._ticker_map = mapping
        return mapping

    def resolve_ticker(self, ticker: str) -> SecCompany:
        clean = parse_symbols([ticker])
        if len(clean) != 1:
            raise AppError("Enter exactly one valid U.S. stock ticker for SEC research.")
        symbol = clean[0]
        company = self.ticker_map().get(symbol)
        if company is None:
            raise AppError(
                f"SEC's ticker/CIK mapping does not currently contain {symbol}. "
                "The symbol may be non-U.S., newly listed, inactive, or absent from the SEC association file."
            )
        return company

    def recent_filings(
        self,
        ticker: str,
        *,
        days: int = 180,
        limit: int = 200,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        company = self.resolve_ticker(ticker)
        data = _sec_json(
            SEC_SUBMISSIONS_URL.format(cik=company.cik),
            self.user_agent,
        )
        if not isinstance(data, dict):
            raise AppError("SEC submissions API returned an unexpected response.")

        recent = ((data.get("filings") or {}).get("recent") or {})
        if not isinstance(recent, dict):
            recent = {}
        fields = [
            "accessionNumber",
            "filingDate",
            "reportDate",
            "acceptanceDateTime",
            "act",
            "form",
            "fileNumber",
            "filmNumber",
            "items",
            "size",
            "isXBRL",
            "isInlineXBRL",
            "primaryDocument",
            "primaryDocDescription",
        ]
        lengths = [
            len(recent.get(field) or [])
            for field in fields
            if isinstance(recent.get(field), list)
        ]
        count = min(max(lengths, default=0), SEC_MAX_RECENT_FILINGS)
        cutoff = (as_of or datetime.now(timezone.utc)) - timedelta(days=max(1, int(days)))
        rows: list[dict[str, Any]] = []

        for index in range(count):
            row: dict[str, Any] = {}
            for field in fields:
                values = recent.get(field) or []
                row[field] = values[index] if isinstance(values, list) and index < len(values) else None

            accepted = _parse_timestamp(row.get("acceptanceDateTime"))
            if accepted is None:
                accepted = _parse_timestamp(row.get("filingDate"))
            if accepted is not None and accepted < cutoff:
                continue

            accession = str(row.get("accessionNumber") or "").strip()
            primary_document = str(row.get("primaryDocument") or "").strip()
            accession_compact = accession.replace("-", "")
            filing_url = None
            if accession_compact and primary_document:
                filing_url = (
                    f"{SEC_ARCHIVES_BASE}/{int(company.cik)}/{accession_compact}/{primary_document}"
                )
            row.update(
                {
                    "ticker": company.ticker,
                    "company_name": company.name,
                    "cik": company.cik,
                    "accepted_at": isoformat_utc(accepted) if accepted is not None else None,
                    "filing_url": filing_url,
                }
            )
            rows.append(row)
            if len(rows) >= max(1, min(500, int(limit))):
                break

        return {
            "ticker": company.ticker,
            "company_name": company.name or str(data.get("name") or ""),
            "cik": company.cik,
            "filings": rows,
        }


def _base_form(form: Any) -> str:
    text = str(form or "").strip().upper()
    return text[:-2] if text.endswith("/A") else text


def _item_set(raw: Any) -> set[str]:
    text = str(raw or "")
    return {
        value.strip()
        for value in re.split(r"[,;\s]+", text)
        if re.fullmatch(r"\d+\.\d+", value.strip())
    }


def classify_sec_filing(filing: dict[str, Any]) -> dict[str, Any]:
    """Map SEC forms/items to conservative catalyst/risk semantics."""
    form = str(filing.get("form") or "").strip().upper()
    base_form = _base_form(form)
    items = _item_set(filing.get("items"))
    category = "SEC filing / informational"
    score = 0.0
    severity = "info"
    dilution_risk = False
    specific = False
    rationale = "Filing preserved as evidence; no directional interpretation assigned."

    # Prospectus/registration forms can increase future or immediate share supply.
    if base_form in {"424B1", "424B3", "424B4", "424B5", "424B7", "424B8"}:
        category = "offering prospectus / dilution risk"
        score = -8.0
        severity = "high"
        dilution_risk = True
        specific = True
        rationale = "Prospectus filing can represent securities being offered or sold."
    elif base_form in {"S-1", "S-3", "F-1", "F-3"}:
        category = "securities registration / potential dilution"
        score = -6.0
        severity = "medium"
        dilution_risk = True
        specific = True
        rationale = "Registration statement can create capacity for future securities issuance."
    elif base_form == "EFFECT":
        category = "registration statement effective"
        score = -4.0
        severity = "medium"
        dilution_risk = True
        specific = True
        rationale = "SEC effectiveness can make a registered securities transaction actionable."
    elif base_form in {"NT 10-K", "NT 10-Q"}:
        category = "late periodic report"
        score = -5.0
        severity = "medium"
        specific = True
        rationale = "Company filed a notice that a required periodic report will be late."
    elif base_form == "144":
        category = "proposed affiliate / insider sale"
        score = -3.0
        severity = "medium"
        specific = True
        rationale = "Form 144 is notice of a proposed sale of restricted or control securities."
    elif base_form in {"SC 13D", "SC 13D/A"}:
        category = "active ownership disclosure"
        score = 3.0
        severity = "medium"
        specific = True
        rationale = "Schedule 13D can indicate a significant holder with active intent; terms still require review."
    elif base_form in {"SC 13G", "SC 13G/A"}:
        category = "significant ownership disclosure"
        score = 1.0
        severity = "info"
        specific = True
        rationale = "Schedule 13G reports significant ownership but does not by itself imply activist intent."
    elif base_form == "8-K":
        if "3.02" in items:
            category = "unregistered equity issuance / dilution risk"
            score = -8.0
            severity = "high"
            dilution_risk = True
            specific = True
            rationale = "8-K Item 3.02 reports unregistered sales of equity securities."
        elif "2.02" in items:
            category = "earnings / financial results filing"
            score = 4.0
            severity = "medium"
            specific = True
            rationale = "8-K Item 2.02 reports results of operations or financial condition; direction needs the actual results."
        elif "1.01" in items:
            category = "material definitive agreement"
            score = 4.0
            severity = "medium"
            specific = True
            rationale = "8-K Item 1.01 reports entry into a material definitive agreement; economic terms still need review."
        elif "2.01" in items:
            category = "acquisition / disposition filing"
            score = 4.0
            severity = "medium"
            specific = True
            rationale = "8-K Item 2.01 reports completion of an acquisition or disposition; transaction terms determine direction."
        elif "5.02" in items:
            category = "management / board change"
            score = 0.0
            severity = "medium"
            specific = True
            rationale = "8-K Item 5.02 reports director or officer changes; no directional assumption is made."
        elif "8.01" in items:
            category = "other material event"
            score = 0.0
            severity = "medium"
            specific = True
            rationale = "8-K Item 8.01 reports another event the company considers material; filing content requires review."
    elif base_form == "4":
        category = "insider transaction filing"
        score = 0.0
        severity = "medium"
        specific = True
        rationale = "Form 4 reports insider ownership changes; buy/sell direction is not inferred without transaction-level parsing."
    elif base_form in {"10-Q", "10-K", "20-F", "6-K"}:
        category = "periodic / foreign issuer report"
        score = 0.0
        severity = "info"
        specific = True
        rationale = "Periodic report is material company evidence, but filing form alone does not indicate positive or negative results."
    elif base_form in {"DEF 14A", "PRE 14A"}:
        category = "shareholder / proxy filing"
        score = 0.0
        severity = "info"
        specific = True
        rationale = "Proxy filing can contain votes, compensation, financing, or corporate-action details; direction requires content review."
    elif base_form == "RW":
        category = "registration withdrawn"
        score = 0.0
        severity = "medium"
        specific = True
        rationale = "Withdrawal can remove a pending registration, but the reason and market impact require review."

    accepted = _parse_timestamp(filing.get("accepted_at") or filing.get("acceptanceDateTime") or filing.get("filingDate"))
    description = str(filing.get("primaryDocDescription") or "").strip()
    headline = f"{form or 'SEC filing'}"
    if description and description.casefold() not in headline.casefold():
        headline += f" — {description}"
    return {
        **filing,
        "headline": headline,
        "form": form,
        "base_form": base_form,
        "items_list": sorted(items),
        "category": category,
        "score": score,
        "severity": severity,
        "is_specific_catalyst": specific,
        "is_positive": score > 0,
        "is_negative": score < 0,
        "is_dilution_risk": dilution_risk,
        "rationale": rationale,
        "published_at": isoformat_utc(accepted) if accepted is not None else filing.get("accepted_at"),
        "evidence_type": "sec_filing",
        "source": "SEC EDGAR",
        "url": filing.get("filing_url"),
    }


def classify_recent_sec_filings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        classify_sec_filing(item)
        for item in (payload.get("filings") or [])
        if isinstance(item, dict)
    ]
    rows.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    return rows


def sec_filing_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    specific = [row for row in rows if row.get("is_specific_catalyst")]
    dilution = [row for row in specific if row.get("is_dilution_risk")]
    high = [row for row in specific if str(row.get("severity") or "") == "high"]
    return {
        "filings": len(rows),
        "specific_filings": len(specific),
        "dilution_risk_filings": len(dilution),
        "high_severity_filings": len(high),
        "latest_filing_at": max(
            (str(row.get("published_at") or "") for row in rows),
            default=None,
        ),
    }
