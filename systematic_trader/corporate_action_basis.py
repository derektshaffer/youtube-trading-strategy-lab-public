"""Explicit PIT split basis; raw values are never overwritten or inferred."""
from decimal import Decimal
from copy import deepcopy
from .events import ContractError,digest


def split_basis(raw,actions,*,bar_ns,as_of_ns):
    result=deepcopy(raw);factor=Decimal(1);sources=[]
    for action in sorted(actions,key=lambda a:a['effective_ns']):
        if not action.get('verified_publication_ns') or not action.get('source_hash'):
            raise ContractError('split_original_publication_evidence_required')
        if action['verified_publication_ns']>as_of_ns:
            raise ContractError('future_split_knowledge_refused')
        if bar_ns<action['effective_ns']<=as_of_ns:
            new,old=Decimal(str(action['new_shares'])),Decimal(str(action['old_shares']))
            if not new.is_finite() or not old.is_finite() or min(new,old)<=0:raise ContractError('invalid_split_ratio')
            factor*=new/old;sources.append(action['source_hash'])
    for key in ('open','high','low','close','vwap'):result[key]=format(Decimal(str(raw[key]))/factor,'f')
    result['volume_shares']=format(Decimal(str(raw['volume_shares']))*factor,'f')
    body=dict(version='pit-split-basis-v1',raw=raw,raw_hash=digest(raw),adjusted=result,
        volume_factor=str(factor),as_of_ns=as_of_ns,source_hashes=sources,execution_authority='none')
    return {**body,'basis_hash':digest(body)}
