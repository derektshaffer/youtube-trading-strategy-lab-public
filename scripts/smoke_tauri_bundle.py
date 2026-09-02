"""Launch the packaged Tauri app and verify its embedded Python sidecar."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import time
from typing import Any
from urllib.request import Request, urlopen


DEFAULT_DATA_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / "com.derektshaffer.trading-intelligence-spike"
)


def directory_size(path: Path) -> int:
    return sum(
        item.stat().st_size
        for item in path.rglob("*")
        if item.is_file()
    )


def main_executable(app: Path) -> Path:
    directory = app / "Contents" / "MacOS"
    candidates = [
        item
        for item in directory.iterdir()
        if item.is_file() and os.access(item, os.X_OK)
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected one executable in {directory}, found {len(candidates)}"
        )
    return candidates[0]


def file_description(path: Path) -> str:
    return subprocess.run(
        ["file", "-b", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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
        raise RuntimeError("Tauri sidecar returned a non-object response")
    return decoded


def terminate_pid(pid: int) -> None:
    if pid <= 1:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    args = parser.parse_args(argv)

    app = Path(args.app).expanduser().resolve()
    if not app.is_dir():
        raise SystemExit(f"Missing Tauri app bundle: {app}")
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    data_dir = Path(args.data_dir).expanduser().resolve()
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    runtime_path = data_dir / "local-service.json"
    token_path = data_dir / "local-service.token"
    executable = main_executable(app)
    log_path = output.with_suffix(".tauri.log")

    metrics: dict[str, Any] = {
        "framework": "tauri",
        "status": "failed",
        "platform": platform.platform(),
        "machine": platform.machine(),
        "app": str(app),
        "app_bytes": directory_size(app),
        "executable": str(executable),
        "executable_description": file_description(executable),
        "log_path": str(log_path),
    }
    process: subprocess.Popen[bytes] | None = None
    sidecar_pid = 0
    started = time.perf_counter()
    try:
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [str(executable)],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            deadline = time.monotonic() + 60.0
            runtime: dict[str, Any] | None = None
            token = ""
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Tauri app exited before sidecar startup with code {process.returncode}"
                    )
                if runtime_path.is_file() and token_path.is_file():
                    try:
                        decoded = json.loads(runtime_path.read_text(encoding="utf-8"))
                        if isinstance(decoded, dict):
                            runtime = decoded
                            token = token_path.read_text(encoding="utf-8").strip()
                    except (OSError, ValueError):
                        runtime = None
                    if runtime and token:
                        break
                time.sleep(0.1)
            if not runtime or not token:
                raise RuntimeError("Tauri app did not start its packaged sidecar")
            sidecar_pid = int(runtime.get("pid") or 0)
            base_url = f"http://{runtime.get('host')}:{int(runtime.get('port') or 0)}"
            health: dict[str, Any] | None = None
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                try:
                    health = request_json(base_url, token, "GET", "/health")
                    break
                except OSError:
                    time.sleep(0.1)
            if not health or health.get("status") != "ok":
                raise RuntimeError("Tauri packaged sidecar did not become healthy")
            ready_at = time.perf_counter()
            request = {
                "job_type": "system.health",
                "payload": {"checks": ["runtime", "sqlite", "tauri-app"]},
                "requested_target": "auto",
                "idempotency_key": f"tauri-app-smoke-{os.getpid()}-{time.time_ns()}",
            }
            route = request_json(base_url, token, "POST", "/v1/route", request)
            submitted = request_json(base_url, token, "POST", "/v1/jobs", request)
            job_id = str((submitted.get("job") or {}).get("id") or "")
            if not job_id:
                raise RuntimeError("Tauri sidecar returned no job id")
            terminal: dict[str, Any] | None = None
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                current = request_json(base_url, token, "GET", f"/v1/jobs/{job_id}")
                if bool(current.get("terminal")):
                    terminal = current
                    break
                time.sleep(0.1)
            if not terminal or terminal.get("status") != "complete":
                raise RuntimeError("Tauri packaged health job did not complete")
            metrics.update(
                {
                    "status": "passed",
                    "route": route,
                    "service_ready_seconds": round(ready_at - started, 4),
                    "job_seconds": round(time.perf_counter() - ready_at, 4),
                    "total_seconds": round(time.perf_counter() - started, 4),
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
        terminate_pid(sidecar_pid)
        output.write_text(
            json.dumps(metrics, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
