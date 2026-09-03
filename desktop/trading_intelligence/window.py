"""Main production Trading Intelligence desktop window."""

from __future__ import annotations

import json
from pathlib import Path
import platform
import queue
import threading
import time
from typing import Any
from urllib.error import HTTPError

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from hybrid_runtime.desktop_settings import (
    DesktopSettings,
    DesktopSettingsError,
    load_desktop_settings,
    save_desktop_settings,
)
from hybrid_runtime.keychain import (
    KeychainError,
    KeychainUnavailable,
    MacOSKeychain,
)

from .pages import ConnectionPage, JobsPage, ProfitFirstPage
from .theme import STYLESHEET


def clean_error(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        try:
            return exc.read().decode("utf-8", errors="replace")[:1_000]
        except OSError:
            pass
    return " ".join(str(exc).split())[:1_000]


def write_metrics(path: str, metrics: dict[str, Any]) -> None:
    if not str(path or "").strip():
        return
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class MainWindow(QMainWindow):
    def __init__(
        self,
        runtime: Any,
        *,
        smoke: bool = False,
        metrics_output: str = "",
    ) -> None:
        super().__init__()
        self.runtime = runtime
        self.smoke = smoke
        self.metrics_output = metrics_output
        self.started = time.perf_counter()
        self.smoke_finished = False
        self.smoke_return_code = 1
        self.active_job_id = ""
        self.active_purpose = ""
        self.active_route: dict[str, Any] = {}
        self.last_plan: dict[str, Any] = {}
        self.metrics: dict[str, Any] = {
            "status": "failed",
            "framework": "pyside6",
            "product": "trading-intelligence-desktop",
            "full_gui": True,
            "platform": platform.platform(),
            "machine": platform.machine(),
        }
        self.setWindowTitle("Trading Intelligence")
        self.resize(1180, 780)
        self.setMinimumSize(900, 620)
        self.setStyleSheet(STYLESHEET)

        shell = QHBoxLayout()
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        root = QWidget()
        root.setLayout(shell)
        self.setCentralWidget(root)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(218)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 20, 16, 16)
        brand = QLabel("Trading Intelligence")
        brand.setObjectName("Brand")
        brand_sub = QLabel("Hybrid research desktop")
        brand_sub.setObjectName("BrandSub")
        side.addWidget(brand)
        side.addWidget(brand_sub)
        side.addSpacing(18)

        self.stack = QStackedWidget()
        settings_error = ""
        try:
            settings = load_desktop_settings(runtime.data_dir)
        except DesktopSettingsError as exc:
            settings = DesktopSettings()
            settings_error = clean_error(exc)
        self.profit_first = ProfitFirstPage()
        self.jobs = JobsPage()
        self.connection = ConnectionPage(settings)
        if settings_error:
            self.connection.status.setText(
                "Settings were reset for safety: " + settings_error
            )
        for page in (self.profit_first, self.jobs, self.connection):
            self.stack.addWidget(page)

        self.nav_buttons: list[QPushButton] = []
        for index, caption in enumerate(
            ("Profit First", "Durable Jobs", "Connection Settings")
        ):
            button = QPushButton(caption)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(
                lambda _checked=False, selected=index: self.show_page(selected)
            )
            side.addWidget(button)
            self.nav_buttons.append(button)
        side.addStretch(1)
        safety = QLabel(
            "Research only · strict validation gates remain authoritative · no brokerage orders"
        )
        safety.setObjectName("Subtle")
        safety.setWordWrap(True)
        side.addWidget(safety)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(22, 18, 22, 18)
        self.top_status = QLabel("Starting authenticated local service…")
        self.top_status.setObjectName("Subtle")
        content_layout.addWidget(self.top_status)
        content_layout.addWidget(self.stack, 1)
        shell.addWidget(sidebar)
        shell.addWidget(content, 1)

        self.profit_first.refresh_requested.connect(self.refresh_profit_first)
        self.jobs.refresh_requested.connect(self.refresh_jobs)
        self.jobs.cancel_requested.connect(self.cancel_job)
        self.jobs.reconnect_requested.connect(self.reconnect_cloud_job)
        self._reconnect_results = queue.Queue()
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.timeout.connect(self._finish_cloud_reconnect)
        self.connection.saved.connect(self.save_connection)
        self.connection.test_requested.connect(self.test_connection)

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_active_job)
        self.poll_timer.start(100)
        self.jobs_timer = QTimer(self)
        self.jobs_timer.timeout.connect(self.refresh_jobs)
        self.jobs_timer.start(2_500)
        self.watchdog = QTimer(self)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(
            lambda: self.fail_smoke(RuntimeError("Desktop Home smoke exceeded 90 seconds"))
        )
        if smoke:
            self.watchdog.start(90_000)
        QTimer.singleShot(50, self.wait_for_health)

    def show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setChecked(button_index == index)

    def wait_for_health(self) -> None:
        try:
            health = self.runtime.request_json("GET", "/health")
            if health.get("status") != "ok":
                raise RuntimeError("The local service health response was not ok")
            self.metrics["service_ready_seconds"] = round(
                time.perf_counter() - self.started,
                4,
            )
            self.top_status.setText("Local service ready · strategy candidates use durable SQLite jobs")
            self.refresh_profit_first()
        except BaseException as exc:
            if (
                time.perf_counter() - self.started < 45.0
                and self.runtime.process is not None
                and self.runtime.process.poll() is None
            ):
                QTimer.singleShot(120, self.wait_for_health)
            else:
                self.profit_first.set_error(clean_error(exc))
                self.fail_smoke(exc)

    def submit_job(self, request: dict[str, Any], purpose: str) -> None:
        if self.active_job_id:
            return
        decision = self.runtime.request_json("POST", "/v1/route", request)
        submitted = self.runtime.request_json("POST", "/v1/jobs", request)
        job_id = str((submitted.get("job") or {}).get("id") or "")
        if not job_id:
            raise RuntimeError(f"{purpose} returned no durable job id")
        self.active_job_id = job_id
        self.active_purpose = purpose
        self.active_route = decision
        purpose_label = {
            "profit_first": "Home",
            "profit_first_validation": "Strict Validation",
            "market_discovery_options": "Find Stocks",
            "market_discovery": "Find Stocks",
        }.get(purpose, purpose.replace("_", " ").title())
        self.top_status.setText(
            f"{purpose_label} · {decision.get('target')} · {decision.get('reason')}"
        )

    def refresh_profit_first(self) -> None:
        if self.active_job_id:
            return
        self.profit_first.set_working(
            "Reading the authoritative library",
            "Loading strategies and validation history without changing the web application.",
            0.02,
        )
        request = {
            "job_type": "strategy.profit_first_plan",
            "payload": {"maximum_candidates": 3},
            "requested_target": "auto",
            "idempotency_key": f"desktop-profit-first-plan-{time.time_ns()}",
            "engine_version": "desktop-profit-first-v1",
        }
        try:
            self.submit_job(request, "profit_first")
        except BaseException as exc:
            self.profit_first.set_error(clean_error(exc))
            self.fail_smoke(exc)

    def refresh_jobs(self) -> None:
        try:
            payload = self.runtime.request_json("GET", "/v1/jobs?limit=100")
            jobs = [item for item in payload.get("jobs") or [] if isinstance(item, dict)]
            self.jobs.render_jobs(jobs)
        except BaseException as exc:
            self.jobs.summary.setText("Job history unavailable: " + clean_error(exc))

    def cancel_job(self, job_id: str) -> None:
        try:
            self.runtime.request_json("POST", f"/v1/jobs/{job_id}/cancel", {})
            self.refresh_jobs()
        except BaseException as exc:
            self.jobs.summary.setText("Cancellation failed: " + clean_error(exc))

    def reconnect_cloud_job(self, job_id: str) -> None:
        if self.jobs.reconnect_busy:
            return
        self.jobs.reconnect_busy = True
        self.jobs._update_reconnect()
        self.jobs.reconnect_status.setText("Verifying the exact cloud run… No new research will be started.")
        # Large private-library reads must not block Qt's event loop. The daemon
        # only calls the authenticated sidecar; widget updates stay on the UI thread.
        runtime, results = self.runtime, self._reconnect_results

        def request() -> None:
            try:
                result = runtime.request_json("POST", f"/v1/jobs/{job_id}/reconnect-cloud", {}, timeout=180.0)
                results.put((result, ""))
            except Exception as exc:
                results.put((None, clean_error(exc)))

        threading.Thread(target=request, name="cloud-reconnect-request", daemon=True).start()
        self._reconnect_timer.start(100)

    def _finish_cloud_reconnect(self) -> None:
        try:
            result, error = self._reconnect_results.get_nowait()
        except queue.Empty:
            return
        self._reconnect_timer.stop()
        self.jobs.reconnect_busy = False
        self.refresh_jobs()
        if error:
            self.jobs.reconnect_status.setText("Reconnect not confirmed: " + error + " Refresh jobs before trying again.")
            return
        self.jobs.reconnect_status.setText("Reconnected to the same cloud run. Failure history preserved; no research dispatched.")
        # Resume the dedicated Finder view as well as the Durable Jobs table.
        if hasattr(self, "finder_job_id") and not self.finder_job_id:
            self.finder_job_id = str(result.get("id") or "")
            self._last_finder_poll_at = 0.0

    def save_connection(self, raw_settings: dict[str, Any], token: str) -> None:
        try:
            settings = DesktopSettings.from_mapping(raw_settings)
            save_desktop_settings(settings, self.runtime.data_dir)
            if token:
                MacOSKeychain().set_secret(settings.keychain_account, token)
            self.connection.populate(settings)
            self.connection.status.setText(
                "Saved. Non-secret settings use an owner-only file; the token is in macOS Keychain."
            )
            self.show_page(0)
            QTimer.singleShot(50, self.refresh_profit_first)
        except (
            DesktopSettingsError,
            ValueError,
            KeychainError,
            KeychainUnavailable,
            OSError,
        ) as exc:
            self.connection.status.setText("Save failed: " + clean_error(exc))
            self.fail_smoke(exc)

    def test_connection(self) -> None:
        if self.active_job_id:
            self.connection.status.setText("A local job is already running.")
            return
        request = {
            "job_type": "library.summary",
            "payload": {},
            "requested_target": "auto",
            "idempotency_key": f"desktop-library-test-{time.time_ns()}",
        }
        try:
            self.connection.status.setText("Testing the configured library source…")
            self.submit_job(request, "connection_test")
        except BaseException as exc:
            self.connection.status.setText("Connection failed: " + clean_error(exc))

    def poll_active_job(self) -> None:
        if not self.active_job_id:
            return
        purpose = self.active_purpose
        try:
            job = self.runtime.request_json("GET", f"/v1/jobs/{self.active_job_id}")
            progress = float(job.get("progress") or 0.0)
            if purpose == "profit_first":
                self.profit_first.set_working(
                    str(job.get("stage") or "working").replace("_", " ").title(),
                    str(job.get("status") or "working").replace("_", " "),
                    progress,
                )
            if not bool(job.get("terminal")):
                return
            if job.get("status") != "complete":
                message = (job.get("error") or {}).get("message") or str(job.get("status"))
                raise RuntimeError(message)
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            self.active_job_id = ""
            self.active_purpose = ""
            if purpose == "profit_first":
                self.last_plan = result
                self.profit_first.render_plan(result)
                self.refresh_jobs()
                self.metrics["profit_first_queue_status"] = result.get("queue_status")
                self.metrics["profit_first_eligible_count"] = int(
                    result.get("eligible_count") or 0
                )
                self.metrics["library_source"] = (result.get("library") or {}).get("source")
                self.metrics["route"] = dict(self.active_route)
                if self.smoke:
                    QTimer.singleShot(80, self.submit_ui_ready)
            elif purpose == "connection_test":
                self.connection.status.setText(
                    "Connected · "
                    f"{int(result.get('strategies') or 0):,} strategies · "
                    f"{int(result.get('validation_runs') or 0):,} validation runs"
                )
            elif purpose == "ui_ready":
                if self.smoke:
                    self.finish_smoke(result)
        except BaseException as exc:
            self.active_job_id = ""
            self.active_purpose = ""
            if purpose == "profit_first":
                self.profit_first.set_error(clean_error(exc))
            elif purpose == "connection_test":
                self.connection.status.setText("Connection failed: " + clean_error(exc))
            self.fail_smoke(exc)

    def submit_ui_ready(self) -> None:
        if self.active_job_id:
            QTimer.singleShot(50, self.submit_ui_ready)
            return
        request = {
            "job_type": "system.health",
            "payload": {
                "checks": [
                    "production-pyside-ui",
                    "profit-first-plan",
                    "durable-sqlite-job",
                    "authenticated-sidecar",
                ],
                "client_metrics": {
                    "framework": "pyside6",
                    "page": "profit_first",
                    "queue_status": self.last_plan.get("queue_status"),
                    "ui_ready_seconds": round(time.perf_counter() - self.started, 4),
                },
            },
            "requested_target": "auto",
            "idempotency_key": f"desktop-ui-ready-{time.time_ns()}",
        }
        try:
            self.submit_job(request, "ui_ready")
        except BaseException as exc:
            self.fail_smoke(exc)

    def finish_smoke(self, result: dict[str, Any]) -> None:
        if self.smoke_finished:
            return
        self.smoke_finished = True
        self.watchdog.stop()
        self.metrics.update(
            {
                "status": "passed",
                "total_seconds": round(time.perf_counter() - self.started, 4),
                "result": result,
                "queue_status": self.last_plan.get("queue_status"),
                "candidate_count": len(self.last_plan.get("candidates") or []),
            }
        )
        write_metrics(self.metrics_output, self.metrics)
        self.smoke_return_code = 0
        QTimer.singleShot(0, QApplication.instance().quit)

    def fail_smoke(self, exc: BaseException) -> None:
        if not self.smoke or self.smoke_finished:
            return
        self.smoke_finished = True
        self.watchdog.stop()
        self.metrics.update(
            {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": clean_error(exc),
                "total_seconds": round(time.perf_counter() - self.started, 4),
            }
        )
        write_metrics(self.metrics_output, self.metrics)
        self.smoke_return_code = 1
        QTimer.singleShot(0, QApplication.instance().quit)
