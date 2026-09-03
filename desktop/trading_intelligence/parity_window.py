"""Web-parity navigation and strategy-to-stock discovery for the desktop app."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QPushButton

from hybrid_runtime.scanner_launcher import (
    discover_scanner_target,
    normalize_scanner_target,
    save_scanner_target,
)

from .beta_recovery_window import MainWindow as RecoveryMainWindow, clean_error, write_metrics
from .market_discovery_page import MarketDiscoveryPage
from .scanner_launcher_page import ScannerLauncherPage


class MainWindow(RecoveryMainWindow):
    """Restore the web app's primary workflow without merging app engines."""

    def __init__(self, runtime: Any, *, smoke: bool = False, metrics_output: str = "") -> None:
        super().__init__(runtime, smoke=smoke, metrics_output=metrics_output)

        self.market_discovery = MarketDiscoveryPage()
        self.stack.addWidget(self.market_discovery)
        self.market_discovery.options_requested.connect(self.refresh_market_discovery_options)
        self.market_discovery.run_requested.connect(self.run_market_discovery)
        self.market_discovery.analyze_requested.connect(self.analyze_discovery_symbol)

        self.scanner_launcher = ScannerLauncherPage()
        self.stack.addWidget(self.scanner_launcher)
        self.scanner_launcher.open_requested.connect(self.open_momentum_scanner)
        self.scanner_launcher.choose_requested.connect(self.choose_momentum_scanner)
        self.scanner_launcher.set_target(discover_scanner_target(self.runtime.data_dir))

        self._rename_primary_workflow()
        self._install_parity_navigation()
        self._order_navigation_like_web_app()

    def _button(self, caption: str) -> QPushButton | None:
        return next((button for button in self.nav_buttons if button.text() == caption), None)

    def _rename_primary_workflow(self) -> None:
        home = self._button("Profit First")
        if home is not None:
            home.setText("Home")
            home.setToolTip("Strong strategy candidates, validation status, and the next useful action.")
        finder = self._button("Stock Strategy Finder")
        if finder is not None:
            finder.setText("Find & Test a Strategy")
            finder.setToolTip("Pick one stock, then find and test strategy candidates for it.")

    def _new_navigation_button(self, caption: str, page: Any, tooltip: str) -> QPushButton:
        index = self.stack.indexOf(page)
        button = QPushButton(caption)
        button.setCheckable(True)
        button.setProperty("stack_index", index)
        button.setToolTip(tooltip)
        button.clicked.connect(
            lambda _checked=False, selected=index: self.show_page(selected)
        )
        sidebar = self.nav_buttons[0].parentWidget()
        layout = sidebar.layout() if sidebar is not None else None
        if layout is not None:
            layout.addWidget(button)
        self.nav_buttons.append(button)
        return button

    def _install_parity_navigation(self) -> None:
        self._new_navigation_button(
            "Find Stocks",
            self.market_discovery,
            "Choose strategy rules, then scan the market for stocks that match them.",
        )
        self._new_navigation_button(
            "Open Momentum Scanner",
            self.scanner_launcher,
            "Launch the separately maintained Momentum Scanner and Stock Analyzer.",
        )

    def _order_navigation_like_web_app(self) -> None:
        sidebar = self.nav_buttons[0].parentWidget()
        layout = sidebar.layout() if sidebar is not None else None
        if layout is None:
            return
        order = (
            "Home",
            "Find & Test a Strategy",
            "Find Stocks",
            "Quick Analysis",
            "Strategy Lab",
            "Results",
            "Research + ML",
            "Open Momentum Scanner",
            "Durable Jobs",
            "System Health",
            "Connection Settings",
            "Setup",
        )
        by_caption = {button.text(): button for button in self.nav_buttons}
        for button in self.nav_buttons:
            layout.removeWidget(button)
        # Brand, subtitle, and spacing occupy the first three layout items.
        for offset, caption in enumerate(order):
            button = by_caption.get(caption)
            if button is not None:
                layout.insertWidget(3 + offset, button)
        known = set(order)
        insert_at = 3 + len(order)
        for button in self.nav_buttons:
            if button.text() not in known:
                layout.insertWidget(insert_at, button)
                insert_at += 1

    def show_page(self, index: int) -> None:
        super().show_page(index)
        if (
            hasattr(self, "market_discovery")
            and index == self.stack.indexOf(self.market_discovery)
            and not self.market_discovery.options_loaded
            and not self.active_job_id
        ):
            QTimer.singleShot(50, self.refresh_market_discovery_options)

    def refresh_market_discovery_options(self) -> None:
        if not self._require_capabilities(("library",), "Find Stocks"):
            return
        if self.active_job_id:
            self.market_discovery.set_error(
                "Another foreground task is active. Cloud research continues independently; try Refresh strategies again shortly."
            )
            return
        self.market_discovery.set_working(
            "Loading faithful strategies",
            "Applying the same source-to-backtester integrity gate used by the web app.",
            0.04,
        )
        request = {
            "job_type": "library.strategy_lab_options",
            "payload": {"limit": 500},
            "requested_target": "auto",
            "idempotency_key": f"desktop-market-discovery-options-{time.time_ns()}",
            "engine_version": "desktop-market-discovery-options-v1",
        }
        try:
            self.submit_job(request, "market_discovery_options")
        except BaseException as exc:
            self.market_discovery.set_error(clean_error(exc))

    def run_market_discovery(self, payload: dict[str, Any]) -> None:
        if not self._require_capabilities(("library", "market"), "Find Stocks"):
            return
        if self.active_job_id:
            self.market_discovery.set_error(
                "Another foreground task is active. Wait for it to finish, then run Find Stocks again."
            )
            return
        count = int(payload.get("candidate_count") or 50)
        self.market_discovery.set_working(
            "Building the live stock universe",
            "Preparing one shared market-data pass before comparing current conditions with strategy rules.",
            0.02,
        )
        request = {
            "job_type": "market.discovery",
            "payload": dict(payload),
            "requested_target": "auto",
            "idempotency_key": f"desktop-market-discovery-{time.time_ns()}",
            "engine_version": "desktop-market-discovery-v1",
        }
        try:
            self.submit_job(request, "market_discovery")
            self.top_status.setText(
                f"Find Stocks · scanning up to {count:,} current candidates in the local sidecar"
            )
        except BaseException as exc:
            self.market_discovery.set_error(clean_error(exc))

    def _poll_existing_cloud_work(self) -> None:
        now = time.monotonic()
        for job_attr, last_attr, callback_name in (
            ("strategy_lab_job_id", "_last_strategy_lab_poll_at", "_poll_strategy_lab"),
            ("finder_job_id", "_last_finder_poll_at", "_poll_stock_finder"),
            (
                "profit_validation_job_id",
                "_last_profit_validation_poll_at",
                "_poll_background_profit_validation",
            ),
        ):
            if not getattr(self, job_attr, ""):
                continue
            last = float(getattr(self, last_attr, 0.0) or 0.0)
            if now - last < float(self._background_cloud_poll_seconds):
                continue
            setattr(self, last_attr, now)
            getattr(self, callback_name)()

    def poll_active_job(self) -> None:
        if self.active_purpose in {"market_discovery_options", "market_discovery"}:
            self._poll_existing_cloud_work()
            self._poll_market_discovery_job()
            return
        super().poll_active_job()

    def _poll_market_discovery_job(self) -> None:
        if not self.active_job_id:
            return
        purpose = self.active_purpose
        try:
            job = self.runtime.request_json("GET", f"/v1/jobs/{self.active_job_id}")
            progress = float(job.get("progress") or 0.0)
            stage = str(job.get("stage") or "working").replace("_", " ").title()
            message = str(job.get("message") or job.get("status") or "working").replace("_", " ")
            self.market_discovery.set_working(stage, message, progress)
            if not bool(job.get("terminal")):
                return
            if job.get("status") != "complete":
                error = job.get("error") if isinstance(job.get("error"), dict) else {}
                raise RuntimeError(error.get("message") or str(job.get("status")))
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            self.active_job_id = ""
            self.active_purpose = ""
            if purpose == "market_discovery_options":
                self.market_discovery.render_options(result)
                self.top_status.setText(
                    f"Find Stocks ready · {int(result.get('faithful_count') or 0):,} faithful strategies"
                )
            else:
                self.market_discovery.render_results(result)
                self.top_status.setText(
                    f"Find Stocks complete · {int(result.get('match_count') or 0):,} strong matches · "
                    f"{int(result.get('validated_match_count') or 0):,} validated"
                )
            self.refresh_jobs()
        except BaseException as exc:
            self.active_job_id = ""
            self.active_purpose = ""
            self.market_discovery.set_error(clean_error(exc))
            self.refresh_jobs()

    def analyze_discovery_symbol(self, symbol: str) -> None:
        ticker = str(symbol or "").strip().upper()
        if not ticker:
            return
        self.analysis.symbol.setText(ticker)
        self.show_page(self.stack.indexOf(self.analysis))
        self.analysis.emit_analysis()

    def open_momentum_scanner(self, raw_target: str) -> None:
        try:
            target = str(raw_target or "").strip() or discover_scanner_target(
                self.runtime.data_dir
            )
            if not target:
                raise ValueError(
                    "Paste the scanner's Streamlit address or choose its local app once, then select Open Momentum Scanner."
                )
            target = save_scanner_target(self.runtime.data_dir, target)
            url = (
                QUrl(target)
                if target.startswith(("http://", "https://"))
                else QUrl.fromLocalFile(target)
            )
            if not QDesktopServices.openUrl(url):
                raise RuntimeError("macOS did not accept the saved scanner location.")
            self.scanner_launcher.set_opened(target)
        except (OSError, RuntimeError, ValueError) as exc:
            self.scanner_launcher.set_error(clean_error(exc))

    def choose_momentum_scanner(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose Momentum Scanner app or launcher",
            str(Path("/Applications")),
            "Applications and launchers (*.app *.command);;All files (*)",
        )
        if not selected:
            return
        try:
            target = normalize_scanner_target(selected)
            save_scanner_target(self.runtime.data_dir, target)
            self.scanner_launcher.set_target(target)
            self.open_momentum_scanner(target)
        except (OSError, ValueError) as exc:
            self.scanner_launcher.set_error(clean_error(exc))


__all__ = ["MainWindow", "clean_error", "write_metrics"]
