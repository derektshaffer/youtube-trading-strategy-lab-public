"""Production desktop window with bounded Research + ML status."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtWidgets import QPushButton

from .research_ml_page import ResearchMLPage
from .strategy_lab_window import MainWindow as StrategyLabMainWindow, clean_error, write_metrics


class MainWindow(StrategyLabMainWindow):
    def __init__(self, runtime: Any, *, smoke: bool = False, metrics_output: str = "") -> None:
        super().__init__(runtime, smoke=smoke, metrics_output=metrics_output)
        self.research_ml = ResearchMLPage()
        self.stack.addWidget(self.research_ml)
        self.research_ml.refresh_requested.connect(self.refresh_research_ml)
        self._install_research_ml_navigation()

    def _install_research_ml_navigation(self) -> None:
        page_index = self.stack.indexOf(self.research_ml)
        button = QPushButton("Research + ML")
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
                if widget is not None and widget.text() == "Results":
                    insert_at = index + 1
                    break
            if insert_at >= 0:
                layout.insertWidget(insert_at, button)
            else:
                layout.addWidget(button)
        self.nav_buttons.append(button)

    def refresh_research_ml(self) -> None:
        if self.active_job_id:
            self.research_ml.set_error(
                "Another foreground local read/analysis is active. Cloud research continues independently."
            )
            return
        self.research_ml.set_working(
            "Reading autonomous research + ML state",
            "Loading bounded queue, research, source, and model summaries from durable storage.",
        )
        request = {
            "job_type": "library.research_ml_summary",
            "payload": {"limit": 30},
            "requested_target": "auto",
            "idempotency_key": f"desktop-research-ml-{time.time_ns()}",
            "engine_version": "desktop-research-ml-v1",
        }
        try:
            self.submit_job(request, "research_ml_summary")
        except BaseException as exc:
            self.research_ml.set_error(clean_error(exc))

    def poll_active_job(self) -> None:
        if self.active_purpose == "research_ml_summary":
            # Preserve background cloud reconciliation while this local library
            # read is active. The timestamp guards keep these at the normal ~1s
            # cadence rather than the foreground UI timer frequency.
            now = time.monotonic()
            if (
                self.strategy_lab_job_id
                and now - self._last_strategy_lab_poll_at >= self._background_cloud_poll_seconds
            ):
                self._last_strategy_lab_poll_at = now
                self._poll_strategy_lab()
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
            self._poll_research_ml_summary()
            return
        super().poll_active_job()

    def _poll_research_ml_summary(self) -> None:
        if not self.active_job_id:
            return
        try:
            job = self.runtime.request_json("GET", f"/v1/jobs/{self.active_job_id}")
            self.research_ml.set_working(
                str(job.get("stage") or "working").replace("_", " ").title(),
                "Preparing bounded autonomous research and predictive ML summaries.",
            )
            if not bool(job.get("terminal")):
                return
            if job.get("status") != "complete":
                message = (job.get("error") or {}).get("message") or str(job.get("status"))
                raise RuntimeError(message)
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            self.active_job_id = ""
            self.active_purpose = ""
            self.research_ml.render_summary(result)
            self.refresh_jobs()
            counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
            self.top_status.setText(
                "Research + ML ready · "
                f"{int(counts.get('active_cloud_jobs') or 0):,} active jobs · "
                f"{int(counts.get('ready_shadow_models') or 0):,} shadow-ready models"
            )
        except BaseException as exc:
            self.active_job_id = ""
            self.active_purpose = ""
            self.research_ml.set_error(clean_error(exc))
            self.refresh_jobs()


__all__ = ["MainWindow", "clean_error", "write_metrics"]
