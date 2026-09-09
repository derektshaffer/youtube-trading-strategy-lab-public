"""Bounded, capture-only SIP service. The transport has no brokerage endpoints."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import random
import threading
import time
import uuid

from .events import ContractError, SYMBOL, canonical_json
from .ledger import Ledger, Recorder, strict_json

CHANNELS = ("trades", "quotes", "bars", "updatedBars", "statuses", "lulds")
AUTO_CHANNELS = ("corrections", "cancelErrors")
ENDPOINT = "wss://stream.data.alpaca.markets/v2/sip"
CANONICAL_ROOT = Path("/Users/Derek_1/Documents/Codex/2026-09-03/referenced-chatgpt-conversation-this-is-an/work/trading-lab-dev")


@dataclass(frozen=True)
class Config:
    symbols: tuple[str, ...]
    feed: str = "sip"
    heartbeat_seconds: float = 5
    silence_seconds: float = 30
    max_reconnects: int = 5
    min_free_bytes: int = 1_073_741_824
    max_frame_bytes: int = 4_194_304

    def __post_init__(self):
        if self.feed != "sip":
            raise ContractError("sip_required_no_fallback")
        if not self.symbols or len(self.symbols) > 100 or len(set(self.symbols)) != len(self.symbols):
            raise ContractError("explicit_unique_symbol_list_required_max_100")
        if any(not isinstance(s, str) or not SYMBOL.fullmatch(s) for s in self.symbols):
            raise ContractError("invalid_symbol")
        for value in (self.heartbeat_seconds, self.silence_seconds):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ContractError("invalid_timeout")
        if self.silence_seconds < self.heartbeat_seconds:
            raise ContractError("invalid_timeout_order")
        for value in (self.max_reconnects, self.min_free_bytes, self.max_frame_bytes):
            if type(value) is not int or value < 0:
                raise ContractError("invalid_limit")
        if self.max_frame_bytes < 1024 or self.max_reconnects > 20:
            raise ContractError("invalid_limit")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict):
            raise ContractError("invalid_config")
        allowed = set(cls.__dataclass_fields__)
        if set(data) - allowed:
            raise ContractError("unknown_config_fields")
        if not isinstance(data.get("symbols"), list):
            raise ContractError("explicit_symbol_list_required")
        data["symbols"] = tuple(data["symbols"])
        return cls(**data)

    def subscription(self):
        return {"action": "subscribe", **{name: list(self.symbols) for name in CHANNELS}}

    def summary(self):
        return {"feed": self.feed, "symbols": list(self.symbols), "channels": list(CHANNELS),
                "heartbeat_seconds": self.heartbeat_seconds, "silence_seconds": self.silence_seconds,
                "max_reconnects": self.max_reconnects, "min_free_bytes": self.min_free_bytes,
                "max_frame_bytes": self.max_frame_bytes, "execution_authority": "none"}


class CaptureFailure(RuntimeError):
    pass


class StopCapture(Exception):
    pass


def credentials_from_environment() -> tuple[str, str]:
    # Read one complete pair; never mix keys from two independently configured accounts.
    for key_name, secret_name in [("ALPACA_API_KEY", "ALPACA_SECRET_KEY"),
                                  ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")]:
        key, secret = os.environ.get(key_name, "").strip(), os.environ.get(secret_name, "").strip()
        if key and secret:
            return key, secret
        if key or secret:
            raise CaptureFailure("incomplete_environment_credentials")
    raise CaptureFailure("market_data_credentials_not_configured")


def connect_live(config: Config):
    from websockets.sync.client import connect
    # Never allow websocket debug tracing to print outbound authentication.
    logger = logging.Logger("systematic_trader.transport", level=logging.CRITICAL + 1)
    logger.addHandler(logging.NullHandler())
    return connect(ENDPOINT, open_timeout=10, close_timeout=5,
                   ping_interval=10, ping_timeout=10, compression=None,
                   max_size=config.max_frame_bytes, max_queue=16, logger=logger)


def source_manifest():
    root = Path(__file__).resolve().parent
    names = sorted([*root.glob("*.py"), *root.glob("*.schema.json")])
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in names}
    return {"source_files": hashes, "source_hash": hashlib.sha256(canonical_json(hashes).encode()).hexdigest()}


class CaptureService:
    def __init__(self, recorder: Recorder, config: Config, credentials: tuple[str, str], *,
                 connect=connect_live, stop=None, monotonic=time.monotonic, duration_seconds=0):
        if recorder.origin != "live" or recorder.feed != config.feed:
            raise CaptureFailure("live_recorder_required")
        if len(credentials) != 2 or not all(isinstance(c, str) and c for c in credentials):
            raise CaptureFailure("invalid_credentials")
        if not math.isfinite(duration_seconds) or duration_seconds < 0:
            raise CaptureFailure("invalid_duration")
        self.recorder, self.config, self.credentials = recorder, config, credentials
        self.connect, self.stop = connect, stop or threading.Event()
        self.monotonic, self.duration_seconds = monotonic, duration_seconds
        self.started = self.monotonic()

    def halted(self):
        return self.stop.is_set() or (self.duration_seconds > 0 and self.monotonic() - self.started >= self.duration_seconds)

    def receive(self, socket, timeout):
        if self.halted():
            raise StopCapture
        raw = socket.recv(timeout=timeout)
        if not isinstance(raw, (str, bytes)):
            raise CaptureFailure("invalid_transport_frame")
        raw = raw.encode() if isinstance(raw, str) else raw
        if len(raw) > self.config.max_frame_bytes:
            self.recorder.internal("recorder.gap", {"reason": "oversized_frame", "unresolved": True})
            raise CaptureFailure("oversized_frame")
        # Market payloads are exact; unexpected reflected credentials are redacted
        # before persistence, with explicit evidence that the bytes were changed.
        altered = False
        for credential in self.credentials:
            encoded = credential.encode()
            if encoded in raw:
                raw = raw.replace(encoded, b"[REDACTED]")
                altered = True
        self.recorder.ingest(raw)
        if altered:
            self.recorder.internal("recorder.gap", {"reason": "credential_reflection_redacted", "unresolved": True})
            raise CaptureFailure("credential_reflection_redacted")
        try:
            messages = strict_json(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise CaptureFailure("invalid_provider_json") from None
        if not isinstance(messages, list) or not messages or any(not isinstance(x, dict) for x in messages):
            raise CaptureFailure("invalid_provider_batch")
        for message in messages:
            if message.get("T") == "error":
                code = message.get("code")
                safe_code = str(code) if type(code) is int and 400 <= code <= 599 else "unknown"
                # All provider rejections stop; no unauthorized feed fallback or retry storm.
                raise CaptureFailure("provider_rejected_" + safe_code)
        return messages

    @staticmethod
    def require_success(messages, state):
        if len(messages) != 1 or messages[0].get("T") != "success" or messages[0].get("msg") != state:
            raise CaptureFailure("unexpected_handshake")

    def require_subscription(self, messages):
        if len(messages) != 1 or messages[0].get("T") != "subscription":
            raise CaptureFailure("subscription_not_acknowledged")
        message = messages[0]
        for channel in CHANNELS + AUTO_CHANNELS:
            actual = message.get(channel)
            if not isinstance(actual, list) or set(actual) != set(self.config.symbols):
                raise CaptureFailure("incomplete_subscription")

    def session(self, socket):
        self.require_success(self.receive(socket, 5), "connected")
        socket.send(canonical_json({"action": "auth", "key": self.credentials[0], "secret": self.credentials[1]}))
        self.require_success(self.receive(socket, 5), "authenticated")
        socket.send(canonical_json(self.config.subscription()))
        self.require_subscription(self.receive(socket, 5))
        self.recorder.internal("recorder.lifecycle", {"state": "subscribed", **self.config.summary()})
        last_market = last_heartbeat = self.monotonic()
        stale = False
        while not self.halted():
            messages = []
            try:
                messages = self.receive(socket, min(1.0, self.config.heartbeat_seconds))
            except TimeoutError:
                pass
            now = self.monotonic()
            if any(m.get("T") not in {"success", "subscription", "error"} for m in messages):
                last_market = now
            if any(m.get("T") == "subscription" for m in messages):
                self.require_subscription(messages)
            age = now - last_market
            if age >= self.config.silence_seconds and not stale:
                self.recorder.internal("recorder.gap", {"reason": "market_stream_silent", "unresolved": True})
                stale = True
            if now - last_heartbeat >= self.config.heartbeat_seconds:
                self.recorder.internal("recorder.heartbeat", {
                    "state": "capture_degraded" if stale else "receiving_unqualified",
                    "market_silence_seconds": age, "last_committed_seq": self.recorder.ledger.watermark(),
                    "execution_authority": "none", "gaps_auto_cleared": False})
                last_heartbeat = now

    def run(self):
        self.recorder.recover()
        self.recorder.internal("recorder.lifecycle", {
            "state": "starting", "config_hash": hashlib.sha256(canonical_json(self.config.summary()).encode()).hexdigest(),
            **self.config.summary(), **source_manifest(), "receipt_boundary": "application_websocket_dequeue",
            "reference_coverage": "unverified", "calendar_coverage": "unverified"})
        # No sequence/replay cursor is supplied by this vendor stream. Startup
        # and every reconnect have an explicit coverage gap, never synthetic continuity.
        self.recorder.internal("recorder.gap", {"reason": "startup_coverage_unknown", "unresolved": True})
        retries = 0
        try:
            while not self.halted():
                self.recorder.connection_id = str(uuid.uuid4())
                try:
                    with self.connect(self.config) as socket:
                        self.session(socket)
                    break
                except StopCapture:
                    break
                except CaptureFailure:
                    raise
                except Exception as exc:
                    # Storage/contract failures must never be retried as network failures.
                    from websockets.exceptions import ConnectionClosed, InvalidHandshake
                    if not isinstance(exc, (ConnectionClosed, InvalidHandshake, OSError, TimeoutError)):
                        raise
                    self.recorder.internal("recorder.gap", {"reason": "transport_disconnect", "unresolved": True,
                                                            "attempt": retries})
                    if retries >= self.config.max_reconnects:
                        raise CaptureFailure("reconnect_budget_exhausted") from None
                    retries += 1
                    delay = min(30, 2 ** retries) + random.uniform(0, 1)
                    self.stop.wait(delay)
            self.recorder.internal("recorder.lifecycle", {"state": "stopped", "reason": "requested_or_duration"})
        except Exception as exc:
            reason = str(exc) if isinstance(exc, CaptureFailure) else "capture_integrity_or_storage_failure"
            self.recorder.internal("recorder.lifecycle", {"state": "failed", "reason": reason})
            raise CaptureFailure(reason) from None


def assert_canonical_source():
    if Path(__file__).resolve().parents[1] != CANONICAL_ROOT:
        raise CaptureFailure("noncanonical_source_refused")
