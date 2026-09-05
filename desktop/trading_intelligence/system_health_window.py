"""Production desktop window with read-only System Health diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any

from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton

from hybrid_runtime.build_identity import read_build_identity
from hybrid_runtime.security import write_private_text_file
from hybrid_runtime.support_snapshot import build_support_snapshot, snapshot_json

from .research_ml_window import MainWindow as ResearchMLMainWindow, clean_error, write_metrics
from .system_health_page import SystemHealthPage


class MainWindow(ResearchMLMainWindow):
    def __init__(self, runtime: Any, *, smoke: bool = False, metrics_output: str = "") -> None:
        super().__init__(runtime, smoke=smoke, metrics_output=metrics_output)
        self.last_system_health_result: dict[str, Any] = {}
        self.system_health = SystemHealthPage()
        self.stack.addWidget(self.system_health)
        self.system_health.refresh_requested.connect(self.refresh_system_health)
        self.system_health.copy_snapshot_requested.connect(self.copy_support_snapshot)
        self.system_health.save_snapshot_requested.connect(self.save_support_snapshot)
        self._install_system_health_navigation()

    def _install_system_health_navigation(self) -> None:
        page_index = self.stack.indexOf(self.system_health)
        button = QPushButton("System Health")
        button.setCheckable(True)
        button.setProperty("stack_index", page_index)
        button.clicked.connect(
            lambda _checked=False, selected=page_index: self.show_page(selected)
        )
        sidebar = self.nav_buttons[0].parentWidget()
        layout = sidebar.layout() if sidebar is not None else None
        if layout is not None:
            layout.addWidget(button)
        self.nav_buttons.append(button)

    def refresh_system_health(self) -> None:
        if self.active_job_id:
            self.system_health.set_error(
                "Another foreground local read/analysis is active. Cloud jobs continue independently."
            )
            return
        self.system_health.set_working(
            "Reading the research-library connection and local durable state."
        )
        request = {
            "job_type": "library.summary",
            "payload": {},
            "requested_target": "auto",
            "idempotency_key": f"desktop-system-health-library-{time.time_ns()}",
            "engine_version": "desktop-system-health-v1",
        }
        try:
            self.submit_job(request, "system_health_library")
        except BaseException as exc:
            self.system_health.set_error(clean_error(exc))

    def poll_active_job(self) -> None:
        if self.active_purpose == "system_health_library":
            self._poll_system_health_library()
            return
        super().poll_active_job()

    def _poll_system_health_library(self) -> None:
        if not self.active_job_id:
            return
        try:
            # Keep existing cloud-job reconciliation alive during the diagnostic
            # library read, using the same bounded cadence as the other pages.
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

            job = self.runtime.request_json("GET", f"/v1/jobs/{self.active_job_id}")
            if not bool(job.get("terminal")):
                return

            if job.get("status") == "complete":
                library = job.get("result") if isinstance(job.get("result"), dict) else {}
            else:
                # A broken/missing library is precisely something System Health
                # must diagnose. Convert the failed library read into a bounded
                # diagnostic input instead of making the diagnostic page itself fail.
                message = (job.get("error") or {}).get("message") or str(job.get("status"))
                library = {
                    "source": "error",
                    "error": clean_error(RuntimeError(message)),
                    "cloud_refreshed": False,
                }

            runtime_health = self.runtime.request_json("GET", "/health")
            from hybrid_runtime.system_health_summary import build_system_health_summary

            result = dict(
                build_system_health_summary(
                    self.runtime.data_dir,
                    library_summary=library,
                    runtime_health=runtime_health,
                )
            )
            # Read this in the GUI process so it identifies the exact .app the
            # user launched, not merely the packaged Python sidecar.
            result["build"] = read_build_identity()
            self.active_job_id = ""
            self.active_purpose = ""
            self.last_system_health_result = dict(result)
            self.system_health.render_health(result)
            self.refresh_jobs()
            self.top_status.setText(
                "System Health · "
                + ("Ready" if result.get("status") == "ready" else "Attention needed")
            )
        except BaseException as exc:
            self.active_job_id = ""
            self.active_purpose = ""
            self.system_health.set_error(clean_error(exc))
            self.refresh_jobs()

    def _support_snapshot_text(self) -> str:
        if not self.last_system_health_result:
            raise RuntimeError("Refresh System Health before creating a support snapshot.")
        snapshot = build_support_snapshot(self.last_system_health_result)
        return snapshot_json(snapshot)

    def copy_support_snapshot(self) -> None:
        try:
            text = self._support_snapshot_text()
            clipboard = QApplication.clipboard()
            if clipboard is None:
                raise RuntimeError("The macOS clipboard is unavailable.")
            clipboard.setText(text)
            self.system_health.set_snapshot_status(
                f"Copied redacted support snapshot to the clipboard ({len(text.encode('utf-8')):,} bytes). Paste it directly into chat."
            )
        except BaseException as exc:
            self.system_health.set_snapshot_status(
                "Support snapshot copy failed: " + clean_error(exc)
            )

    def save_support_snapshot(self) -> None:
        try:
            text = self._support_snapshot_text()
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            desktop = Path.home() / "Desktop"
            base = desktop if desktop.is_dir() else Path.home()
            suggested = base / f"trading-intelligence-support-{stamp}.json"
            destination, _selected_filter = QFileDialog.getSaveFileName(
                self,
                "Save redacted Trading Intelligence support snapshot",
                str(suggested),
                "JSON files (*.json);;All files (*)",
            )
            if not destination:
                self.system_health.set_snapshot_status("Support snapshot save cancelled.")
                return
            path = write_private_text_file(destination, text)
            self.system_health.set_snapshot_status(
                f"Saved redacted support snapshot as {path.name} with owner-only permissions."
            )
        except BaseException as exc:
            self.system_health.set_snapshot_status(
                "Support snapshot save failed: " + clean_error(exc)
            )


__all__ = ["MainWindow", "clean_error", "write_metrics"]
