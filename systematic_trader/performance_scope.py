"""Process-local exact-event capability; not a sandbox against the OS owner."""
from contextvars import ContextVar
from contextlib import contextmanager
from .events import ContractError, digest

_ACTIVE=ContextVar('certified_performance_events',default=None)
_TOKEN=object()


def require_event(event):
    if event.get('origin')=='fixture':return
    active=_ACTIVE.get()
    if event.get('origin')!='import' or active is None or digest(event) not in active:
        raise ContractError('trusted_runner_required_for_nonfixture_performance')


def require_decision(decision):
    if decision.get('key',[None,None,None])[2]=='fixture':return
    if _ACTIVE.get() is None:raise ContractError('trusted_runner_required_for_nonfixture_performance')


@contextmanager
def _activate(events, token):
    if token is not _TOKEN:raise ContractError('verified_runner_handoff_required')
    marker=_ACTIVE.set(frozenset(digest(e) for e in events))
    try:yield
    finally:_ACTIVE.reset(marker)
