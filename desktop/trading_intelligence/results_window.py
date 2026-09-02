"""Production desktop window with bounded authoritative Results summaries."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtWidgets import QPushButton

from .finder_window import MainWindow as FinderMainWindow, clean_error, write_metrics
from .results_page import ResultsPage


class MainWindow(FinderMainWindow):
    def __init__(self, runtime: Any, *, smoke: bool = False, metrics_output: str = "") -> None:
        super().__init__(runtime, smoke=smoke, metrics_output=metrics_output)
        self.results = ResultsPage()
        self.stack.addWidget(self.results)
        self.results.refresh_requested.connect(self.refresh_results)
        self._install_results_navigation()

    def _install_results_navigation(self) -> None:
        page_index = self.stack.indexOf(self.results)
        button = QPushButton("Results")
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

    def refresh_results(self) -> None:
        if self.active_job_id:
            self.results.set_error(
                "Another foreground local job is active. Cloud Finder/validation jobs may continue in the background."
            )
            return
        self.results.set_working(
            "Reading durable results",
            "Loading bounded summaries from the authoritative research library without copying full optimization payloads.",
        )
        request = {
            "job_type": "library.results_summary",
            "payload": {"limit": 30},
            "requested_target": "auto",
            "idempotency_key": f"desktop-results-{time.time_ns()}",
            "engine_version": "desktop-results-v1",
        }
        try:
            self.submit_job(request, "results_summary")
        except BaseException as exc:
            self.results.set_error(clean_error(exc))

    def poll_active_job(self) -> None:
        # Keep the existing cloud Finder/validation reconciliation alive while
        # the local Results summary is being prepared.
        now = time.monotonic()
        if (
            self.finder_job_id
            and now - self._last_finder_poll_at >= self._background_cloud_poll_seconds
        ):
            self._last_finder_poll_at = now
            self._poll_stock_finder()
        if (
            self.profit_validation_job_id
            and now - self._last_profit_validation_poll_at >= self._background_cloud_poll_seconds
        ):
            self._last_profit_validation_poll_at = now
            self._poll_background_profit_validation()

        if self.active_purpose == "results_summary":
            self._poll_results_summary()
            return
        super().poll_active_job()

    def _poll_results_summary(self) -> None:
        if not self.active_job_id:
            return
        try:
            job = self.runtime.request_json("GET", f"/v1/jobs/{self.active_job_id}")
            self.results.set_working(
                str(job.get("stage") or "working").replace("_", " ").title(),
                "Preparing bounded result summaries from durable storage.",
            )
            if not bool(job.get("terminal")):
                return
            if job.get("status") != "complete":
                message = (job.get("error") or {}).get("message") or str(job.get("status"))
                raise RuntimeError(message)
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            self.active_job_id = ""
            self.active_purpose = ""
            self.results.render_results(result)
            self.refresh_jobs()
            counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
            self.top_status.setText(
                "Results ready · "
                f"{int(counts.get('finder_runs') or 0):,} Finder · "
                f"{int(counts.get('validation_runs') or 0):,} validation · "
                f"{int(counts.get('strategy_lab_runs') or 0):,} Strategy Lab"
            )
        except BaseException as exc:
            self.active_job_id = ""
            self.active_purpose = ""
            self.results.set_error(clean_error(exc))
            self.refresh_jobs()


__all__ = ["MainWindow", "clean_error", "write_metrics"]
