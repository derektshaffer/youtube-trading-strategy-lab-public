"""Supervise read-only discovery calls without letting a stalled provider own the queue."""

from __future__ import annotations

import queue
import threading
import time

from .engine_adapter import JobCancelled


DISCOVERY_TIMEOUTS = {"market.discovery": 900.0, "library.strategy_lab_options": 300.0}


def run_bounded(handler, payload, progress, cancelled, heartbeat, *, timeout):
    """Only the supervisor may publish progress/results to durable storage.

    A provider read already in flight cannot be interrupted safely in Python.
    On cancellation/timeout its daemon is fenced from publishing anything, and
    exits at its next callback/return. The local worker can serve the next job.
    These handlers only read providers and build results; no trading is done.
    """
    events = queue.Queue()
    stopped = threading.Event()

    def report(*args):
        if stopped.is_set():
            raise JobCancelled("Discovery stopped")
        events.put(("progress", args))

    def execute():
        try:
            result = handler(payload, report, stopped.is_set)
            if not stopped.is_set():
                events.put(("result", result))
        except BaseException as exc:
            if not stopped.is_set():
                events.put(("error", exc))

    threading.Thread(target=execute, name="discovery-provider", daemon=True).start()
    started = last_heartbeat = time.monotonic()
    try:
        while True:
            if cancelled():
                raise JobCancelled("Discovery cancellation requested")
            now = time.monotonic()
            if now - started >= timeout:
                raise TimeoutError(f"Discover Stocks operation exceeded {timeout:g} seconds; run it again.")
            if now - last_heartbeat >= 5:
                heartbeat()
                last_heartbeat = now
            try:
                kind, value = events.get(timeout=min(0.1, max(0.001, timeout - (now - started))))
            except queue.Empty:
                continue
            if kind == "progress":
                progress(*value)
            elif kind == "error":
                if isinstance(value, Exception):
                    raise value
                raise RuntimeError(f"Discovery worker stopped: {type(value).__name__}: {value}")
            else:
                return value
    finally:
        stopped.set()
