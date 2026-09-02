"""Production desktop wrapper with secure first-run onboarding."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton

from hybrid_runtime.desktop_settings import (
    ALPACA_API_KEY_ACCOUNT,
    ALPACA_SECRET_KEY_ACCOUNT,
    DesktopSettings,
    DesktopSettingsError,
    load_desktop_settings,
    save_desktop_settings,
)
from hybrid_runtime.keychain import KeychainError, KeychainUnavailable, MacOSKeychain
from hybrid_runtime.onboarding import configuration_status

from .onboarding_page import OnboardingPage
from .system_health_window import MainWindow as SystemHealthMainWindow, clean_error, write_metrics


class MainWindow(SystemHealthMainWindow):
    def __init__(self, runtime: Any, *, smoke: bool = False, metrics_output: str = "") -> None:
        super().__init__(runtime, smoke=smoke, metrics_output=metrics_output)
        try:
            settings = load_desktop_settings(runtime.data_dir)
        except DesktopSettingsError:
            settings = DesktopSettings()
        try:
            configured = configuration_status(runtime.data_dir)
        except Exception:
            configured = {
                "library_configured": False,
                "cloud_configured": False,
                "market_configured": False,
                "full_configured": False,
            }
        self.onboarding = OnboardingPage(settings, configured)
        self.stack.addWidget(self.onboarding)
        self.onboarding.save_and_verify_requested.connect(self.save_and_verify_setup)
        self.onboarding.skip_requested.connect(self.skip_onboarding)
        self.onboarding.complete_requested.connect(self.complete_onboarding)
        self._install_onboarding_navigation()

    def _install_onboarding_navigation(self) -> None:
        page_index = self.stack.indexOf(self.onboarding)
        button = QPushButton("Setup")
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
                if widget is not None and getattr(widget, "text", lambda: "")() == "Connection Settings":
                    insert_at = index + 1
                    break
            if insert_at >= 0:
                layout.insertWidget(insert_at, button)
            else:
                layout.addWidget(button)
        # Keep nav_buttons ordered by stack index even when the visual button is
        # inserted next to Connection Settings.
        self.nav_buttons.append(button)

    def wait_for_health(self) -> None:
        # CI smoke fixtures intentionally exercise the complete Profit First
        # vertical slice without needing real Keychain credentials.
        if self.smoke:
            super().wait_for_health()
            return
        try:
            health = self.runtime.request_json("GET", "/health")
            if health.get("status") != "ok":
                raise RuntimeError("The local service health response was not ok")
            try:
                settings = load_desktop_settings(self.runtime.data_dir)
                configured = configuration_status(self.runtime.data_dir)
            except Exception as exc:
                settings = DesktopSettings()
                configured = {
                    "library_configured": False,
                    "cloud_configured": False,
                    "market_configured": False,
                    "full_configured": False,
                }
                self.onboarding.set_error("Setup settings need attention: " + clean_error(exc))
            self.onboarding.populate(settings, configured)
            if bool(configured.get("full_configured")):
                # Preserve the existing launch behavior for already-configured users.
                super().wait_for_health()
                return
            self.top_status.setText(
                "First-run setup · connect the library, cloud research, and market data"
            )
            self.show_page(self.stack.indexOf(self.onboarding))
            self.refresh_jobs()
        except BaseException as exc:
            if (
                time.perf_counter() - self.started < 45.0
                and self.runtime.process is not None
                and self.runtime.process.poll() is None
            ):
                QTimer.singleShot(120, self.wait_for_health)
            else:
                self.onboarding.set_error(clean_error(exc))

    def save_and_verify_setup(self, raw: dict[str, Any]) -> None:
        if self.active_job_id:
            self.onboarding.set_error(
                "Another foreground job is active. Cloud research continues independently."
            )
            return
        payload = dict(raw)
        github_token = str(payload.pop("_github_token", "") or "").strip()
        alpaca_key = str(payload.pop("_alpaca_api_key", "") or "").strip()
        alpaca_secret = str(payload.pop("_alpaca_secret_key", "") or "").strip()
        try:
            settings = DesktopSettings.from_mapping(payload)
            save_desktop_settings(settings, self.runtime.data_dir)
            keychain = MacOSKeychain()
            if github_token:
                keychain.set_secret(settings.keychain_account, github_token)
            if alpaca_key:
                keychain.set_secret(ALPACA_API_KEY_ACCOUNT, alpaca_key)
            if alpaca_secret:
                keychain.set_secret(ALPACA_SECRET_KEY_ACCOUNT, alpaca_secret)
            # Keep the older Connection Settings page synchronized. It remains a
            # convenient advanced library editor after first-run setup.
            self.connection.populate(settings)
            configured = configuration_status(self.runtime.data_dir)
            self.onboarding.populate(settings, configured)
            self.onboarding.set_verifying(
                "Verifying saved connections",
                "Checking the authoritative library, private GitHub/cloud access, and Alpaca market data.",
                0.02,
            )
            request = {
                "job_type": "system.onboarding_probe",
                "payload": {},
                "requested_target": "auto",
                "idempotency_key": f"desktop-onboarding-probe-{time.time_ns()}",
                "engine_version": "desktop-onboarding-v1",
            }
            self.submit_job(request, "onboarding_probe")
        except (
            DesktopSettingsError,
            KeychainError,
            KeychainUnavailable,
            OSError,
            ValueError,
        ) as exc:
            self.onboarding.set_error("Setup save failed: " + clean_error(exc))

    def poll_active_job(self) -> None:
        if self.active_purpose == "onboarding_probe":
            self._poll_onboarding_probe()
            return
        super().poll_active_job()

    def _poll_onboarding_probe(self) -> None:
        if not self.active_job_id:
            return
        try:
            # Long-running cloud jobs stay visible/reconciled while setup performs
            # its small local network probes.
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
            progress = float(job.get("progress") or 0.0)
            self.onboarding.set_verifying(
                str(job.get("stage") or "verifying").replace("_", " ").title(),
                "Verifying saved connections. No brokerage orders are submitted.",
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
            self.onboarding.render_probe(result)
            self.refresh_jobs()
            self.top_status.setText(
                "Setup verified · ready to start"
                if result.get("ready")
                else "Setup saved · review connection checks"
            )
        except BaseException as exc:
            self.active_job_id = ""
            self.active_purpose = ""
            self.onboarding.set_error(clean_error(exc))
            self.refresh_jobs()

    def complete_onboarding(self) -> None:
        self.top_status.setText("Setup verified · loading Profit First")
        self.show_page(0)
        QTimer.singleShot(50, self.refresh_profit_first)

    def skip_onboarding(self) -> None:
        self.top_status.setText(
            "Limited setup · unavailable connections will remain blocked until Setup is verified"
        )
        self.show_page(0)
        QTimer.singleShot(50, self.refresh_profit_first)

    def save_connection(self, raw_settings: dict[str, Any], token: str) -> None:
        # The legacy Connection Settings page predates the market-feed setting.
        # Preserve the feed selected in Setup instead of silently resetting it.
        try:
            current = load_desktop_settings(self.runtime.data_dir)
            merged = dict(raw_settings)
            merged["market_feed"] = current.market_feed
        except Exception:
            merged = dict(raw_settings)
        super().save_connection(merged, token)


__all__ = ["MainWindow", "clean_error", "write_metrics"]
