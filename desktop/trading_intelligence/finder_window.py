"""Production desktop window with background Stock Finder and cloud validation jobs."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton

from .enhanced_window import MainWindow as EnhancedMainWindow, clean_error, write_metrics
from .finder_page import StockFinderPage


class MainWindow(EnhancedMainWindow):
    """Keep cloud research independent from foreground local analysis/navigation."""

    def __init__(self, runtime: Any, *, smoke: bool = False, metrics_output: str = "") -> None:
        self.finder_job_id = ""
        self.profit_validation_job_id = ""
        self.finder_route: dict[str, Any] = {}
        self.profit_validation_route: dict[str, Any] = {}
        self._background_cloud_poll_seconds = 1.0
        self._last_finder_poll_at = 0.0
        self._last_profit_validation_poll_at = 0.0
        super().__init__(runtime, smoke=smoke, metrics_output=metrics_output)
        self.finder = StockFinderPage()
        self.stack.addWidget(self.finder)
        self.finder.run_requested.connect(self.run_stock_finder)
        self._normalize_navigation_targets()
        self._install_finder_navigation()
        QTimer.singleShot(300, self._restore_background_cloud_jobs)

    def _normalize_navigation_targets(self) -> None:
        # Base pages are stable: Profit First, Durable Jobs, Connection Settings.
        for index, button in enumerate(self.nav_buttons[:3]):
            button.setProperty("stack_index", index)
        analysis_index = self.stack.indexOf(self.analysis)
        for button in self.nav_buttons[3:]:
            if button.text() == "Quick Analysis":
                button.setProperty("stack_index", analysis_index)

    def _install_finder_navigation(self) -> None:
        finder_index = self.stack.indexOf(self.finder)
        button = QPushButton("Stock Strategy Finder")
        button.setCheckable(True)
        button.setProperty("stack_index", finder_index)
        button.clicked.connect(
            lambda _checked=False, selected=finder_index: self.show_page(selected)
        )
        sidebar = self.nav_buttons[0].parentWidget()
        layout = sidebar.layout() if sidebar is not None else None
        if layout is not None:
            insert_at = -1
            for index in range(layout.count()):
                widget = layout.itemAt(index).widget()
                if widget is not None and widget.text() == "Quick Analysis":
                    insert_at = index + 1
                    break
            if insert_at >= 0:
                layout.insertWidget(insert_at, button)
            else:
                layout.addWidget(button)
        self.nav_buttons.append(button)

    def show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for button in self.nav_buttons:
            target = button.property("stack_index")
            try:
                button.setChecked(int(target) == int(index))
            except (TypeError, ValueError):
                button.setChecked(False)

    def _recent_jobs(self) -> list[dict[str, Any]]:
        payload = self.runtime.request_json("GET", "/v1/jobs?limit=250")
        return [item for item in payload.get("jobs") or [] if isinstance(item, dict)]

    @staticmethod
    def _matching_active_job(
        jobs: list[dict[str, Any]],
        job_type: str,
        *,
        symbol: str = "",
        profile: str = "",
    ) -> dict[str, Any] | None:
        wanted_symbol = str(symbol or "").strip().upper()
        wanted_profile = str(profile or "").strip()
        for job in jobs:
            if bool(job.get("terminal")) or str(job.get("job_type") or "") != job_type:
                continue
            payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
            if wanted_symbol and str(payload.get("symbol") or "").strip().upper() != wanted_symbol:
                continue
            if wanted_profile and str(payload.get("profile") or "").strip() != wanted_profile:
                continue
            return job
        return None

    def _restore_background_cloud_jobs(self) -> None:
        try:
            jobs = self._recent_jobs()
        except BaseException:
            return
        finder_job = self._matching_active_job(jobs, "strategy.stock_finder")
        if finder_job is not None:
            self.finder_job_id = str(finder_job.get("id") or "")
            self._last_finder_poll_at = 0.0
            payload = finder_job.get("payload") if isinstance(finder_job.get("payload"), dict) else {}
            symbol = str(payload.get("symbol") or "").strip().upper()
            profile = str(payload.get("profile") or "Deep")
            self.finder.symbol.setText(symbol)
            index = self.finder.profile.findData(profile)
            if index >= 0:
                self.finder.profile.setCurrentIndex(index)
            self.finder.set_working(
                f"Reconnected to {symbol} Finder",
                "This cloud search kept running independently of the desktop app.",
                float(finder_job.get("progress") or 0.0),
            )
        validation_job = self._matching_active_job(
            jobs,
            "strategy.profit_first_validation",
        )
        if validation_job is not None:
            self.profit_validation_job_id = str(validation_job.get("id") or "")
            self._last_profit_validation_poll_at = 0.0
            self.profit_first.validation.setEnabled(False)
            self.profit_first.validation.setText("Validation running in cloud")

    def _submit_background_cloud_job(
        self,
        request: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        decision = self.runtime.request_json("POST", "/v1/route", request)
        submitted = self.runtime.request_json("POST", "/v1/jobs", request)
        job_id = str((submitted.get("job") or {}).get("id") or "")
        if not job_id:
            raise RuntimeError("Cloud job submission returned no durable job id")
        return job_id, decision

    def run_stock_finder(self, payload: dict[str, Any]) -> None:
        if self.finder_job_id:
            self.finder.set_error(
                "A Stock Finder cloud job is already attached here. Its progress is shown above and in Durable Jobs."
            )
            return
        symbol = str(payload.get("symbol") or "").strip().upper()
        profile = str(payload.get("profile") or "Deep").strip()
        try:
            jobs = self._recent_jobs()
            existing = self._matching_active_job(
                jobs,
                "strategy.stock_finder",
                symbol=symbol,
                profile=profile,
            )
            if existing is not None:
                self.finder_job_id = str(existing.get("id") or "")
                self._last_finder_poll_at = 0.0
                self.finder.set_working(
                    f"Attached to {symbol} · {profile}",
                    "The matching distributed cloud search was already active; no duplicate work was submitted.",
                    float(existing.get("progress") or 0.0),
                )
                return
            request = {
                "job_type": "strategy.stock_finder",
                "payload": {
                    "symbol": symbol,
                    "profile": profile,
                    "continue_after_app_exit": True,
                },
                "requested_target": "auto",
                "priority": 90 if profile == "Very Deep" else 75,
                # Fresh completed searches are intentionally repeatable; active
                # remote dedupe is handled by the authoritative stock_finder queue.
                "idempotency_key": f"desktop-stock-finder-{symbol}-{profile}-{time.time_ns()}",
                "engine_version": "desktop-stock-finder-v1",
            }
            self.finder_job_id, self.finder_route = self._submit_background_cloud_job(request)
            self._last_finder_poll_at = 0.0
            self.finder.set_working(
                f"Queueing {symbol} · {profile}",
                "The distributed Finder is being published to cloud workers. You can continue using Quick Analysis while it runs.",
                0.01,
            )
            self.refresh_jobs()
        except BaseException as exc:
            self.finder_job_id = ""
            self.finder.set_error(clean_error(exc))

    def run_profit_first_validation(self) -> None:
        if self.profit_validation_job_id:
            return
        queue_status = str(self.last_plan.get("queue_status") or "").strip()
        if queue_status not in {"ready", "active"}:
            self._sync_profit_first_validation_button()
            return
        dedupe = str(
            self.last_plan.get("dedupe_key")
            or self.last_plan.get("active_job_id")
            or self.last_plan.get("existing_job_id")
            or "current-batch"
        ).strip()
        try:
            jobs = self._recent_jobs()
            existing = self._matching_active_job(
                jobs,
                "strategy.profit_first_validation",
            )
            if existing is not None:
                self.profit_validation_job_id = str(existing.get("id") or "")
                self._last_profit_validation_poll_at = 0.0
                self.profit_first.set_working(
                    "Attached to strict cloud validation",
                    "The same validation job was already active; no duplicate work was submitted.",
                    float(existing.get("progress") or 0.0),
                )
                self._sync_profit_first_validation_button()
                return
            request = {
                "job_type": "strategy.profit_first_validation",
                "payload": {
                    "maximum_candidates": 3,
                    "remote_dedupe_key": dedupe if dedupe != "current-batch" else "",
                    "continue_after_app_exit": True,
                },
                "requested_target": "auto",
                "idempotency_key": f"desktop-profit-first-validation-{dedupe}",
                "engine_version": "desktop-cloud-bridge-v1",
            }
            self.profit_validation_job_id, self.profit_validation_route = (
                self._submit_background_cloud_job(request)
            )
            self._last_profit_validation_poll_at = 0.0
            self.profit_first.set_working(
                "Connecting to strict cloud validation",
                "Remote validation can continue while you use other desktop pages or close the app.",
                0.01,
            )
            self._sync_profit_first_validation_button()
            self.refresh_jobs()
        except BaseException as exc:
            self.profit_validation_job_id = ""
            self.profit_first.set_error(clean_error(exc))
            self._sync_profit_first_validation_button()

    def _sync_profit_first_validation_button(self) -> None:
        super()._sync_profit_first_validation_button()
        if self.profit_validation_job_id:
            self.profit_first.validation.setText("Validation running in cloud")
            self.profit_first.validation.setEnabled(False)

    def poll_active_job(self) -> None:
        # The base foreground timer is intentionally fast for local UI jobs. Long
        # cloud research changes much more slowly, so throttle those loopback API
        # reads to avoid needless work while keeping Quick Analysis responsive.
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
        super().poll_active_job()

    def _cloud_link(self, job_id: str) -> dict[str, Any]:
        try:
            payload = self.runtime.request_json("GET", f"/v1/jobs/{job_id}/cloud-link")
        except BaseException:
            return {}
        return payload.get("link") if isinstance(payload.get("link"), dict) else {}

    def _poll_stock_finder(self) -> None:
        job_id = self.finder_job_id
        try:
            job = self.runtime.request_json("GET", f"/v1/jobs/{job_id}")
            link = self._cloud_link(job_id)
            metadata = link.get("metadata") if isinstance(link.get("metadata"), dict) else {}
            progress = float(job.get("progress") or link.get("remote_progress") or 0.0)
            stage = str(job.get("stage") or link.get("remote_stage") or "cloud_queued").replace("_", " ")
            error = str(link.get("dispatch_error") or "").strip()
            if error:
                detail = "Cloud connection required: " + error
            else:
                detail = str(metadata.get("distributed_message") or "").strip()
                total = int(metadata.get("distributed_shards_total") or 0)
                completed = len(metadata.get("distributed_shards_completed") or [])
                if total:
                    shard_text = f"{completed} of {total} cloud shards complete"
                    detail = f"{detail} · {shard_text}" if detail else shard_text
                if not detail:
                    detail = "Waiting for the existing distributed Stock Finder workers."
            self.finder.set_working(
                f"Cloud Finder · {stage.title()}",
                detail,
                progress,
            )
            if not bool(job.get("terminal")):
                return
            self.finder_job_id = ""
            if job.get("status") != "complete":
                message = (job.get("error") or {}).get("message") or str(job.get("status"))
                raise RuntimeError(message)
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            self.finder.render_result(result)
            self.top_status.setText(
                f"{result.get('symbol') or 'Stock'} Finder complete · durable cloud result restored"
            )
            self.refresh_jobs()
        except BaseException as exc:
            self.finder_job_id = ""
            self.finder.set_error(clean_error(exc))
            self.refresh_jobs()

    def _poll_background_profit_validation(self) -> None:
        job_id = self.profit_validation_job_id
        try:
            job = self.runtime.request_json("GET", f"/v1/jobs/{job_id}")
            progress = float(job.get("progress") or 0.0)
            stage = str(job.get("stage") or "cloud_queued").replace("_", " ")
            detail = self._cloud_wait_detail(job_id) or (
                "Remote validation continues independently of local analysis and navigation."
            )
            self.profit_first.set_working(
                f"Strict cloud validation · {stage}",
                detail,
                progress,
            )
            if not bool(job.get("terminal")):
                return
            self.profit_validation_job_id = ""
            if job.get("status") != "complete":
                message = (job.get("error") or {}).get("message") or str(job.get("status"))
                raise RuntimeError(message)
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            outcome = str(result.get("outcome") or "cloud_validation_complete")
            self.top_status.setText(outcome.replace("_", " ").title())
            self.refresh_jobs()
            QTimer.singleShot(150, self.refresh_profit_first)
            self._sync_profit_first_validation_button()
        except BaseException as exc:
            self.profit_validation_job_id = ""
            self.profit_first.set_error(clean_error(exc))
            self.refresh_jobs()
            self._sync_profit_first_validation_button()

    def cancel_job(self, job_id: str) -> None:
        try:
            job = self.runtime.request_json("GET", f"/v1/jobs/{job_id}")
            if str(job.get("execution_target") or "") == "cloud":
                link = self._cloud_link(job_id)
                remote_status = str(link.get("remote_status") or "").strip().lower()
                if remote_status and remote_status not in {
                    "queued",
                    "pending",
                    "retry",
                    "retry_wait",
                }:
                    self.jobs.summary.setText(
                        "Cloud cancellation is available before the remote worker starts. "
                        "This job is already running remotely, so it remains attached rather than being left in a false cancelling state."
                    )
                    return
            super().cancel_job(job_id)
        except BaseException as exc:
            self.jobs.summary.setText("Cancellation failed: " + clean_error(exc))


__all__ = ["MainWindow", "clean_error", "write_metrics"]
