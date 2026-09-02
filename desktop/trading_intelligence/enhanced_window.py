"""Production window extensions for real cached market analysis and cloud validation."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)

from hybrid_runtime.desktop_settings import (
    ALPACA_API_KEY_ACCOUNT,
    ALPACA_SECRET_KEY_ACCOUNT,
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

from .analysis_page import AnalysisPage
from .pages import Card
from .window import MainWindow as BaseMainWindow, clean_error, write_metrics


class MainWindow(BaseMainWindow):
    """Add quick market analysis and the existing durable cloud validation bridge."""

    def __init__(self, runtime: Any, *, smoke: bool = False, metrics_output: str = "") -> None:
        super().__init__(runtime, smoke=smoke, metrics_output=metrics_output)
        self.analysis = AnalysisPage()
        self.stack.addWidget(self.analysis)
        self.analysis.analyze_requested.connect(self.run_stock_analysis)
        self._install_analysis_navigation()
        self._install_market_connection_fields()
        self._install_profit_first_cloud_validation()

    def _install_analysis_navigation(self) -> None:
        sidebar = self.nav_buttons[0].parentWidget()
        layout = sidebar.layout() if sidebar is not None else None
        button = QPushButton("Quick Analysis")
        button.setCheckable(True)
        page_index = self.stack.indexOf(self.analysis)
        button.clicked.connect(
            lambda _checked=False, selected=page_index: self.show_page(selected)
        )
        insert_at = -1
        if layout is not None:
            for index in range(layout.count()):
                if layout.itemAt(index).widget() is self.nav_buttons[0]:
                    insert_at = index + 1
                    break
            if insert_at >= 0:
                layout.insertWidget(insert_at, button)
            else:
                layout.addWidget(button)
        self.nav_buttons.append(button)

    def show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        selected_widget = self.stack.widget(index)
        for button_index, button in enumerate(self.nav_buttons):
            if button_index < 3:
                target = self.stack.widget(button_index)
            else:
                target = self.analysis
            button.setChecked(target is selected_widget)

    def _install_market_connection_fields(self) -> None:
        card = Card()
        form = QGridLayout(card)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)
        heading = QLabel("Real market data")
        heading.setObjectName("SectionTitle")
        description = QLabel(
            "Alpaca credentials stay in macOS Keychain. The feed choice is non-secret. "
            "Blank credential fields keep the existing saved values."
        )
        description.setObjectName("Subtle")
        description.setWordWrap(True)
        self.market_feed = QComboBox()
        self.market_feed.addItem("SIP consolidated feed", "sip")
        self.market_feed.addItem("IEX feed", "iex")
        self.alpaca_key = QLineEdit()
        self.alpaca_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.alpaca_key.setPlaceholderText("Leave blank to keep the existing Alpaca API key")
        self.alpaca_secret = QLineEdit()
        self.alpaca_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.alpaca_secret.setPlaceholderText("Leave blank to keep the existing Alpaca secret")
        form.addWidget(heading, 0, 0, 1, 2)
        form.addWidget(description, 1, 0, 1, 2)
        for row, (caption, widget) in enumerate(
            (
                ("Historical feed", self.market_feed),
                ("Alpaca API key", self.alpaca_key),
                ("Alpaca secret", self.alpaca_secret),
            ),
            start=2,
        ):
            label = QLabel(caption)
            label.setObjectName("FormLabel")
            form.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            form.addWidget(widget, row, 1)
        form.setColumnStretch(1, 1)

        try:
            settings = load_desktop_settings(self.runtime.data_dir)
        except DesktopSettingsError:
            settings = DesktopSettings()
        feed_index = self.market_feed.findData(settings.market_feed)
        self.market_feed.setCurrentIndex(max(0, feed_index))

        root = self.connection.layout()
        if root is not None:
            # ConnectionPage layout: headings, library card, controls, stretch.
            insert_at = max(0, root.count() - 2)
            root.insertWidget(insert_at, card)

    def _install_profit_first_cloud_validation(self) -> None:
        self.profit_first.validation.setText("Run strict cloud validation")
        self.profit_first.validation.setToolTip(
            "Runs the existing strict validator in the cloud. The remote job continues if this app closes."
        )
        self.profit_first.validation.clicked.connect(self.run_profit_first_validation)
        self._sync_profit_first_validation_button()

    def _sync_profit_first_validation_button(self) -> None:
        status = str(self.last_plan.get("queue_status") or "").strip()
        if status == "ready":
            self.profit_first.validation.setText("Run strict cloud validation")
            self.profit_first.validation.setEnabled(not bool(self.active_job_id))
            self.profit_first.next_detail.setText(
                "These candidates are ready. Strict validation runs in the existing cloud worker, "
                "uses the authoritative research library, and can continue after the desktop app closes."
            )
        elif status == "active":
            self.profit_first.validation.setText("Attach to active validation")
            self.profit_first.validation.setEnabled(not bool(self.active_job_id))
            self.profit_first.next_detail.setText(
                "A matching strict validation is already running. The desktop will attach to that exact "
                "cloud job instead of launching duplicate work."
            )
        elif status == "already-attempted":
            self.profit_first.validation.setText("Already attempted")
            self.profit_first.validation.setEnabled(False)
        elif status == "no-eligible-candidates":
            self.profit_first.validation.setText("No eligible candidates")
            self.profit_first.validation.setEnabled(False)
        else:
            self.profit_first.validation.setText("Strict cloud validation")
            self.profit_first.validation.setEnabled(False)

    def save_connection(self, raw_settings: dict[str, Any], token: str) -> None:
        try:
            payload = dict(raw_settings)
            payload["market_feed"] = str(self.market_feed.currentData() or "sip")
            settings = DesktopSettings.from_mapping(payload)
            save_desktop_settings(settings, self.runtime.data_dir)
            keychain = MacOSKeychain()
            if token:
                keychain.set_secret(settings.keychain_account, token)
            alpaca_key = self.alpaca_key.text().strip()
            alpaca_secret = self.alpaca_secret.text().strip()
            if alpaca_key:
                keychain.set_secret(ALPACA_API_KEY_ACCOUNT, alpaca_key)
            if alpaca_secret:
                keychain.set_secret(ALPACA_SECRET_KEY_ACCOUNT, alpaca_secret)
            self.connection.populate(settings)
            feed_index = self.market_feed.findData(settings.market_feed)
            self.market_feed.setCurrentIndex(max(0, feed_index))
            self.alpaca_key.clear()
            self.alpaca_secret.clear()
            self.connection.status.setText(
                "Saved securely. GitHub and Alpaca credentials are in macOS Keychain; "
                "non-secret settings use the owner-only settings file."
            )
        except (
            DesktopSettingsError,
            ValueError,
            KeychainError,
            KeychainUnavailable,
            OSError,
        ) as exc:
            self.connection.status.setText("Save failed: " + clean_error(exc))
            self.fail_smoke(exc)

    def run_stock_analysis(self, payload: dict[str, Any]) -> None:
        if self.active_job_id:
            self.analysis.set_error(
                "Another local job is active. The durable queue will be available here after it finishes."
            )
            return
        symbol = str(payload.get("symbol") or "").strip().upper()
        timeframe = str(payload.get("timeframe") or "5Min")
        self.analysis.set_working(
            f"Loading {symbol}",
            "Checking the persistent cache before requesting any additional Alpaca candles.",
            0.02,
        )
        request = {
            "job_type": "analysis.stock",
            "payload": dict(payload),
            "requested_target": "auto",
            "idempotency_key": f"desktop-analysis-{symbol}-{timeframe}-{time.time_ns()}",
            "engine_version": "desktop-market-cache-v1",
        }
        try:
            self.submit_job(request, "stock_analysis")
        except BaseException as exc:
            self.analysis.set_error(clean_error(exc))

    def run_profit_first_validation(self) -> None:
        if self.active_job_id:
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
        self.profit_first.set_working(
            "Connecting to strict cloud validation",
            "Publishing or attaching to the existing authoritative validation queue. Closing the app will not stop remote work.",
            0.01,
        )
        self.profit_first.validation.setEnabled(False)
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
        try:
            self.submit_job(request, "profit_first_validation")
        except BaseException as exc:
            self.profit_first.set_error(clean_error(exc))
            self._sync_profit_first_validation_button()

    def poll_active_job(self) -> None:
        if not self.active_job_id:
            return
        if self.active_purpose == "stock_analysis":
            self._poll_stock_analysis()
            return
        if self.active_purpose == "profit_first_validation":
            self._poll_profit_first_validation()
            return
        previous_purpose = self.active_purpose
        super().poll_active_job()
        if previous_purpose == "profit_first" and not self.active_job_id:
            self._sync_profit_first_validation_button()

    def _poll_stock_analysis(self) -> None:
        try:
            job = self.runtime.request_json("GET", f"/v1/jobs/{self.active_job_id}")
            progress = float(job.get("progress") or 0.0)
            self.analysis.set_working(
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
            self.analysis.render_analysis(result)
            self.refresh_jobs()
            cache = result.get("cache") if isinstance(result.get("cache"), dict) else {}
            self.top_status.setText(
                f"{result.get('symbol') or 'Stock'} analysis ready · "
                + ("persistent cache reused" if not cache.get("network_request") else "incremental cache refresh complete")
            )
        except BaseException as exc:
            self.active_job_id = ""
            self.active_purpose = ""
            self.analysis.set_error(clean_error(exc))
            self.refresh_jobs()

    def _cloud_wait_detail(self, job_id: str) -> str:
        try:
            payload = self.runtime.request_json("GET", f"/v1/jobs/{job_id}/cloud-link")
        except BaseException:
            return ""
        link = payload.get("link") if isinstance(payload.get("link"), dict) else {}
        error = str(link.get("dispatch_error") or "").strip()
        if not error:
            return ""
        metadata = link.get("metadata") if isinstance(link.get("metadata"), dict) else {}
        prefix = (
            "Cloud connection required: "
            if bool(metadata.get("waiting_for_connection"))
            else "Cloud queue note: "
        )
        return prefix + error

    def _poll_profit_first_validation(self) -> None:
        try:
            job_id = self.active_job_id
            job = self.runtime.request_json("GET", f"/v1/jobs/{job_id}")
            progress = float(job.get("progress") or 0.0)
            stage = str(job.get("stage") or "cloud_queued").replace("_", " ")
            detail = self._cloud_wait_detail(job_id)
            if not detail:
                detail = (
                    "Remote validation continues independently of this window. "
                    "Progress is reconciled into this durable desktop job."
                )
            self.profit_first.set_working(
                f"Strict cloud validation · {stage}",
                detail,
                progress,
            )
            if not bool(job.get("terminal")):
                return
            if job.get("status") != "complete":
                message = (job.get("error") or {}).get("message") or str(job.get("status"))
                raise RuntimeError(message)
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            outcome = str(result.get("outcome") or "cloud_validation_complete")
            self.active_job_id = ""
            self.active_purpose = ""
            self.refresh_jobs()
            self.top_status.setText(outcome.replace("_", " ").title())
            self.profit_first.banner_title.setText(outcome.replace("_", " ").title())
            self.profit_first.banner_detail.setText(
                "The authoritative cloud result is saved. Refreshing Profit First from the research library…"
            )
            self.profit_first.progress.setValue(1000)
            QTimer.singleShot(150, self.refresh_profit_first)
        except BaseException as exc:
            self.active_job_id = ""
            self.active_purpose = ""
            self.profit_first.set_error(clean_error(exc))
            self.refresh_jobs()
            self._sync_profit_first_validation_button()


__all__ = ["MainWindow", "clean_error", "write_metrics"]
