"""Provider-neutral adapters with explicit offline transports and no credential discovery.

No HTTP/SDK transport is shipped in v1. A live flag cannot turn fixtures into a
real call. In particular these adapters never enter the legacy Gemini router.
"""
from dataclasses import asdict, dataclass
from typing import Callable, Protocol

from .contracts import ReviewError, digest, freeze, canonical


@dataclass(frozen=True)
class Route:
    provider: str
    model: str
    model_version: str
    configuration_version: str = 'offline-route-v1'
    mode: str = 'disabled'
    tier: str = 'unconfigured'
    max_calls: int = 10
    max_input_bytes: int = 65536
    max_input_tokens: int = 16384
    max_output_tokens: int = 2048
    retries: int = 0
    concurrency: int = 1

    def __post_init__(self):
        if self.provider not in {'openai', 'gemini'} or not self.model or not self.model_version:
            raise ReviewError('route_identity_required')
        if self.mode not in {'disabled', 'fixture', 'live'} or self.tier not in {'unconfigured', 'fixture', 'free'}:
            raise ReviewError('route_mode_or_tier_invalid')
        if self.retries != 0 or self.concurrency != 1:
            raise ReviewError('no_automatic_retry_or_concurrent_calls')
        for value, ceiling in ((self.max_calls, 10), (self.max_input_bytes, 65536),
                               (self.max_input_tokens, 16384), (self.max_output_tokens, 2048)):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ReviewError('budget_out_of_bounds')


class Reasoner(Protocol):
    route: Route
    def invoke(self, request: dict, storage) -> dict: ...


class IndependentReviewer(Reasoner, Protocol):
    pass


class FixtureTransport:
    """Explicit test seam; never loaded from a user job or environment variable."""
    def __init__(self, respond: Callable[[dict], dict]):
        self.respond = respond

    def __call__(self, request):
        return self.respond(freeze(request))


class _Adapter:
    provider = ''
    purposes = frozenset()

    def __init__(self, route: Route, transport: FixtureTransport | None = None):
        if route.provider != self.provider:
            raise ReviewError('adapter_provider_mismatch')
        self.route, self.transport = route, transport

    def invoke(self, request, storage):
        route = self.route
        if request['purpose'] not in self.purposes:
            raise ReviewError('provider_role_mismatch')
        if route.mode == 'disabled':
            raise ReviewError('provider_disabled')
        if route.mode == 'live':
            if route.provider == 'gemini' and route.tier != 'free':
                raise ReviewError('explicit_free_tier_route_required')
            raise ReviewError('live_transport_not_enabled_in_v1')
        if route.tier != 'fixture' or type(self.transport) is not FixtureTransport:
            raise ReviewError('explicit_offline_fixture_transport_required')
        request = freeze({**request, 'route': asdict(route), 'fresh_context': True})
        request_hash = digest(request)
        # Conservative byte ceiling also bounds input without needing a vendor tokenizer.
        if len(canonical(request).encode()) > min(route.max_input_bytes, route.max_input_tokens):
            raise ReviewError('input_budget_exceeded')
        cached = storage.cached_call(request_hash)
        if cached is not None:
            return cached
        reservation = storage.reserve_call(request_hash, asdict(route), request)
        try:
            response = self.transport({**request, 'request_hash': request_hash,
                                       'max_output_tokens': route.max_output_tokens})
            # Preserve the exact fixture/provider receipt before parsing normalized output.
            storage.finish_call(reservation, response)
        except Exception:
            storage.fail_call(reservation)
            raise ReviewError('provider_unavailable') from None
        return storage.cached_call(request_hash, require=True)


class OpenAIPrimaryAdapter(_Adapter):
    provider = 'openai'
    purposes = frozenset({'primary'})


class GeminiReviewerAdapter(_Adapter):
    provider = 'gemini'
    purposes = frozenset({'review'})


class GeminiSpecialistAdapter(_Adapter):
    """Future video/document route uses the same caps; no legacy paid fallback."""
    provider = 'gemini'
    purposes = frozenset({'video', 'document'})
