"""PySide6 framework spike against the shared loopback job API."""

from __future__ import annotations

import atexit
import json
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


PORT = available_port()
SERVICE_URL = f"http://127.0.0.1:{PORT}"
TOKEN = secrets.token_urlsafe(48)
DATA_DIR = Path(tempfile.mkdtemp(prefix="trading-intelligence-pyside-spike-"))
SERVICE_PROCESS = subprocess.Popen(
    [
        sys.executable,
        "-m",
        "hybrid_runtime.server",
        "--host",
        "127.0.0.1",
        "--port",
        str(PORT),
        "--token",
        TOKEN,
        "--data-dir",
        str(DATA_DIR),
    ],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)


def stop_service() -> None:
    if SERVICE_PROCESS.poll() is None:
        SERVICE_PROCESS.terminate()
        try:
            SERVICE_PROCESS.wait(timeout=2)
        except subprocess.TimeoutExpired:
            SERVICE_PROCESS.kill()


atexit.register(stop_service)


def request_json(method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        SERVICE_URL + path,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


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
            result = request_json("GET", "/health")
            self.status.setText("Local service: " + result.get("status", "unknown"))
            self.start.setEnabled(True)
        except (OSError, URLError, ValueError) as exc:
            if self.health_attempts < 40 and SERVICE_PROCESS.poll() is None:
                QTimer.singleShot(150, self.check_health)
            else:
                self.show_error(exc)

    def submit_job(self) -> None:
        try:
            payload = {
                "job_type": "system.health",
                "payload": {"checks": ["runtime", "sqlite"]},
                "requested_target": "auto",
                "idempotency_key": "pyside6-spike-health",
            }
            route = request_json("POST", "/v1/route", payload)
            self.route.setText(f"Route: {route['target']} — {route['reason']}")
            result = request_json("POST", "/v1/jobs", payload)
            self.job_id = result["job"]["id"]
            self.start.setEnabled(False)
        except (OSError, URLError, ValueError, KeyError) as exc:
            self.show_error(exc)

    def poll_job(self) -> None:
        if not self.job_id:
            return
        try:
            job = request_json("GET", f"/v1/jobs/{self.job_id}")
            self.progress.setValue(round(float(job.get("progress") or 0) * 1000))
            self.status.setText(f"{job['status']} · {job['stage']}")
            if job.get("terminal"):
                self.start.setEnabled(True)
                self.job_id = ""
        except (OSError, URLError, ValueError, KeyError) as exc:
            self.show_error(exc)


def main() -> int:
    application = QApplication(sys.argv)
    application.aboutToQuit.connect(stop_service)
    window = Window()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
