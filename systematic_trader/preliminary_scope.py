"""One-use exact-input capability for the legacy bar engine, never certification."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from .events import digest

_ACTIVE=ContextVar('preliminary_bar_engine',default=None)
_TOKEN=object()


def _fingerprint(rows,strategy,symbol,settings):
    return digest(dict(rows=rows,strategy=strategy,symbol=symbol,settings=asdict(settings)))


def require_preliminary(rows,strategy,symbol,settings,prepared):
    from youtube_strategy_engine import AppError
    scope=_ACTIVE.get()
    if any(x is not None for x in prepared) or scope is None or scope['used'] or scope['hash']!=_fingerprint(rows,strategy,symbol,settings):
        raise AppError('Legacy performance execution is disabled: an exact preliminary-research context is required; this is not certified execution.')
    scope['used']=True


@contextmanager
def _authorize(rows,strategy,symbol,settings,token):
    if token is not _TOKEN:raise ValueError('preliminary_context_required')
    marker=_ACTIVE.set(dict(hash=_fingerprint(rows,strategy,symbol,settings),used=False))
    try:yield
    finally:_ACTIVE.reset(marker)
