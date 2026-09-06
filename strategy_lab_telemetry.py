"""Opt-in checkpoint I/O measurements, without job data or credentials in logs."""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import json
import time


class CheckpointTelemetry:
    def __init__(self, run_id, emit=None):
        self.run_id = str(run_id)
        self.emit = emit or (lambda row: print(json.dumps(row, separators=(",", ":")), flush=True))
        self.last_wall = time.perf_counter()
        self.last_cpu = time.process_time()
        self.active = False
        self.parts = {}
        self.totals = {}
        self.count = 0

    @contextmanager
    def measure(self, name):
        started = time.perf_counter()
        try:
            yield
        finally:
            if self.active:
                self.parts[name] = self.parts.get(name, 0.0) + time.perf_counter() - started

    def serialize(self, data):
        with self.measure("serialization_seconds"):
            raw = json.dumps(data, separators=(",", ":"), default=str, allow_nan=False).encode("utf-8")
        if self.active:
            self.parts["checkpoint_bytes"] = max(self.parts.get("checkpoint_bytes", 0), len(raw))
        return raw

    @contextmanager
    def operation(self, kind, stage=""):
        if self.active:
            yield
            return
        started, cpu = time.perf_counter(), time.process_time()
        gap = max(0.0, started - self.last_wall)
        cpu_gap = max(0.0, cpu - self.last_cpu)
        self.parts = {}
        self.active = True
        succeeded = False
        try:
            yield
            succeeded = True
        finally:
            ended = time.perf_counter()
            self.active = False
            elapsed = ended - started
            self.count += 1
            values = {
                "persistence_seconds": elapsed,
                "serialization_seconds": self.parts.get("serialization_seconds", 0.0),
                "local_write_seconds": self.parts.get("local_write_seconds", 0.0),
                "remote_seconds": self.parts.get("remote_seconds", 0.0),
                # This includes provider waits and other work, not just backtesting.
                "non_checkpoint_wall_seconds": gap,
                "non_checkpoint_cpu_seconds": cpu_gap,
            }
            values["other_persistence_seconds"] = max(0.0, elapsed - sum(
                values[key] for key in ("serialization_seconds", "local_write_seconds", "remote_seconds")))
            for key, value in values.items():
                self.totals[key] = self.totals.get(key, 0.0) + value
            row = {"event": "strategy_lab_checkpoint_io", "run_id": self.run_id,
                   "kind": kind, "stage": str(stage), "sequence": self.count,
                   "succeeded": succeeded, "checkpoint_bytes": self.parts.get("checkpoint_bytes", 0),
                   **{key: round(value, 6) for key, value in values.items()},
                   "cumulative": {key: round(value, 6) for key, value in self.totals.items()}}
            # Timing/logging must never change checkpoint success or failure.
            try:
                self.emit(row)
            except Exception:
                pass
            self.last_wall, self.last_cpu = time.perf_counter(), time.process_time()


def instrument_checkpoint_store(store, run_id):
    metric = getattr(store, "_checkpoint_telemetry", None)
    if metric is None or metric.run_id != str(run_id):
        metric = CheckpointTelemetry(run_id)
        store._checkpoint_telemetry = metric
    cloud = getattr(store, "cloud_backup", None)
    if cloud is not None:
        cloud._checkpoint_telemetry = metric
    return metric


def checkpoint_operation(kind):
    def decorate(function):
        @wraps(function)
        def measured(store, *args, **kwargs):
            metric = instrument_checkpoint_store(store, kwargs.get("run_id", ""))
            with metric.operation(kind, kwargs.get("stage", "")):
                return function(store, *args, **kwargs)
        return measured
    return decorate


def timed_checkpoint_io(name):
    """Non-checkpoint stores take the original path without enabling telemetry."""
    def decorate(function):
        @wraps(function)
        def measured(owner, *args, **kwargs):
            metric = getattr(owner, "_checkpoint_telemetry", None)
            if metric is None:
                return function(owner, *args, **kwargs)
            with metric.measure(name):
                return function(owner, *args, **kwargs)
        return measured
    return decorate
