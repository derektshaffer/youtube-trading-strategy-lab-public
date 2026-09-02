"""PySide6 framework spike against the shared authenticated loopback API."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def default_data_dir() -> Path:
    configured = str(os.environ.get("TRADING_INTELLIGENCE_SPIKE_DATA_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Trading Intelligence Lab"
        / "PySide6 Spike"
    )


def packaged_sidecar() -> Path | None:
    override = str(os.environ.get("TRADING_INTELLIGENCE_SIDECAR_PATH") or "").strip()
    if override:
        candidate = Path(override).expanduser().resolve()
        return candidate if candidate.is_file() else None

    roots: list[Path] = []
    frozen_root = str(getattr(sys, "_MEIPASS", "") or "").strip()
    if frozen_root:
        roots.append(Path(frozen_root))
    executable = Path(sys.executable).resolve()
    roots.extend(
        [
            executable.parent,
            executable.parent / "_internal",
            executable.parent.parent / "Resources",
            executable.parent.parent / "Frameworks",
        ]
    )
    for root in roots:
        candidate = root / "trading-intelligence-service"
        if candidate.is_file():
            return candidate
    return None


class RuntimeService:
    def __init__(self, *, data_dir: Path | None = None) -> None:
        self.data_dir = (data_dir or default_data_dir()).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.port = available_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.token = secrets.token_urlsafe(48)
        self.process: subprocess.Popen[bytes] | None = None
        self._log = None
        self.started_at = 0.0

    def _command(self) -> list[str]:
        sidecar = packaged_sidecar()
        arguments = [
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--data-dir",
            str(self.data_dir),
        ]
        if sidecar is not None:
            return [str(sidecar), *arguments]
        if bool(getattr(sys, "frozen", False)):
            raise RuntimeError("The packaged Python service is missing from this app bundle")
        return [sys.executable, "-m", "hybrid_runtime.server", *arguments]

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        environment = os.environ.copy()
        environment["TRADING_INTELLIGENCE_LOCAL_TOKEN"] = self.token
        environment["PYTHONUNBUFFERED"] = "1"
        self._log = (self.data_dir / "pyside-service.log").open("ab")
        self.started_at = time.perf_counter()
        self.process = subprocess.Popen(
            self._command(),
            env=environment,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)
        if self._log is not None:
            self._log.close()
            self._log = None

    def request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=5) as response:
            decoded = json.loads(response.read().decode("utf-8"))
        if not isinstance(decoded, dict):
            raise RuntimeError("The local service returned a non-object response")
        return decoded

    def wait_until_ready(self, timeout_seconds: float = 45.0) -> dict[str, Any]:
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError(
                    f"The local service exited with code {self.process.returncode}"
                )
            try:
                health = self.request_json("GET", "/health")
                if health.get("status") == "ok":
                    return health
            except (OSError, URLError, ValueError):
                time.sleep(0.1)
        raise RuntimeError("The local service did not become ready")


def headless_smoke(runtime: RuntimeService, metrics_output: str = "") -> int:
    metrics: dict[str, Any] = {"framework": "pyside6", "status": "failed"}
    started = time.perf_counter()
    try:
        runtime.start()
        health = runtime.wait_until_ready()
        ready_at = time.perf_counter()
        request = {
            "job_type": "system.health",
            "payload": {"checks": ["runtime", "sqlite", "pyside-app"]},
            "requested_target": "auto",
            "idempotency_key": f"pyside-smoke-{os.getpid()}-{time.time_ns()}",
        }
        route = runtime.request_json("POST", "/v1/route", request)
        submitted = runtime.request_json("POST", "/v1/jobs", request)
        job_id = str((submitted.get("job") or {}).get("id") or "")
        if not job_id:
            raise RuntimeError("The local service returned no job id")
        deadline = time.monotonic() + 30.0
        terminal: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            current = runtime.request_json("GET", f"/v1/jobs/{job_id}")
            if bool(current.get("terminal")):
                terminal = current
                break
            time.sleep(0.1)
        if not terminal or terminal.get("status") != "complete":
            raise RuntimeError("The packaged PySide health job did not complete")
        metrics.update(
            {
                "status": "passed",
                "health": health,
                "route": route,
                "service_ready_seconds": round(ready_at - started, 4),
                "job_seconds": round(time.perf_counter() - ready_at, 4),
                "total_seconds": round(time.perf_counter() - started, 4),
                "result": terminal.get("result") or {},
            }
        )
        return_code = 0
    except BaseException as exc:
        metrics["error_type"] = type(exc).__name__
        metrics["error"] = " ".join(str(exc).split())[:1_000]
        return_code = 1
    finally:
        runtime.stop()
        text = json.dumps(metrics, indent=2, sort_keys=True) + "\n"
        if str(metrics_output or "").strip():
            path = Path(metrics_output).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
    return return_code


def run_gui(runtime: RuntimeService) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QApplication,
        QLabel,
        QMainWindow,
        QPushButton,
        QProgressBar,
        QVBoxLayout,
        QWidget,
    )

    class Window(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.job_id = ""
            self.health_attempts = 0
            self.setWindowTitle("Trading Intelligence — PySide6 spike")
            self.resize(760, 480)
            self.status = QLabel("Starting authenticated local service…")
            self.route = QLabel("Routing decision will appear here")
            self.progress = QProgressBar()
            self.progress.setRange(0, 1000)
            self.start = QPushButton("Run local health job")
            self.start.setEnabled(False)
            self.start.clicked.connect(self.submit_job)
            layout = QVBoxLayout()
            for widget in (self.status, self.route, self.progress, self.start):
                layout.addWidget(widget)
            root = QWidget()
            root.setLayout(layout)
            self.setCentralWidget(root)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.poll_job)
            self.timer.start(700)
            QTimer.singleShot(100, self.check_health)

        def show_error(self, exc: BaseException) -> None:
            if isinstance(exc, HTTPError):
                detail = exc.read().decode("utf-8", errors="replace")
            else:
                detail = str(exc)
            self.status.setText("Error: " + detail)

        def check_health(self) -> None:
            self.health_attempts += 1
            try:
                result = runtime.request_json("GET", "/health")
                self.status.setText("Local service: " + str(result.get("status") or "unknown"))
                self.start.setEnabled(True)
            except (OSError, URLError, ValueError) as exc:
                if self.health_attempts < 80 and runtime.process is not None and runtime.process.poll() is None:
                    QTimer.singleShot(150, self.check_health)
                else:
                    self.show_error(exc)

        def submit_job(self) -> None:
            try:
                payload = {
                    "job_type": "system.health",
                    "payload": {"checks": ["runtime", "sqlite"]},
                    "requested_target": "auto",
                    "idempotency_key": f"pyside-gui-{time.time_ns()}",
                }
                decision = runtime.request_json("POST", "/v1/route", payload)
                self.route.setText(
                    f"Route: {decision['target']} — {decision['reason']}"
                )
                result = runtime.request_json("POST", "/v1/jobs", payload)
                self.job_id = str(result["job"]["id"])
                self.start.setEnabled(False)
            except (OSError, URLError, ValueError, KeyError) as exc:
                self.show_error(exc)

        def poll_job(self) -> None:
            if not self.job_id:
                return
            try:
                job = runtime.request_json("GET", f"/v1/jobs/{self.job_id}")
                self.progress.setValue(round(float(job.get("progress") or 0) * 1000))
                self.status.setText(f"{job['status']} · {job['stage']}")
                if job.get("terminal"):
                    self.start.setEnabled(True)
                    self.job_id = ""
            except (OSError, URLError, ValueError, KeyError) as exc:
                self.show_error(exc)

    runtime.start()
    application = QApplication(sys.argv[:1])
    application.aboutToQuit.connect(runtime.stop)
    window = Window()
    window.show()
    return application.exec()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--headless-smoke", action="store_true")
    parser.add_argument("--metrics-output", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime = RuntimeService()
    if args.headless_smoke:
        return headless_smoke(runtime, args.metrics_output)
    return run_gui(runtime)


if __name__ == "__main__":
    raise SystemExit(main())
