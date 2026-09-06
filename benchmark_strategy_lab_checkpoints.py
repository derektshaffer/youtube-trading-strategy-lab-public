"""Replay saved checkpoint I/O only. No market, queue, network or Git operations.

The deployed runner and original artifacts must be supplied explicitly. Remote
reads/writes are in-memory transport stubs with real JSON parsing/serialization.
Network and clone/push latency are deliberately NOT simulated or predicted.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import strategy_lab_jobs as current_jobs
import youtube_strategy_engine as engine
from strategy_lab_telemetry import instrument_checkpoint_store


class LegacyStore(engine.StrategyStore):
    def _write_local(self, value, *, make_backup=True):
        # Exact pre-repair streaming writer. Its json.dump timing includes
        # buffered writes; the remaining fsync/backup/replace is separate.
        metric = getattr(self, "_checkpoint_telemetry", None)
        descriptor, name = tempfile.mkstemp(prefix="strategy_", suffix=".json", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                start = time.perf_counter()
                json.dump(value, handle, separators=(",", ":"), default=str, allow_nan=False)
                serialization = time.perf_counter() - start
                start = time.perf_counter()
                handle.flush(); os.fsync(handle.fileno())
            if make_backup:
                self._make_automatic_backup()
            os.replace(name, self.path)
            writing = time.perf_counter() - start
            if metric and metric.active:
                metric.parts["serialization_seconds"] = metric.parts.get("serialization_seconds", 0) + serialization
                metric.parts["local_write_seconds"] = metric.parts.get("local_write_seconds", 0) + writing
                metric.parts["checkpoint_bytes"] = self.path.stat().st_size
        finally:
            if os.path.exists(name):
                os.unlink(name)


def run_benchmark(artifact, baseline_source, *, ticks=12):
    baseline = ModuleType("checkpoint_deployed_baseline")
    exec(compile(baseline_source, "deployed-strategy-lab-jobs.py", "exec"), baseline.__dict__)
    original = json.loads(artifact)
    source = next(row for row in original["validation_runs"] if row.get("ticker") == "SPY")
    original_others = [row for row in original["validation_runs"] if row is not source]
    output = {}
    for label, runner, store_class in (("before", baseline, LegacyStore),
                                       ("after", current_jobs, engine.StrategyStore)):
        documents = {}
        writes = []
        events = []
        class ReplayBackup(engine.GitHubCloudBackup):
            def __init__(self, repository="fixture/private", token="fixture-token", *, branch="main", path="checkpoint.json"):
                super().__init__(repository, token, branch=branch, path=path)
            def read_library(self, *, include_raw=False):
                raw = documents.get(self.path)
                if raw is None:
                    return None
                value = {"library": json.loads(raw), "sha": hashlib.sha1(raw).hexdigest()}
                if include_raw:
                    value["_raw_bytes"] = raw
                return value
            def _save_large_library(self, serialized, *, current_sha=""):
                documents[self.path] = serialized
                writes.append({"path": self.path, "bytes": len(serialized), "would_use_git": True})
                return hashlib.sha1(serialized).hexdigest()
            def _request(self, url, *, method="GET", payload=None, **kwargs):
                if method != "PUT":
                    raise AssertionError("Benchmark attempted a non-stubbed remote operation")
                import base64
                raw = base64.b64decode(payload["content"])
                documents[self.path] = raw
                writes.append({"path": self.path, "bytes": len(raw), "would_use_git": False})
                return {"content": {"sha": hashlib.sha1(raw).hexdigest()}}

        data = deepcopy(original)
        record = next(row for row in data["validation_runs"] if row.get("ticker") == "SPY")
        run_id = "offline-checkpoint-benchmark"
        record.update(id=run_id, status="running", attempt=0, progress=.01, stage="preparing")
        record.pop("execution_error", None)
        job = deepcopy(record["job"])
        job["run_id"] = run_id
        documents["checkpoint.json"] = json.dumps(data, separators=(",", ":"), default=str).encode()
        clock = [100.0]
        with tempfile.TemporaryDirectory(prefix="checkpoint-io-benchmark-") as directory:
            full = store_class(directory, cloud_backup=ReplayBackup())
            instrument_checkpoint_store(full, run_id).emit = events.append
            def execute(job, **callbacks):
                for index in range(ticks):
                    clock[0] += 11
                    callbacks["progress"](.38 + index * .005, "optimization", "Offline recorded-progress replay")
                callbacks["optimizer_checkpoint"]({"fingerprint": "offline-fixture", "symbol": "SPY",
                    "completed_strategy_ids": [job["candidates"][0]["id"]],
                    "rankings": [{"source_strategy_id": job["candidates"][0]["id"]}]})
                raise RuntimeError("Controlled benchmark stop, not a market validation")
            started = time.perf_counter()
            with patch.object(engine, "GitHubCloudBackup", ReplayBackup), patch.object(
                    runner, "time", SimpleNamespace(monotonic=lambda: clock[0])):
                outcome = runner.execute_strategy_lab_job_once(run_id=run_id, job=job, checkpoint_store=full,
                    market=object(), main_store=object(), executor=execute)
            elapsed = time.perf_counter() - started
            saved = json.loads(documents["checkpoint.json"])
            assert outcome["status"] == "failed"
            final = next(row for row in saved["validation_runs"] if row["id"] == run_id)
            assert final["status"] == "failed" and "result" not in final
            assert final["job"] == job and final["optimizer_state"]["completed_strategy_ids"] == [job["candidates"][0]["id"]]
            assert [row for row in saved["validation_runs"] if row["id"] != run_id] == original_others
            full_writes = [row for row in writes if row["path"] == "checkpoint.json"]
            heartbeats = [row for row in writes if row["path"] != "checkpoint.json"]
            output[label] = {"runtime_seconds": elapsed, "durable_writes": len(full_writes),
                "heartbeat_writes": len(heartbeats), "would_use_git_writes": sum(row["would_use_git"] for row in writes),
                "serialized_upload_bytes": sum(row["bytes"] for row in writes),
                "durable_bytes": len(documents["checkpoint.json"]),
                "max_heartbeat_bytes": max([row["bytes"] for row in heartbeats] or [0]),
                "timing_totals": events[-1]["cumulative"], "events": events,
                "retained_history_equal": True, "identity_and_resume_equal": True}
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = (args.artifact_dir / "checkpoint-original.json").read_bytes()
    first = json.loads((args.artifact_dir / "checkpoint-first.json").read_bytes())
    original = json.loads(raw)
    result = run_benchmark(raw, (args.artifact_dir / "deployed-strategy-lab-jobs.py").read_text())
    result["original_bytes"] = len(raw)
    result["history_unchanged_during_real_run"] = original["validation_runs"][1:] == first["validation_runs"][1:]
    result["limitations"] = ["Offline transport only; no clone, push, remote writes or market validation.",
        "Before serialization includes buffered json.dump writes; after serialization is json.dumps plus UTF-8 encoding.",
        "Non-checkpoint wall time is not a pure backtest CPU measurement."]
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: ({a:b for a,b in v.items() if a != "events"} if isinstance(v, dict) else v)
                      for k,v in result.items()}, indent=2))


if __name__ == "__main__":
    main()
