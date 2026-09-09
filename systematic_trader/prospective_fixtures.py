"""Explicit invented source guarantees; cannot claim live coverage."""
from .events import canonical_json
from .prospective_adapters import CATEGORIES

OPEN=1000;CLOSE=2000;ID='fixture:listing-A'


def fact(kind,entity=None,values=None,*,security_id=ID,symbol='SAME',start=900,end=2001,operation='original'):
    return dict(kind=kind,symbol=symbol,security_id=security_id,entity_id=entity or kind,operation=operation,
        effective_ns=start,effective_to_ns=end,values=values or {})


def raw(facts):return canonical_json(dict(version='prospective-fixture-v1',facts=facts))


def entering():
    return [fact('identity',values={'security_class':'common_stock','listing_venue':'fixture-X'}),
        fact('corporate_actions',values={'state':'explicit_no_actions'}),fact('round_lot',values={'shares_per_round_lot':100}),
        fact('trading_status',values={'state':'trading'}),fact('luld',values={'state':'normal','lower':'90','upper':'110'})]


def coverage():
    return [fact('coverage',entity='coverage:'+c,values={'category':c,'semantics':'explicit_fixture_interval'}) for c in CATEGORIES]


def complete(recorder,session='fixture-session'):
    recorder.register(session,OPEN,CLOSE,[ID]);recorder.ingest(session,'fixture',raw(entering()),received_ns=OPEN-1)
    recorder.ingest(session,'fixture',raw(coverage()),received_ns=CLOSE+1)
    return session
