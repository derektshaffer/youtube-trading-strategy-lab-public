"""Bounded streaming replay with strict information availability and ordering."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from ..events import ContractError, canonical_json, digest
from .contracts import Tick


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024*1024), b""):
            h.update(part)
    return h.hexdigest()


def read_ticks(path, *, expected_sha256, max_events=2_000_000):
    # Verify exact bytes before exposing the first event. Hash again while reading
    # so a concurrent change cannot produce a successful result under an old hash.
    if file_hash(path) != expected_sha256:
        raise ContractError("short_horizon_dataset_hash_mismatch")
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for index, line in enumerate(stream, 1):
            if index > max_events or len(line) > 65536:
                raise ContractError("short_horizon_dataset_bound_exceeded")
            h.update(line)
            yield Tick(**json.loads(line))
    if h.hexdigest() != expected_sha256:
        raise ContractError("short_horizon_dataset_changed_during_replay")


def ordered(events, dataset, *, max_events=2_000_000):
    previous_time = previous_sequence = None
    seen = {}
    source_states = {}
    for index, e in enumerate(events):
        if index >= max_events:
            raise ContractError("short_horizon_dataset_bound_exceeded")
        now = dataset.available(e)
        if e.symbol not in dataset.symbols or not dataset.start_ns <= e.exchange_ns <= now < dataset.end_ns:
            raise ContractError("short_horizon_event_outside_manifest")
        if not any(s.pre_ns <= e.exchange_ns <= now < s.end_ns for s in dataset.sessions):
            raise ContractError("short_horizon_event_outside_calendar")
        fingerprint = digest(asdict(e))
        if e.event_id in seen:
            if seen[e.event_id] != fingerprint:
                raise ContractError("short_horizon_conflicting_duplicate")
            continue
        seen[e.event_id] = fingerprint
        if previous_time is not None:
            if now < previous_time:
                raise ContractError("short_horizon_availability_order_regression")
            if now == previous_time and (dataset.sequence_scope != "global" or e.sequence is None or
                                         previous_sequence is None or e.sequence <= previous_sequence):
                raise ContractError("short_horizon_ambiguous_equal_time_order")
        key = (e.symbol, e.kind)
        prev = source_states.get(key)
        if e.kind in {"quote", "status"} and prev and e.exchange_ns == prev.exchange_ns:
            if dataset.sequence_scope != "global" or e.sequence is None or prev.sequence is None or e.sequence <= prev.sequence:
                raise ContractError("short_horizon_ambiguous_source_state")
        if prev is None or e.exchange_ns >= prev.exchange_ns:
            source_states[key] = e
        elif e.kind == "status":
            raise ContractError("short_horizon_late_status_uncertain")
        previous_time, previous_sequence = now, e.sequence
        yield now, e


def replay(events, simulator, observer):
    count = 0
    for now, event in ordered(events, simulator.dataset):
        simulator.advance(now)  # Timers at T see only market information before T.
        simulator.observe(event, now)
        observer(event, now)
        count += 1
    simulator.advance(simulator.dataset.end_ns)
    return count
