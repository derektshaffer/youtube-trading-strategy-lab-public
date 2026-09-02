"""Full PySide6 window and apples-to-apples GUI smoke harness."""

from __future__ import annotations

import platform
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError


def run_gui(
    runtime: Any,
    *,
    gui_smoke: bool = False,
    metrics_output: str = "",
    frozen_app_bundle: Any,
    directory_size: Any,
    write_metrics: Any,
) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QPushButton,
        QProgressBar,
        QVBoxLayout,
        QWidget,
    )

    from pyside_chart import CandleChart

    class Window(QMainWindow):
        def __init__(self, started: float) -> None:
            super().__init__()
            self.started = started
            self.health_attempts = 0
            self.active_job_id = ""
            self.active_purpose = ""
            self.active_started = 0.0
            self.active_route: dict[str, Any] = {}
            self.timeframe = "5Min"
            self.smoke_finished = False
            self.smoke_return_code = 1
            self.metrics: dict[str, Any] = {
                "framework": "pyside6",
                "status": "failed",
                "full_gui": True,
                "platform": platform.platform(),
                "machine": platform.machine(),
            }
            bundle = frozen_app_bundle()
            if bundle is not None:
                self.metrics["app"] = str(bundle)
                self.metrics["app_bytes"] = directory_size(bundle)

            self.setWindowTitle("Trading Intelligence — PySide6 spike")
            self.resize(1100, 760)
            self.setMinimumSize(760, 560)
            self.setStyleSheet(
                """
                QMainWindow, QWidget { background: #07101c; color: #eef6ff; }
                QLabel#Eyebrow { color: #70d7ff; font-size: 11px; font-weight: 800; }
                QLabel#Title { font-size: 28px; font-weight: 850; }
                QFrame#Card { background: #0d1929; border: 1px solid #293e58; border-radius: 14px; }
                QPushButton { background: #101f32; color: #afc2d5; border: 1px solid #2a425d; border-radius: 8px; padding: 6px 10px; font-weight: 700; }
                QPushButton:checked { background: #75dcff; color: #07101c; border-color: #75dcff; }
                QPushButton#Primary { background: #58d9ae; color: #06140f; border: 0; padding: 10px 12px; font-weight: 850; }
                QPushButton:disabled { color: #536579; background: #0b1725; }
                QProgressBar { border: 1px solid #2a425d; border-radius: 7px; background: #08131f; min-height: 14px; text-align: center; }
                QProgressBar::chunk { background: #58d9ae; border-radius: 6px; }
                QCheckBox { color: #aabed2; }
                """
            )

            header = QVBoxLayout()
            eyebrow = QLabel("DESKTOP FRAMEWORK COMPARISON")
            eyebrow.setObjectName("Eyebrow")
            title = QLabel("Trading Intelligence")
            title.setObjectName("Title")
            subtitle = QLabel(
                "Full-window startup, authenticated Python sidecar, durable jobs, and interactive chart."
            )
            subtitle.setStyleSheet("color:#91a7bd;")
            header.addWidget(eyebrow)
            header.addWidget(title)
            header.addWidget(subtitle)

            chart_card = QFrame()
            chart_card.setObjectName("Card")
            chart_layout = QVBoxLayout(chart_card)
            chart_top = QHBoxLayout()
            self.symbol = QLabel("SDOT  5m")
            self.symbol.setStyleSheet("font-size:20px;font-weight:850;")
            self.quote = QLabel("Loading chart…")
            self.quote.setStyleSheet("color:#62e4ab;font-weight:780;")
            chart_top.addWidget(self.symbol)
            chart_top.addStretch(1)
            chart_top.addWidget(self.quote)
            chart_layout.addLayout(chart_top)

            controls = QHBoxLayout()
            self.timeframe_buttons: dict[str, QPushButton] = {}
            for value, caption in (
                ("1Min", "1m"),
                ("5Min", "5m"),
                ("15Min", "15m"),
                ("1Hour", "1h"),
            ):
                button = QPushButton(caption)
                button.setCheckable(True)
                button.setChecked(value == self.timeframe)
                button.clicked.connect(
                    lambda _checked=False, selected=value: self.request_chart(selected)
                )
                self.timeframe_buttons[value] = button
                controls.addWidget(button)
            reset = QPushButton("Reset")
            controls.addWidget(reset)
            controls.addStretch(1)
            self.vwap = QCheckBox("VWAP")
            self.vwap.setChecked(True)
            self.ema = QCheckBox("EMA 9")
            self.ema.setChecked(True)
            controls.addWidget(self.vwap)
            controls.addWidget(self.ema)
            chart_layout.addLayout(controls)

            self.chart = CandleChart()
            chart_layout.addWidget(self.chart, 1)
            hint = QLabel("Scroll to zoom · drag to pan · move for crosshair")
            hint.setStyleSheet("color:#60768d;font-size:10px;")
            chart_layout.addWidget(hint)
            reset.clicked.connect(self.chart.reset_view)
            self.vwap.toggled.connect(self.toggle_indicators)
            self.ema.toggled.connect(self.toggle_indicators)

            job_card = QFrame()
            job_card.setObjectName("Card")
            job_layout = QVBoxLayout(job_card)
            job_heading = QLabel("Local job service")
            job_heading.setStyleSheet("font-size:15px;font-weight:800;")
            self.status = QLabel("Starting authenticated local service…")
            self.status.setWordWrap(True)
            self.route = QLabel("Waiting for route decision.")
            self.route.setWordWrap(True)
            self.route.setStyleSheet("color:#83cdeb;font-size:11px;")
            self.progress = QProgressBar()
            self.progress.setRange(0, 1000)
            self.start = QPushButton("Run local health job")
            self.start.setObjectName("Primary")
            self.start.setEnabled(False)
            self.start.clicked.connect(self.submit_health)
            self.measurements = QLabel("First chart: —\nVisible bars: —\nData route: —")
            self.measurements.setStyleSheet("color:#91a7bd;font-size:11px;")
            warning = QLabel("Synthetic comparison data only. Never use this chart for trading.")
            warning.setWordWrap(True)
            warning.setStyleSheet("color:#d9b86f;font-size:10px;")
            for widget in (
                job_heading,
                self.status,
                self.route,
                self.progress,
                self.start,
                self.measurements,
                warning,
            ):
                job_layout.addWidget(widget)
            job_layout.addStretch(1)

            grid = QGridLayout()
            grid.addWidget(chart_card, 0, 0)
            grid.addWidget(job_card, 0, 1)
            grid.setColumnStretch(0, 1)
            grid.setColumnMinimumWidth(1, 280)

            outer = QVBoxLayout()
            outer.addLayout(header)
            outer.addSpacing(8)
            outer.addLayout(grid, 1)
            root = QWidget()
            root.setLayout(outer)
            self.setCentralWidget(root)

            self.poll_timer = QTimer(self)
            self.poll_timer.timeout.connect(self.poll_job)
            self.poll_timer.start(90)
            self.watchdog = QTimer(self)
            self.watchdog.setSingleShot(True)
            self.watchdog.timeout.connect(
                lambda: self.fail_smoke(RuntimeError("Full-GUI smoke exceeded 75 seconds"))
            )
            if gui_smoke:
                self.watchdog.start(75_000)
            QTimer.singleShot(50, self.check_health)

        def format_error(self, exc: BaseException) -> str:
            if isinstance(exc, HTTPError):
                try:
                    return exc.read().decode("utf-8", errors="replace")
                except OSError:
                    pass
            return str(exc)

        def show_error(self, exc: BaseException) -> None:
            self.status.setText("Error: " + self.format_error(exc))

        def fail_smoke(self, exc: BaseException) -> None:
            self.show_error(exc)
            if not gui_smoke or self.smoke_finished:
                return
            self.smoke_finished = True
            self.metrics["error_type"] = type(exc).__name__
            self.metrics["error"] = " ".join(self.format_error(exc).split())[:1_000]
            self.metrics["total_seconds"] = round(
                time.perf_counter() - self.started,
                4,
            )
            write_metrics(metrics_output, self.metrics)
            self.smoke_return_code = 1
            QTimer.singleShot(0, QApplication.instance().quit)

        def check_health(self) -> None:
            self.health_attempts += 1
            try:
                result = runtime.request_json("GET", "/health")
                if result.get("status") != "ok":
                    raise RuntimeError("Local service health response was not ok")
                self.metrics["health"] = result
                self.metrics["service_ready_seconds"] = round(
                    time.perf_counter() - self.started,
                    4,
                )
                self.status.setText("Local service: ready")
                self.start.setEnabled(True)
                self.request_chart(self.timeframe)
            except (OSError, URLError, ValueError, RuntimeError) as exc:
                if (
                    self.health_attempts < 300
                    and runtime.process is not None
                    and runtime.process.poll() is None
                ):
                    QTimer.singleShot(100, self.check_health)
                else:
                    self.fail_smoke(exc)

        def submit_request(
            self,
            request: dict[str, Any],
            *,
            purpose: str,
        ) -> None:
            if self.active_job_id:
                return
            decision = runtime.request_json("POST", "/v1/route", request)
            submitted = runtime.request_json("POST", "/v1/jobs", request)
            job_id = str((submitted.get("job") or {}).get("id") or "")
            if not job_id:
                raise RuntimeError(f"{purpose} returned no job id")
            self.active_job_id = job_id
            self.active_purpose = purpose
            self.active_started = time.perf_counter()
            self.active_route = decision
            self.route.setText(
                f"Route: {decision.get('target')} — {decision.get('reason')}"
            )
            self.progress.setValue(0)

        def request_chart(self, timeframe: str) -> None:
            if self.active_job_id:
                return
            self.timeframe = timeframe
            for value, button in self.timeframe_buttons.items():
                button.setChecked(value == timeframe)
                button.setEnabled(False)
            self.symbol.setText(
                "SDOT  " + {"1Min": "1m", "5Min": "5m", "15Min": "15m", "1Hour": "1h"}.get(
                    timeframe,
                    timeframe,
                )
            )
            request = {
                "job_type": "chart.framework_fixture",
                "payload": {"symbol": "SDOT", "timeframe": timeframe, "bars": 220},
                "requested_target": "auto",
                "idempotency_key": f"pyside-chart-{timeframe}-{time.time_ns()}",
            }
            try:
                self.submit_request(request, purpose="chart")
            except BaseException as exc:
                for button in self.timeframe_buttons.values():
                    button.setEnabled(True)
                self.fail_smoke(exc)

        def toggle_indicators(self) -> None:
            self.chart.show_vwap = self.vwap.isChecked()
            self.chart.show_ema = self.ema.isChecked()
            self.chart.update()

        def submit_health(self) -> None:
            request = {
                "job_type": "system.health",
                "payload": {"checks": ["runtime", "sqlite", "manual-button"]},
                "requested_target": "auto",
                "idempotency_key": f"pyside-health-{time.time_ns()}",
            }
            try:
                self.start.setEnabled(False)
                self.submit_request(request, purpose="health")
            except BaseException as exc:
                self.start.setEnabled(True)
                self.show_error(exc)

        def register_ui_ready(self) -> None:
            if self.active_job_id:
                QTimer.singleShot(30, self.register_ui_ready)
                return
            if self.chart.last_render_ms <= 0:
                QTimer.singleShot(30, self.register_ui_ready)
                return
            request = {
                "job_type": "system.health",
                "payload": {
                    "checks": ["pyside-ui", "chart-rendered", "authenticated-sidecar"],
                    "client_metrics": {
                        "framework": "pyside6",
                        "chart_render_ms": round(self.chart.last_render_ms, 4),
                        "chart_bars": len(self.chart.candles),
                        "timeframe": self.timeframe,
                        "ui_ready_monotonic_ms": round(
                            (time.perf_counter() - self.started) * 1000.0,
                            4,
                        ),
                    },
                },
                "requested_target": "auto",
                "idempotency_key": f"pyside-ui-ready-{time.time_ns()}",
            }
            try:
                self.submit_request(request, purpose="ui_ready")
            except BaseException as exc:
                self.fail_smoke(exc)

        def finish_smoke(self, job: dict[str, Any]) -> None:
            if self.smoke_finished:
                return
            self.smoke_finished = True
            self.watchdog.stop()
            self.metrics.update(
                {
                    "status": "passed",
                    "route": dict(self.active_route),
                    "chart_render_ms": round(self.chart.last_render_ms, 4),
                    "chart_bars": len(self.chart.candles),
                    "visible_bars": len(self.chart.visible_rows()),
                    "result": job.get("result") or {},
                    "total_seconds": round(
                        time.perf_counter() - self.started,
                        4,
                    ),
                }
            )
            write_metrics(metrics_output, self.metrics)
            self.smoke_return_code = 0
            QTimer.singleShot(0, QApplication.instance().quit)

        def poll_job(self) -> None:
            if not self.active_job_id:
                return
            purpose = self.active_purpose
            try:
                job = runtime.request_json(
                    "GET",
                    f"/v1/jobs/{self.active_job_id}",
                )
                self.progress.setValue(round(float(job.get("progress") or 0.0) * 1000))
                self.status.setText(f"{job.get('status')} · {job.get('stage')}")
                if not bool(job.get("terminal")):
                    return
                if job.get("status") != "complete":
                    detail = (job.get("error") or {}).get("message") or job.get("status")
                    raise RuntimeError(f"{purpose} failed: {detail}")

                elapsed = time.perf_counter() - self.active_started
                self.active_job_id = ""
                self.active_purpose = ""
                if purpose == "chart":
                    result = job.get("result") if isinstance(job.get("result"), dict) else {}
                    candles = result.get("candles") if isinstance(result, dict) else []
                    if not isinstance(candles, list) or not candles:
                        raise RuntimeError("Chart fixture returned no candles")
                    self.chart.set_candles(candles)
                    latest = candles[-1]
                    self.quote.setText(f"${float(latest['close']):.2f} · synthetic")
                    self.metrics["chart_job_seconds"] = round(elapsed, 4)
                    self.metrics["chart_route"] = dict(self.active_route)
                    for button in self.timeframe_buttons.values():
                        button.setEnabled(True)
                    self.status.setText(f"Chart ready · {len(candles)} bars")
                    self.progress.setValue(1000)
                    QTimer.singleShot(80, self.register_ui_ready)
                elif purpose == "ui_ready":
                    self.status.setText("Full desktop UI ready")
                    self.progress.setValue(1000)
                    if gui_smoke:
                        self.finish_smoke(job)
                elif purpose == "health":
                    self.status.setText("Local health job complete")
                    self.progress.setValue(1000)
                    self.start.setEnabled(True)

                self.measurements.setText(
                    (
                        f"First chart: {self.chart.last_render_ms:.2f} ms\n"
                        f"Visible bars: {len(self.chart.visible_rows())}\n"
                        f"Data route: {self.active_route.get('target') or 'local'}"
                    )
                )
            except (OSError, URLError, ValueError, KeyError, RuntimeError) as exc:
                self.active_job_id = ""
                self.active_purpose = ""
                for button in self.timeframe_buttons.values():
                    button.setEnabled(True)
                self.start.setEnabled(True)
                self.fail_smoke(exc)

    started = time.perf_counter()
    runtime.start()
    application = QApplication(sys.argv[:1])
    application.setApplicationName("Trading Intelligence")
    application.aboutToQuit.connect(runtime.stop)
    window = Window(started)
    window.show()
    result = application.exec()
    if gui_smoke:
        return window.smoke_return_code
    return result
