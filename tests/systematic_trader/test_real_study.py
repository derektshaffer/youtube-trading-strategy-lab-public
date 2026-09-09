from copy import deepcopy
import pytest
from systematic_trader.real_study import require_certified_dataset
from systematic_trader.events import ContractError,digest


def certificate():
    c=dict(classification='NOT_CERTIFIED',certified=False,blocking_findings=['missing_original_availability'],profitability_evaluation_allowed=False)
    return {**c,'dataset_manifest_hash':digest(c)}


def test_uncertified_dataset_cannot_enter_strategy_or_simulator():
    with pytest.raises(ContractError,match='not_certified'):require_certified_dataset(certificate())


def test_editing_report_flags_does_not_bypass_certificate_integrity_or_authority():
    c=certificate();c.update(classification='CERTIFIED',certified=True,blocking_findings=[],profitability_evaluation_allowed=True)
    with pytest.raises(ContractError,match='hash_mismatch'):require_certified_dataset(c)
    c['dataset_manifest_hash']=digest({k:v for k,v in c.items() if k!='dataset_manifest_hash'})
    with pytest.raises(ContractError,match='authority_unconfigured'):require_certified_dataset(c)
