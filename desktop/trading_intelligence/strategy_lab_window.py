"""Production desktop window with real cloud Strategy Lab ownership."""

from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton

from .results_window import MainWindow as ResultsMainWindow, clean_error, write_metrics
from .strategy_lab_page import StrategyLabPage


class MainWindow(ResultsMainWindow):
    def __init__(self, runtime: Any, *, smoke: bool = False, metrics_output: str = "") -> None:
        self.strategy_lab_job_id = ""
        self.strategy_lab_route: dict[str, Any] = {}
        self._last_strategy_lab_poll_at = 0.0
        super().__init__(runtime, smoke=smoke, metrics_output=metrics_output)
        self.strategy_lab = StrategyLabPage()
        self.stack.addWidget(self.strategy_lab)
        self.strategy_lab.options_requested.connect(self.refresh_strategy_lab_options)
        self.strategy_lab.run_requested.connect(self.run_strategy_lab)
        self._install_strategy_lab_navigation()
        QTimer.singleShot(500, self.refresh_strategy_lab_options)

    def _install_strategy_lab_navigation(self) -> None:
        page_index = self.stack.indexOf(self.strategy_lab)
        button = QPushButton("Strategy Lab")
        button.setCheckable(True)
        button.setProperty("stack_index", page_index)
        button.clicked.connect(
            lambda _checked=False, selected=page_index: self.show_page(selected)
        )
        sidebar = self.nav_buttons[0].parentWidget()
        layout = sidebar.layout() if sidebar is not None else None
        if layout is not None:
            insert_at = -1
            for index in range(layout.count()):
                widget = layout.itemAt(index).widget()
                if widget is not None and widget.text() == "Stock Strategy Finder":
                    insert_at = index + 1
                    break
            if insert_at >= 0:
                layout.insertWidget(insert_at, button)
            else:
                layout.addWidget(button)
        self.nav_buttons.append(button)

    def _restore_background_cloud_jobs(self) -> None:
        super()._restore_background_cloud_jobs()
        try:
            jobs = self._recent_jobs()
        except BaseException:
            return
        lab = self._matching_active_job(jobs, "strategy.strategy_lab")
        if lab is None:
            return
        self.strategy_lab_job_id = str(lab.get("id") or "")
        payload = lab.get("payload") if isinstance(lab.get("payload"), dict) else {}
        self.strategy_lab.ticker.setText(str(payload.get("ticker") or "SDOT").upper())
        timeframe = self.strategy_lab.timeframe.findData(str(payload.get("timeframe") or "5Min"))
        if timeframe >= 0:
            self.strategy_lab.timeframe.setCurrentIndex(timeframe)
        depth = self.strategy_lab.depth.findData(int(payload.get("search_depth") or 36))
        if depth >= 0:
            self.strategy_lab.depth.setCurrentIndex(depth)
        self.strategy_lab.history_days.setValue(int(payload.get("history_days") or 30))
        self.strategy_lab.set_working(
            f"Reconnected to {payload.get('ticker') or 'Strategy Lab'} cloud research",
            "This run continued on the remote worker while the desktop was closed.",
            float(lab.get("progress") or 0.0),
        )

    def refresh_strategy_lab_options(self) -> None:
        if self.active_job_id:
            return
        request = {
            "job_type": "library.strategy_lab_options",
            "payload": {"limit": 300},
            "requested_target": "auto",
            "idempotency_key": f"desktop-strategy-lab-options-{time.time_ns()}",
            "engine_version": "desktop-strategy-lab-options-v1",
        }
        try:
            self.strategy_lab.set_working(
                "Loading faithful strategies",
                "Applying the same current source-to-backtester integrity gate used by the web Strategy Lab.",
            )
            self.submit_job(request, "strategy_lab_options")
        except BaseException as exc:
            self.strategy_lab.set_error(clean_error(exc))

    def run_strategy_lab(self, payload: dict[str, Any]) -> None:
        if self.strategy_lab_job_id:
            self.strategy_lab.set_error(
                "A Strategy Lab cloud job is already attached. Its progress is shown here and in Durable Jobs."
            )
            return
        try:
            jobs = self._recent_jobs()
            existing = self._matching_active_job(jobs, "strategy.strategy_lab")
            if existing is not None:
                self.strategy_lab_job_id = str(existing.get("id") or "")
                self.strategy_lab.set_working(
                    "Attached to existing Strategy Lab cloud run",
                    "No duplicate research was submitted.",
                    float(existing.get("progress") or 0.0),
                )
                return
            started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            run_id = f"strategy-lab-desktop-{time.time_ns()}"
            cloud_payload = {
                **dict(payload),
                "run_id": run_id,
                "started_at": started_at,
                "research_end": started_at,
                "continue_after_app_exit": True,
            }
            depth = int(cloud_payload.get("search_depth") or 36)
            request = {
                "job_type": "strategy.strategy_lab",
                "payload": cloud_payload,
                "requested_target": "auto",
                "priority": 98 if depth >= 160 else (92 if depth >= 96 else 86),
                "idempotency_key": run_id,
                "engine_version": "strategy-lab-cloud-v1",
            }
            self.strategy_lab_job_id, self.strategy_lab_route = (
                self._submit_background_cloud_job(request)
            )
            self.strategy_lab.set_working(
                f"Queueing {cloud_payload.get('ticker')} Strategy Lab",
                "The run is now durably owned by the cloud path and can continue after this app or Mac closes.",
                0.01,
            )
            self.refresh_jobs()
        except BaseException as exc:
            self.strategy_lab_job_id = ""
            self.strategy_lab.set_error(clean_error(exc))

    def poll_active_job(self) -> None:
        now = time.monotonic()
        if (
            self.strategy_lab_job_id
            and now - self._last_strategy_lab_poll_at >= self._background_cloud_poll_seconds
        ):
            self._last_strategy_lab_poll_at = now
            self._poll_strategy_lab()
        if self.active_purpose == "strategy_lab_options":
            self._poll_strategy_lab_options()
            return
        super().poll_active_job()

    def _poll_strategy_lab_options(self) -> None:
        if not self.active_job_id:
            return
        try:
            job = self.runtime.request_json("GET", f"/v1/jobs/{self.active_job_id}")
            if not bool(job.get("terminal")):
                return
            if job.get("status") != "complete":
                message = (job.get("error") or {}).get("message") or str(job.get("status"))
                raise RuntimeError(message)
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            self.active_job_id = ""
            self.active_purpose = ""
            self.strategy_lab.set_options(result)
            self.refresh_jobs()
        except BaseException as exc:
            self.active_job_id = ""
            self.active_purpose = ""
            self.strategy_lab.set_error(clean_error(exc))
            self.refresh_jobs()

    def _poll_strategy_lab(self) -> None:
        job_id = self.strategy_lab_job_id
        try:
            job = self.runtime.request_json("GET", f"/v1/jobs/{job_id}")
            link = self._cloud_link(job_id)
            metadata = link.get("metadata") if isinstance(link.get("metadata"), dict) else {}
            progress = float(job.get("progress") or link.get("remote_progress") or 0.0)
            stage = str(job.get("stage") or link.get("remote_stage") or "cloud_queued").replace("_", " ")
            detail = str(metadata.get("distributed_message") or "").strip()
            error = str(link.get("dispatch_error") or "").strip()
            if error:
                detail = "Cloud connection required: " + error
            if not detail:
                detail = "The remote Strategy Lab worker owns this run; the Mac may be closed safely."
            self.strategy_lab.set_working(
                f"Strategy Lab · {stage.title()}",
                detail,
                progress,
            )
            if not bool(job.get("terminal")):
                return
            self.strategy_lab_job_id = ""
            if job.get("status") != "complete":
                message = (job.get("error") or {}).get("message") or str(job.get("status"))
                raise RuntimeError(message)
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            self.strategy_lab.render_result(result)
            self.top_status.setText(
                f"{result.get('ticker') or 'Strategy Lab'} cloud research complete · durable checkpoint restored"
            )
            self.refresh_jobs()
            QTimer.singleShot(200, self.refresh_results)
        except BaseException as exc:
            self.strategy_lab_job_id = ""
            self.strategy_lab.set_error(clean_error(exc))
            self.refresh_jobs()


__all__ = ["MainWindow", "clean_error", "write_metrics"]
