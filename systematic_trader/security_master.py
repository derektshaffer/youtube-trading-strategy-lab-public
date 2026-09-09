"""Raw-first provider-neutral historical security claims, not a survivor universe.

Issuer identity is distinct from security and listing identity. Knowledge time,
effective time and actual acquisition time are never interchangeable. This
boundary has no network, credential, live-order, or automatic promotion path.
"""
from copy import deepcopy
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Protocol
from .events import ContractError,digest,timestamp_ns
from .ledger import strict_json
from .research_check import save

VERSION='security-claim-v1'


class SecurityProvider(Protocol):
    version: str
    def decode(self,raw:bytes,context:dict)->list[dict]: ...


def clock(value):
    return timestamp_ns(value) if value is not None else None


def claim(provider,native_id,symbol,**fields):
    if not all(isinstance(x,str) and x for x in (provider,native_id,symbol)):
        raise ContractError('security_claim_identity_fields_required')
    result=dict(version=VERSION,provider=provider,native_id=native_id,symbol=symbol,security_id=None,issuer_id=None,
        listing_id=None,identifiers={},exchange=None,security_class=None,listing_date=None,delisting_date=None,
        first_traded_date=None,effective_ns=None,effective_end_ns=None,vendor_record_ns=None,vendor_created_ns=None,
        known_ns=None,known_basis='unknown',observed_ns=None,history_mode='dated_claim_only',identity_scope='issuer_claim',
        listing_status=None,tradability='UNKNOWN',corporate_action_links=[],raw_fields={},original_application_receipt_ns=None,
        production_qualified=False,execution_authority='none')
    result.update(deepcopy(fields));return result


class DatabentoReferenceJSON:
    version='databento-security-master-json-v1'
    def decode(self,raw,context):
        rows=strict_json(raw)
        if not isinstance(rows,list) or not rows:raise ContractError('security_master_json_array_required')
        if context.get('history_mode') not in {'point_in_time','latest_snapshot'}:
            raise ContractError('security_master_history_mode_required')
        result=[]
        for r in rows:
            if not isinstance(r,dict) or any(not r.get(k) for k in ('listing_id','security_id','nasdaq_symbol','exchange','ts_effective')):
                raise ContractError('security_master_native_fields_required')
            changed,created=clock(r.get('ts_record')),clock(r.get('ts_created'))
            # A retrospectively backfilled record was not in this vendor's
            # database before its creation, even when its effective date is old.
            known=max(changed,created) if changed is not None and created is not None else None
            # `symbol` echoes the query and may refer to another historical
            # name. `nasdaq_symbol` is the record's listing symbol. The vendor
            # documents ambiguous no-delimiter USNYSE class symbols; these
            # require an identifier join and never a guessed symbol mapping.
            result.append(claim('databento',str(r['listing_id']),r['nasdaq_symbol'],
                listing_id='databento:'+str(r['listing_id']),security_id='databento:'+str(r['security_id']),
                issuer_id='databento:'+str(r['issuer_id']) if r.get('issuer_id') else None,
                identifiers={k:r[k] for k in ('isin','us_code','figi','cik') if r.get(k)},
                exchange=r.get('exchange'),security_class=r.get('security_type'),listing_date=r.get('listing_date'),
                delisting_date=r.get('delisting_date'),listing_status=r.get('listing_status'),
                effective_ns=clock(r['ts_effective']),vendor_record_ns=changed,vendor_created_ns=created,known_ns=known,
                known_basis='max_vendor_record_and_database_creation_not_application_arrival' if known is not None else 'unknown',
                history_mode=context['history_mode'],identity_scope='listing_state',
                symbol_lookup_supported=r['exchange']!='USNYSE',raw_fields=r))
        return result


class _InlineFacts(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.depth=0;self.stack=[];self.facts={}
    def handle_starttag(self,tag,attrs):
        if tag in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:return
        self.depth+=1;attrs=dict(attrs)
        if tag in {'ix:nonnumeric','ix:nonfraction'} and attrs.get('name'):
            self.stack.append([self.depth,attrs['name'],[]])
    def handle_endtag(self,tag):
        for item in self.stack[:]:
            if item[0]==self.depth:
                self.facts.setdefault(item[1],set()).add(''.join(item[2]).strip());self.stack.remove(item)
        self.depth=max(0,self.depth-1)
    def handle_startendtag(self,tag,attrs):pass
    def handle_data(self,data):
        for item in self.stack:item[2].append(data)


class IssuerInlineXBRL:
    version='issuer-inline-xbrl-claims-v1'
    def decode(self,raw,context):
        parser=_InlineFacts();parser.feed(raw.decode('utf-8'));facts=parser.facts
        def one(name):
            values={v for v in facts.get('dei:'+name,set()) if v}
            if len(values)>1:raise ContractError('ambiguous_issuer_security_facts')
            return next(iter(values),None)
        symbol,cik=one('TradingSymbol'),one('EntityCentralIndexKey')
        if not symbol or not cik:raise ContractError('issuer_inline_security_facts_missing')
        return [claim('issuer_inline_xbrl',context['source_id'],symbol,issuer_id='sec-cik:'+cik.zfill(10),
            identifiers={'issuer_cik':cik.zfill(10)},exchange=one('SecurityExchangeName'),security_class=one('Security12bTitle'),
            raw_fields={k:sorted(v) for k,v in facts.items() if k.startswith('dei:')},
            known_basis='filing_content_only_publication_time_not_derived_from_reporting_period')]


class ReviewedDocument:
    version='reviewed-issuer-document-claims-v1'
    def decode(self,raw,context):
        from io import BytesIO
        if raw.startswith(b'%PDF'):
            from pypdf import PdfReader
            text=' '.join(p.extract_text() or '' for p in PdfReader(BytesIO(raw)).pages)
        else:
            from html import unescape
            import re
            text=unescape(re.sub('<[^>]+>',' ',raw.decode('utf-8')))
        clean=lambda s:' '.join(s.split()).casefold()
        # A manual extraction must retain exact supporting spans found in bytes.
        # It can only produce dated claims, never a continuous interval or PIT join.
        facts=context['facts'];spans=context['supporting_spans']
        if set(facts)-{'symbol','security_class','exchange'}:
            raise ContractError('reviewed_identifier_or_date_requires_dedicated_parser')
        if not spans or any(clean(span) not in clean(text) for span in spans):
            raise ContractError('reviewed_security_claim_span_not_found')
        if not all(clean(str(facts[k])) and any(clean(str(facts[k])) in clean(span) for span in spans) for k in ('symbol','security_class','exchange')):
            raise ContractError('reviewed_security_fact_not_found')
        return [claim('reviewed_issuer_document',context['source_id'],facts['symbol'],
            exchange=facts['exchange'],security_class=facts['security_class'],identifiers=facts.get('identifiers',{}),
            listing_date=facts.get('listing_date'),first_traded_date=facts.get('first_traded_date'),
            raw_fields=dict(reviewed_facts=facts,supporting_spans=spans,document_date=context.get('document_date')))]


ADAPTERS={c.version:c for c in (DatabentoReferenceJSON,IssuerInlineXBRL,ReviewedDocument)}


def ingest(raw,*,adapter_version,context,directory,observed_ns=None):
    if not isinstance(raw,bytes) or not raw or len(raw)>16*1024*1024:raise ContractError('security_raw_size_or_type')
    if observed_ns is not None and (type(observed_ns) is not int or observed_ns<=0):raise ContractError('security_acquisition_clock_invalid')
    root=Path(directory);root.mkdir(parents=True,exist_ok=False,mode=0o700)
    # Durable receipt precedes adapter selection, decoding and normalization.
    with (root/'source.raw').open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    (root/'source.raw').chmod(0o600)
    receipt=dict(version='security-source-receipt-v1',adapter_version=adapter_version,context=deepcopy(context),
                 sha256=hashlib.sha256(raw).hexdigest(),observed_ns=time.time_ns() if observed_ns is None else observed_ns)
    save(root/'receipt.json',{**receipt,'receipt_hash':digest(receipt)})
    try:
        if adapter_version not in ADAPTERS:raise ContractError('security_provider_not_registered')
        rows=ADAPTERS[adapter_version]().decode(raw,context)
        for row in rows:
            row.update(observed_ns=receipt['observed_ns'],raw_sha256=receipt['sha256'],adapter_version=adapter_version,
                       context_hash=digest(context),raw_receipt_hash=digest(receipt))
            if row['known_ns'] is not None and row['known_ns']>row['observed_ns']:
                raise ContractError('security_known_time_after_actual_acquisition')
            row['claim_hash']=digest(row)
        if len({r['claim_hash'] for r in rows})!=len(rows):raise ContractError('duplicate_security_record')
    except Exception as exc:
        save(root/'rejection.json',dict(reason=str(exc) if isinstance(exc,ContractError) else 'security_source_parse_failure',raw_sha256=receipt['sha256']))
        if isinstance(exc,ContractError):raise
        raise ContractError('security_source_parse_failure') from None
    body=dict(version='security-claim-bundle-v1',receipt_hash=digest(receipt),claims=rows,
        universe_complete=False,source_semantics_independently_certified=False,execution_authority='none')
    result={**body,'bundle_hash':digest(body)};save(root/'claims.json',result);return result


def read(directory):
    from .sparse_benchmark import verified_json
    root=Path(directory);receipt=verified_json(root/'receipt.json','receipt_hash');bundle=verified_json(root/'claims.json','bundle_hash')
    raw=(root/'source.raw').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=receipt['sha256'] or bundle['receipt_hash']!=receipt['receipt_hash']:
        raise ContractError('security_source_lineage_mismatch')
    for row in bundle['claims']:
        if (row['claim_hash']!=digest({k:v for k,v in row.items() if k!='claim_hash'}) or row['raw_sha256']!=receipt['sha256']
            or row['raw_receipt_hash']!=receipt['receipt_hash'] or row['observed_ns']!=receipt['observed_ns']
            or row['context_hash']!=digest(receipt['context']) or row['adapter_version']!=receipt['adapter_version']):
            raise ContractError('security_claim_integrity')
    # Rebuild from the retained bytes as well as checking hashes. A rehashed
    # normalized claim is not allowed to disagree with its declared source.
    expected=ADAPTERS[receipt['adapter_version']]().decode(raw,receipt['context'])
    for row in expected:
        row.update(observed_ns=receipt['observed_ns'],raw_sha256=receipt['sha256'],adapter_version=receipt['adapter_version'],
                   context_hash=digest(receipt['context']),raw_receipt_hash=receipt['receipt_hash'])
        row['claim_hash']=digest(row)
    if expected!=bundle['claims']:raise ContractError('security_normalization_replay_mismatch')
    return bundle


def resolve(claims,*,symbol,effective_ns,knowledge_ns):
    """Resolve listing state using both clocks; unknown/non-PIT claims never join."""
    eligible=[]
    for row in claims:
        if row['claim_hash']!=digest({k:v for k,v in row.items() if k!='claim_hash'}):raise ContractError('security_claim_integrity')
        if row['identity_scope']!='listing_state' or row['history_mode']!='point_in_time' or row['known_ns'] is None:continue
        if row['known_ns']<=knowledge_ns and row['effective_ns']<=effective_ns:
            eligible.append(row)
    by_listing={}
    for row in eligible:by_listing.setdefault(row['listing_id'],[]).append(row)
    matches=[]
    for rows in by_listing.values():
        rank=max((r['effective_ns'],r['known_ns']) for r in rows)
        latest=[r for r in rows if (r['effective_ns'],r['known_ns'])==rank]
        if len({digest(r['raw_fields']) for r in latest})>1:raise ContractError('conflicting_security_revision')
        row=latest[0]
        if row.get('symbol_lookup_supported',False) and row['symbol']==symbol and (row['effective_end_ns'] is None or effective_ns<row['effective_end_ns']):matches.append(row)
    if len(matches)>1:raise ContractError('ambiguous_cross_listing_symbol')
    return deepcopy(matches[0]) if matches else None
