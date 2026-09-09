"""Tradier market-data-only transport. No account, position, or order client."""
from dataclasses import dataclass
import json
import logging
import os
import threading
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .events import canonical_json
from .ledger import Recorder
from .service import Config, CaptureFailure, source_manifest

SESSION_ENDPOINT = "https://api.tradier.com/v1/markets/events/session"
STREAM_ENDPOINT = "wss://ws.tradier.com/v1/markets/events"


@dataclass(frozen=True)
class TradierConfig(Config):
    feed: str = "tradier_consolidated"

    def __post_init__(self):
        if self.feed != "tradier_consolidated":
            raise CaptureFailure("tradier_feed_mismatch")
        Config(self.symbols, heartbeat_seconds=self.heartbeat_seconds, silence_seconds=self.silence_seconds,
               max_reconnects=self.max_reconnects, min_free_bytes=self.min_free_bytes, max_frame_bytes=self.max_frame_bytes)

    def subscription(self, session_id):
        return {"symbols": list(self.symbols), "filter": ["timesale", "quote"], "sessionid": session_id,
                "linebreak": True, "validOnly": False, "advancedDetails": True}

    def summary(self):
        result = super().summary()
        result.update(channels=["timesale", "quote"], provider="tradier", validOnly=False, advancedDetails=True)
        return result


def credentials(source="environment"):
    if source == "environment":
        values = [os.environ.get(k, "").strip() for k in ("TRADIER_ACCESS_TOKEN", "TRADIER_TOKEN")]
        nonempty = set(v for v in values if v)
        if len(nonempty) > 1:
            raise CaptureFailure("ambiguous_tradier_environment_credentials")
        if nonempty:
            return nonempty.pop()
        raise CaptureFailure("tradier_credentials_not_configured")
    services = {"lab-keychain": "Trading Intelligence Lab", "lab-dev-keychain": "Trading Intelligence Lab Dev"}
    if source not in services:
        raise CaptureFailure("unknown_credential_source")
    from hybrid_runtime.keychain import MacOSKeychain, KeychainError
    store = MacOSKeychain()
    store.service, store._fallback_service = services[source], None
    try:
        token = store.get_secret("tradier-access-token").strip()
    except KeychainError:
        raise CaptureFailure("tradier_credentials_not_configured") from None
    if not token:
        raise CaptureFailure("tradier_credentials_not_configured")
    return token


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise CaptureFailure("market_data_redirect_refused")


def create_session(token):
    # This fixed POST creates a market stream token only. A production API token
    # is never passed to a generic brokerage SDK or arbitrary URL.
    request = Request(SESSION_ENDPOINT, data=b"", method="POST", headers={
        "Authorization": "Bearer " + token, "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=10) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise CaptureFailure("session_response_too_large")
        session_id = json.loads(raw).get("stream", {}).get("sessionid")
        if not isinstance(session_id, str) or not session_id or len(session_id) > 512:
            raise CaptureFailure("invalid_market_session_response")
        return session_id
    except HTTPError as exc:
        raise CaptureFailure(f"tradier_market_session_rejected_{exc.code}") from None
    except CaptureFailure:
        raise
    except Exception:
        raise CaptureFailure("tradier_market_session_unavailable") from None


def connect_market(config):
    from websockets.sync.client import connect
    logger = logging.Logger("systematic_trader.tradier", level=logging.CRITICAL + 1)
    logger.addHandler(logging.NullHandler())
    return connect(STREAM_ENDPOINT, open_timeout=10, close_timeout=5, ping_interval=10, ping_timeout=10,
                   max_size=config.max_frame_bytes, max_queue=16, compression=None, logger=logger)


class TradierCapture:
    def __init__(self, recorder: Recorder, config: TradierConfig, token: str, *,
                 session_factory=create_session, connect=connect_market, stop=None,
                 duration_seconds=60, monotonic=time.monotonic):
        if recorder.provider != "tradier" or recorder.feed != config.feed or recorder.origin != "live":
            raise CaptureFailure("tradier_live_recorder_required")
        if not token:
            raise CaptureFailure("tradier_credentials_not_configured")
        import math
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise CaptureFailure("bounded_tradier_capture_required")
        self.recorder, self.config, self.token = recorder, config, token
        self.session_factory, self.connect, self.stop = session_factory, connect, stop or threading.Event()
        self.duration, self.monotonic = duration_seconds, monotonic

    def run(self):
        self.recorder.recover()
        self.recorder.internal("recorder.lifecycle", {"state": "starting", **self.config.summary(), **source_manifest()})
        self.recorder.internal("recorder.gap", {"reason": "startup_coverage_unknown", "unresolved": True})
        start, reconnects, market_count = self.monotonic(), 0, 0
        try:
            while not self.stop.is_set() and self.monotonic() - start < self.duration:
                self.recorder.connection_id = str(uuid.uuid4())
                # Session IDs are short-lived secrets, used immediately and never persisted.
                session_id = self.session_factory(self.token)
                try:
                    with self.connect(self.config) as socket:
                        socket.send(canonical_json(self.config.subscription(session_id)))
                        self.recorder.internal("recorder.lifecycle", {"state": "subscription_sent_unconfirmed", **self.config.summary()})
                        last_market = last_heartbeat = self.monotonic()
                        stale = False
                        while not self.stop.is_set() and self.monotonic() - start < self.duration:
                            try:
                                raw = socket.recv(timeout=1)
                            except TimeoutError:
                                raw = None
                            if raw is not None:
                                raw = raw.encode() if isinstance(raw, str) else raw
                                if not isinstance(raw, bytes) or len(raw) > self.config.max_frame_bytes:
                                    raise CaptureFailure("invalid_tradier_frame")
                                changed = False
                                for secret in (self.token, session_id):
                                    if secret.encode() in raw:
                                        raw = raw.replace(secret.encode(), b"[REDACTED]"); changed = True
                                raw_id = self.recorder.ingest(raw)
                                if changed:
                                    self.recorder.internal("recorder.gap", {"reason": "credential_reflection_redacted", "unresolved": True})
                                    raise CaptureFailure("credential_reflection_redacted")
                                batch = [json.loads(r[0]) for r in self.recorder.ledger.connection.execute(
                                    "SELECT event_json FROM events WHERE raw_id=? ORDER BY seq", (raw_id,))]
                                if any(e["event_type"] in {"provider.control", "recorder.reject"} for e in batch):
                                    raise CaptureFailure("tradier_invalid_or_rejected_stream")
                                market_count += sum(e["event_type"].startswith("market.") for e in batch)
                                last_market = self.monotonic()
                            now = self.monotonic()
                            if now - last_market >= self.config.silence_seconds and not stale:
                                self.recorder.internal("recorder.gap", {"reason": "market_stream_silent", "unresolved": True}); stale = True
                            if now - last_heartbeat >= self.config.heartbeat_seconds:
                                self.recorder.internal("recorder.heartbeat", {"state": "capture_degraded" if stale else "receiving_unqualified",
                                    "market_events": market_count, "market_silence_seconds": now - last_market, "execution_authority": "none"})
                                last_heartbeat = now
                        break
                except CaptureFailure:
                    raise
                except Exception as exc:
                    from websockets.exceptions import ConnectionClosed
                    if not isinstance(exc, (ConnectionClosed, ConnectionError, TimeoutError)):
                        raise
                    self.recorder.internal("recorder.gap", {"reason": "transport_disconnect", "unresolved": True,
                        "sequence_resume_available": False, "reconnect": reconnects})
                    if reconnects >= self.config.max_reconnects:
                        raise CaptureFailure("reconnect_budget_exhausted") from None
                    reconnects += 1
                    self.stop.wait(min(30, 2**reconnects))
            self.recorder.internal("recorder.lifecycle", {"state": "stopped", "market_events": market_count,
                "certified": False, "execution_authority": "none"})
            return market_count
        except Exception as exc:
            reason = str(exc) if isinstance(exc, CaptureFailure) else "tradier_capture_integrity_failure"
            self.recorder.internal("recorder.lifecycle", {"state": "failed", "reason": reason})
            raise CaptureFailure(reason) from None
