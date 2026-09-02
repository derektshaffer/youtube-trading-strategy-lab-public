"""Start and authenticate the packaged local Python service."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


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


class DesktopRuntime:
    def __init__(self, *, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.port = available_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.token = secrets.token_urlsafe(48)
        self.process: subprocess.Popen[bytes] | None = None
        self._log = None

    def _command(self) -> list[str]:
        arguments = [
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--data-dir",
            str(self.data_dir),
        ]
        sidecar = packaged_sidecar()
        if sidecar is not None:
            return [str(sidecar), *arguments]
        if bool(getattr(sys, "frozen", False)):
            raise RuntimeError("The packaged Trading Intelligence service is missing")
        return [sys.executable, "-m", "hybrid_runtime.server", *arguments]

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        environment = os.environ.copy()
        environment["TRADING_INTELLIGENCE_LOCAL_TOKEN"] = self.token
        environment["TRADING_INTELLIGENCE_DESKTOP_DATA_DIR"] = str(self.data_dir)
        environment["PYTHONUNBUFFERED"] = "1"
        log_path = self.data_dir / "desktop-service.log"
        self._log = log_path.open("ab")
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
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self._log is not None:
            self._log.close()
            self._log = None

    def request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 8.0,
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
        with urlopen(request, timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8"))
        if not isinstance(decoded, dict):
            raise RuntimeError("The local service returned a non-object response")
        return decoded

    def wait_until_ready(self, timeout_seconds: float = 45.0) -> dict[str, Any]:
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        last_error: BaseException | None = None
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError(
                    f"The local service exited with code {self.process.returncode}"
                )
            try:
                health = self.request_json("GET", "/health", timeout=2.0)
                if health.get("status") == "ok":
                    return health
            except (OSError, URLError, ValueError) as exc:
                last_error = exc
                time.sleep(0.1)
        if last_error is not None:
            raise RuntimeError(
                "The local service did not become ready: "
                + " ".join(str(last_error).split())[:300]
            ) from last_error
        raise RuntimeError("The local service did not become ready")
