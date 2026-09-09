"""Invented observations for handoff mechanics, never market prices/outcomes."""
from pathlib import Path
from .certification_inputs import protocol
from .research_check import save
from .events import timestamp_ns


def write_evidence(directory, *, missing=None):
    root=Path(directory);root.mkdir(parents=True,exist_ok=False,mode=0o700)
    p=protocol();days=sorted(set(p['sessions'])|{d for ds in p['history_sessions'].values() for d in ds})
    securities={s:dict(identity='fixture:'+s,listing='fixture-listing',start='2024-12-10',end='2025-01-08',
        status='fixture-continuously-trading',actions='fixture-no-actions-complete',round_lot=100,conditions='fixture-native') for s in p['symbols']}
    rows=[dict(symbol=s,session=d,minute=m,price='100',volume=100,available_minute=m+1)
          for s in p['symbols'] for d in days for m in range(5) if (s,d,m)!=missing]
    quotes=[]
    for symbol in p['symbols']:
        for day in p['sessions']:
            t=day+'T09:35:00-05:00';stamp=timestamp_ns(t)
            quotes.append(dict(symbol=symbol,session=day,quote=dict(t=t,bp='100',ap='100.01',bs=1,**{'as':1},z='C',c=['R']),
                               available_ns=stamp+1_000_000,decision_ns=stamp+2_000_000))
    save(root/'fixture-evidence.json',dict(origin='synthetic-fixture',version='certificate-evidence-fixture-v1',
        securities=securities,observations=rows,quotes=quotes,revision='original_fixture_no_corrections'))
    return root
