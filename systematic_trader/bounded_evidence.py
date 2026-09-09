"""Small fixed-subset evidence predicates; no strategy execution or authority.

Keep quote eligibility separate from security trading status. UTP's own
specification requires Trading Action messages; a regular quote cannot clear a
halt or establish a tradable interval. Process dates are not action ex-dates.
"""
from datetime import date
from decimal import Decimal
import re
from .events import ContractError, digest, timestamp_ns
from .ledger import strict_json
from .security_master import claim


def cusip_valid(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Z0-9*@#]{8}[0-9]',value):return False
    total=0
    for i,c in enumerate(value[:8]):
        n=int(c) if c.isdigit() else ord(c)-55 if c.isalpha() else {'*':36,'@':37,'#':38}[c]
        n*=1+i%2;total+=n//10+n%10
    return (10-total%10)%10==int(value[-1])


class SEC13FExtract:
    """Decode retained web-tool extraction, never pretend it is original PDF bytes.

    A 13F list names a security/class/CUSIP, not its ticker or continuous listing
    interval. The symbol in context is the already fixed investigation label.
    """
    version='sec-13f-web-extract-v1'
    def decode(self,raw,context):
        text=strict_json(raw)
        if not isinstance(text,str):raise ContractError('sec_13f_web_text_required')
        expected=context['cusip']
        if not cusip_valid(expected):raise ContractError('invalid_cusip_check_digit')
        source=context['source_url']
        if source not in text or not re.fullmatch(r'https://www\.sec\.gov/files/investment/13flist2024q[34]\.pdf',source):
            raise ContractError('sec_13f_source_unverified')
        pattern=re.compile(r'\b'+re.escape(expected[:6])+r'\s+'+re.escape(expected[6:8])+r'\s+'+expected[8]+r'\s+\*?\s*'+re.escape(context['issuer_label'])+r'\s+'+re.escape(context['class_label'])+r'(?=\n|$)')
        matches=pattern.findall(text)
        if not matches:raise ContractError('sec_13f_identifier_claim_not_found')
        return [claim('sec_13f_web_extract',source+'#'+expected,context['symbol'],security_id='cusip:'+expected,
            identifiers={'cusip':expected},security_class=context['class_label'],identity_scope='dated_identifier_claim',
            known_basis='quarterly_reference_web_extraction_not_original_publication_clock',
            raw_fields=dict(cusip=expected,issuer_label=context['issuer_label'],class_label=context['class_label'],
                supporting_spans=sorted(set(matches)),representation='web_tool_extracted_text_not_original_pdf',
                symbol_is_investigation_label=True,listing_interval_certified=False))]


def register_security_adapter():
    from .security_master import ADAPTERS
    ADAPTERS[SEC13FExtract.version]=SEC13FExtract


def action_effective_date(kind,row):
    """Absent effective dates remain unknown; payable/process dates never substitute."""
    field='ex_date' if kind in {'cash_dividends','stock_dividends','forward_splits','reverse_splits','unit_splits','spin_offs','rights_distributions','capital_gains_distributions'} else 'effective_date'
    value=row.get(field)
    if value is None:return None
    try:return date.fromisoformat(value).isoformat()
    except (ValueError,TypeError):raise ContractError('invalid_action_effective_date') from None


def action_review(records, *, start, end, query_clock):
    if date.fromisoformat(start)>date.fromisoformat(end):raise ContractError('action_window_invalid')
    inside=[];outside=[];unknown=[]
    for item in records:
        day=action_effective_date(item['kind'],item['record'])
        row={**item,'economic_effective_date':day}
        (unknown if day is None else inside if start<=day<=end else outside).append(row)
    body=dict(version='bounded-action-basis-v1',start=start,end=end,query_clock=query_clock,
        relevant_known_actions=inside,known_actions_outside_window=outside,unknown_effective_actions=unknown,
        complete_effective_date_coverage=False,
        reason='process_date_query_does_not_certify_effective_date_coverage' if query_clock=='process_date' else 'independent_coverage_certificate_required',
        execution_authority='none')
    return {**body,'result_hash':digest(body)}


def quote_rule(row, *, decision_ns, available_ns, max_age_ns=2_000_000_000):
    """Native quote-quality predicate; does not return a fill or trading status."""
    reasons=[]
    if type(decision_ns) is not int or type(max_age_ns) is not int or max_age_ns<0:raise ContractError('invalid_quote_clock')
    event=timestamp_ns(row['t'])
    if available_ns is None:reasons.append('original_availability_unknown')
    elif type(available_ns) is not int or available_ns<event:raise ContractError('invalid_quote_availability')
    elif available_ns>=decision_ns:reasons.append('quote_not_strictly_available')
    if not 0<=decision_ns-event<=max_age_ns:reasons.append('stale_or_future_quote')
    if row.get('z')!='C':reasons.append('only_documented_utp_scope_supported')
    if row.get('c')!=['R']:reasons.append('quote_condition_not_regular_two_sided')
    try:
        bid,ask=Decimal(str(row['bp'])),Decimal(str(row['ap']))
        if not bid.is_finite() or not ask.is_finite() or bid<=0 or ask<=bid:reasons.append('nonpositive_locked_or_crossed')
    except (KeyError,ArithmeticError,ValueError):reasons.append('invalid_quote_price')
    if any(type(row.get(k)) is not int or row[k]<=0 for k in ('bs','as')):reasons.append('invalid_native_size')
    clock_reasons={'original_availability_unknown','quote_not_strictly_available','stale_or_future_quote'}
    return dict(version='bounded-utp-quote-rule-v1',quote_input_eligible=not reasons,
        native_structure_eligible=not any(r not in clock_reasons for r in reasons),reasons=reasons,
        native_size_unit='provider_round_lots',exact_share_conversion=None,
        # UTP permits a round lot of at least one share. This is a bound, not a
        # conversion or permission to pass guessed shares into the simulator.
        minimum_bid_shares=row['bs'] if type(row.get('bs')) is int and row['bs']>0 else None,
        minimum_ask_shares=row['as'] if type(row.get('as')) is int and row['as']>0 else None,
        security_trading_status='UNKNOWN',fill_allowed=False)


def supported_cells(protocol, findings):
    """Apply preregistered evidence criteria; never inspect outcomes or returns."""
    required=protocol['required_predicates'];cells=[]
    expected={(s,d) for s in protocol['symbols'] for d in protocol['sessions']}
    if set(findings)!=expected:raise ContractError('bounded_cell_coverage_mismatch')
    for symbol,day in sorted(expected):
        row=findings[(symbol,day)]
        if any(k not in required for k in row):raise ContractError('unregistered_selection_predicate')
        failed=[k for k in required if row.get(k) is not True]
        cells.append(dict(symbol=symbol,session=day,eligible=not failed,missing=failed))
    return dict(cells=cells,eligible=[c for c in cells if c['eligible']],selection_uses_outcomes=False,
                empty_subset_is_admitted=False,execution_authority='none')
