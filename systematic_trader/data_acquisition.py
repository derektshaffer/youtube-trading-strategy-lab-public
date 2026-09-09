"""Bounded, resumable raw historical acquisition using existing secure access.

Only fixed read-only market/reference endpoints exist here. Raw successful
responses are saved before interpretation; credentials and error bodies are not.
"""
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from .events import ContractError, canonical_json, digest
from .ledger import strict_json
from .research_check import save
from .tradier import NoRedirect
from .research_split import protect_holdout

ENDPOINTS = {
    "bars": "https://data.alpaca.markets/v2/stocks/bars",
    "quotes": "https://data.alpaca.markets/v2/stocks/quotes",
    "trades": "https://data.alpaca.markets/v2/stocks/trades",
    "actions": "https://data.alpaca.markets/v1/corporate-actions",
    "calendar": "https://paper-api.alpaca.markets/v2/calendar",
    "assets": "https://paper-api.alpaca.markets/v2/assets",
}
MAX_RESPONSE = 32 * 1024 * 1024


class HistoricalAccess:
    def __init__(self, credentials, *, opener=None):
        if len(credentials) != 2 or not all(isinstance(x,str) and x for x in credentials):
            raise ContractError("historical_credentials_required")
        self._credentials = credentials
        self._opener = opener or build_opener(NoRedirect())

    def get(self, kind, params):
        if kind not in ENDPOINTS:
            raise ContractError("historical_endpoint_refused")
        if kind in {"bars","trades","quotes"}:
            protect_holdout(params.get("start"),params.get("end"))
            if params.get("feed") != "sip" or params.get("sort") != "asc" or "asof" not in params:
                raise ContractError("explicit_historical_feed_order_identity_required")
            if kind == "bars" and params.get("adjustment") != "raw":
                raise ContractError("raw_historical_adjustment_required")
        headers = {"APCA-API-KEY-ID":self._credentials[0], "APCA-API-SECRET-KEY":self._credentials[1], "Accept":"application/json"}
        try:
            with self._opener.open(Request(ENDPOINTS[kind]+"?"+urlencode(params),headers=headers,method="GET"),timeout=45) as response:
                raw = response.read(MAX_RESPONSE+1)
                if response.status != 200:
                    raise ContractError("historical_non_success_response")
            if len(raw)>MAX_RESPONSE:
                raise ContractError("historical_response_too_large")
            if any(value.encode() in raw for value in self._credentials):
                raise ContractError("historical_credential_reflection_refused")
            return raw
        except HTTPError as exc:
            raise ContractError(f"historical_http_{exc.code}") from None
        except ContractError:
            raise
        except Exception:
            raise ContractError("historical_transport_unavailable") from None


def acquire(access, directory, *, kind, params, max_pages=200):
    """Resume identical queries; never call a truncated chain complete."""
    if kind not in ENDPOINTS or type(max_pages) is not int or max_pages<1 or "page_token" in params:
        raise ContractError("invalid_historical_acquisition_request")
    if kind in {"bars","trades","quotes"}:
        protect_holdout(params.get("start"),params.get("end"))
    path=Path(directory);path.mkdir(parents=True,exist_ok=True,mode=0o700)
    request=dict(version="raw-history-acquisition-v1",provider="alpaca",kind=kind,endpoint=ENDPOINTS[kind],params=params)
    query_hash=digest(request)
    request_path=path/"request.json"
    if request_path.exists():
        if json.loads(request_path.read_text())!={**request,"query_hash":query_hash}:
            raise ContractError("historical_resume_query_mismatch")
    else:save(request_path,{**request,"query_hash":query_hash})
    previous="0"*64;token=None;seen_tokens=set();pages=[]
    for index in range(max_pages):
        page=path/f"{index:06d}.raw.json";meta_path=path/f"{index:06d}.meta.json"
        query={**params, **({"page_token":token} if token else {})}
        if page.exists() and meta_path.exists():
            raw=page.read_bytes();meta=json.loads(meta_path.read_text())
            if (meta["sha256"]!=hashlib.sha256(raw).hexdigest() or meta["previous_hash"]!=previous or
                    meta["request_hash"]!=digest(query) or meta["query_hash"]!=query_hash):
                raise ContractError("historical_saved_page_integrity_failure")
        else:
            if page.exists() or meta_path.exists():
                # Preserve partial raw evidence, never overwrite or label a retry identical.
                raise ContractError("historical_orphan_page_requires_review")
            raw=access.get(kind,query)
            meta=dict(index=index,query_hash=query_hash,request_hash=digest(query),previous_hash=previous,
                      sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),retrieved_ns=time.time_ns())
            with page.open("xb") as handle:
                os.chmod(page,0o600);handle.write(raw);handle.flush();os.fsync(handle.fileno())
            save(meta_path,meta)
        # Successful bytes are durable even if parsing or payload validation fails.
        payload=strict_json(raw)
        if kind in {"calendar","assets"}:
            if not isinstance(payload,list):raise ContractError("historical_reference_shape_mismatch")
            next_token=None
        else:
            field="corporate_actions" if kind=="actions" else kind
            if not isinstance(payload,dict) or not isinstance(payload.get(field),dict) or "next_page_token" not in payload:
                raise ContractError("historical_page_shape_mismatch")
            next_token=payload["next_page_token"]
            if next_token is not None and (not isinstance(next_token,str) or not next_token):
                raise ContractError("historical_page_token_invalid")
        previous=digest(meta);pages.append(meta)
        if next_token is None:
            result=dict(**request,query_hash=query_hash,pages=len(pages),bytes=sum(p["bytes"] for p in pages),
                        page_chain_head=previous,complete=True,certified=False,execution_authority="none")
            completion=path/"complete.json"
            if completion.exists():
                if json.loads(completion.read_text())!=result:raise ContractError("historical_completion_mismatch")
            else:save(completion,result)
            return result
        if next_token in seen_tokens:
            raise ContractError("historical_repeated_page_token")
        seen_tokens.add(next_token);token=next_token
        time.sleep(.35)  # Below the documented basic request budget; no retry loop.
    raise ContractError("historical_page_budget_exhausted")


def verified_pages(directory):
    """Offline integrity-checked page iterator. A missing tail is never complete."""
    path=Path(directory)
    request=json.loads((path/"request.json").read_text())
    complete=json.loads((path/"complete.json").read_text())
    if request.get("kind") in {"bars","trades","quotes"}:
        protect_holdout(request["params"].get("start"),request["params"].get("end"))
    if request["query_hash"]!=digest({k:v for k,v in request.items() if k!="query_hash"}):
        raise ContractError("historical_request_integrity_failure")
    if type(complete.get("pages")) is not int or complete["pages"]<1:
        raise ContractError("historical_completion_page_count_invalid")
    previous="0"*64;token=None;total_bytes=0;seen_tokens=set()
    for index in range(complete["pages"]):
        raw=(path/f"{index:06d}.raw.json").read_bytes();meta=json.loads((path/f"{index:06d}.meta.json").read_text())
        query={**request["params"], **({"page_token":token} if token else {})}
        if (meta["index"]!=index or meta["bytes"]!=len(raw) or
            meta["sha256"]!=hashlib.sha256(raw).hexdigest() or meta["previous_hash"]!=previous or
            meta["query_hash"]!=request["query_hash"] or meta["request_hash"]!=digest(query)):
            raise ContractError("historical_page_integrity_failure")
        previous=digest(meta);payload=strict_json(raw)
        total_bytes+=len(raw)
        if request["kind"] in {"assets","calendar"}:
            if not isinstance(payload,list):raise ContractError("historical_reference_shape_mismatch")
            token=None
        else:
            field="corporate_actions" if request["kind"]=="actions" else request["kind"]
            if not isinstance(payload,dict) or not isinstance(payload.get(field),dict) or "next_page_token" not in payload:
                raise ContractError("historical_page_shape_mismatch")
            token=payload["next_page_token"]
            if token is not None:
                if not isinstance(token,str) or not token:raise ContractError("historical_page_token_invalid")
                if token in seen_tokens:raise ContractError("historical_repeated_page_token")
                seen_tokens.add(token)
        if index<complete["pages"]-1 and not token:raise ContractError("historical_premature_page_chain_end")
        yield payload,meta
    if token or previous!=complete["page_chain_head"] or complete["query_hash"]!=request["query_hash"]:
        raise ContractError("historical_page_chain_incomplete")
    expected=dict(**request,pages=complete["pages"],bytes=total_bytes,page_chain_head=previous,
                  complete=True,certified=False,execution_authority="none")
    if complete!=expected:
        raise ContractError("historical_completion_manifest_mismatch")
