"""Launch a packaged sidecar and record an authenticated end-to-end smoke."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import secrets
import socket
import subprocess
import time
from typing import Any
from urllib.request import Request, urlopen


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def request_json(
    base_url: str,
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        base_url + path,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=5) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError("Local service returned a non-object response")
    return decoded


def file_description(path: Path) -> str:
    try:
        return subprocess.run(
            ["file", "-b", str(path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unavailable"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--data-dir", default="")
    args = parser.parse_args(argv)

    binary = Path(args.binary).expanduser().resolve()
    if not binary.is_file():
        raise SystemExit(f"Missing sidecar binary: {binary}")
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    data_dir = (
        Path(args.data_dir).expanduser().resolve()
        if str(args.data_dir).strip()
        else output.parent / "sidecar-data"
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = output.with_suffix(".sidecar.log")
    port = available_port()
    base_url = f"http://127.0.0.1:{port}"
    token = secrets.token_urlsafe(48)
    environment = os.environ.copy()
    environment["TRADING_INTELLIGENCE_LOCAL_TOKEN"] = token
    environment["PYTHONUNBUFFERED"] = "1"

    started = time.perf_counter()
    process: subprocess.Popen[bytes] | None = None
    job_started = 0.0
    metrics: dict[str, Any] = {
        "status": "failed",
        "platform": platform.platform(),
        "machine": platform.machine(),
        "binary": str(binary),
        "binary_bytes": binary.stat().st_size,
        "binary_description": file_description(binary),
        "log_path": str(log_path),
    }
    try:
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [
                    str(binary),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--data-dir",
                    str(data_dir),
                ],
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            deadline = time.monotonic() + 45.0
            health: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Sidecar exited before health check with code {process.returncode}"
                    )
                try:
                    health = request_json(base_url, token, "GET", "/health")
                    break
                except OSError:
                    time.sleep(0.1)
            if not health or health.get("status") != "ok":
                raise RuntimeError("Sidecar did not become healthy")

            service_ready_seconds = time.perf_counter() - started
            request = {
                "job_type": "system.health",
                "payload": {"checks": ["runtime", "sqlite", "packaged-sidecar"]},
                "requested_target": "auto",
                "idempotency_key": f"sidecar-smoke-{os.getpid()}-{time.time_ns()}",
            }
            route = request_json(base_url, token, "POST", "/v1/route", request)
            job_started = time.perf_counter()
            submitted = request_json(base_url, token, "POST", "/v1/jobs", request)
            job_id = str((submitted.get("job") or {}).get("id") or "")
            if not job_id:
                raise RuntimeError("Sidecar job submission returned no job id")

            terminal: dict[str, Any] | None = None
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                current = request_json(base_url, token, "GET", f"/v1/jobs/{job_id}")
                if bool(current.get("terminal")):
                    terminal = current
                    break
                time.sleep(0.1)
            if not terminal or terminal.get("status") != "complete":
                raise RuntimeError(
                    "Packaged sidecar health job did not complete successfully"
                )
            events = request_json(
                base_url,
                token,
                "GET",
                f"/v1/jobs/{job_id}/events",
            ).get("events") or []
            metrics.update(
                {
                    "status": "passed",
                    "route": route,
                    "service_ready_seconds": round(service_ready_seconds, 4),
                    "job_seconds": round(time.perf_counter() - job_started, 4),
                    "total_seconds": round(time.perf_counter() - started, 4),
                    "event_count": len(events),
                    "result": terminal.get("result") or {},
                }
            )
    except BaseException as exc:
        metrics["error_type"] = type(exc).__name__
        metrics["error"] = " ".join(str(exc).split())[:1_000]
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        output.write_text(
            json.dumps(metrics, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
