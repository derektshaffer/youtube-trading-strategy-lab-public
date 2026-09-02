"""Production window extensions for real cached market analysis."""

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
    """Add quick market analysis without rewriting the stable Profit First shell."""

    def __init__(self, runtime: Any, *, smoke: bool = False, metrics_output: str = "") -> None:
        super().__init__(runtime, smoke=smoke, metrics_output=metrics_output)
        self.analysis = AnalysisPage()
        self.stack.addWidget(self.analysis)
        self.analysis.analyze_requested.connect(self.run_stock_analysis)
        self._install_analysis_navigation()
        self._install_market_connection_fields()

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

    def poll_active_job(self) -> None:
        if not self.active_job_id or self.active_purpose != "stock_analysis":
            super().poll_active_job()
            return
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


__all__ = ["MainWindow", "clean_error", "write_metrics"]
