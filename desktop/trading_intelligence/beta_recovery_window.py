"""Production beta wrapper with capability-aware recovery before real work starts."""

from __future__ import annotations

import time
from typing import Any, Iterable

from hybrid_runtime.onboarding_state import (
    configuration_status,
    mark_setup_pending,
    mark_setup_probe_result,
)

from .onboarding_window import MainWindow as OnboardingMainWindow, clean_error, write_metrics


class MainWindow(OnboardingMainWindow):
    """Route recoverable configuration gaps back to Setup instead of failed jobs."""

    def _capability_snapshot(self) -> dict[str, Any]:
        if self.smoke:
            return {
                "capabilities": {"library": True, "cloud": True, "market": True},
                "launch_ready": True,
                "setup_verification": "verified",
            }
        return configuration_status(self.runtime.data_dir)

    def _require_capabilities(
        self,
        required: Iterable[str],
        feature: str,
    ) -> bool:
        try:
            status = self._capability_snapshot()
        except Exception as exc:
            self.onboarding.set_error(
                f"{feature} cannot verify the current setup state: {clean_error(exc)}"
            )
            self.show_page(self.stack.indexOf(self.onboarding))
            return False
        capabilities = (
            status.get("capabilities")
            if isinstance(status.get("capabilities"), dict)
            else {}
        )
        missing = [name for name in required if not bool(capabilities.get(name))]
        if not missing:
            return True
        labels = {
            "library": "research library",
            "cloud": "GitHub/cloud research",
            "market": "Alpaca market data",
        }
        missing_text = ", ".join(labels.get(name, name) for name in missing)
        verification = str(status.get("setup_verification") or "missing").replace("_", " ")
        self.onboarding.set_error(
            f"{feature} needs verified {missing_text}. Setup is currently {verification}. "
            "Correct the affected connection and choose Save securely + verify."
        )
        self.top_status.setText(f"{feature} blocked safely · setup verification required")
        self.show_page(self.stack.indexOf(self.onboarding))
        return False

    def refresh_profit_first(self) -> None:
        if self._require_capabilities(("library",), "Profit First"):
            super().refresh_profit_first()

    def refresh_results(self) -> None:
        if self._require_capabilities(("library",), "Results"):
            super().refresh_results()

    def refresh_research_ml(self) -> None:
        if self._require_capabilities(("library",), "Research + ML"):
            super().refresh_research_ml()

    def refresh_strategy_lab_options(self) -> None:
        if self._require_capabilities(("library",), "Strategy Lab"):
            super().refresh_strategy_lab_options()

    def run_stock_analysis(self, payload: dict[str, Any]) -> None:
        if self._require_capabilities(("market",), "Quick Analysis"):
            super().run_stock_analysis(payload)

    def run_stock_finder(self, payload: dict[str, Any]) -> None:
        if self._require_capabilities(("library", "cloud"), "Stock Strategy Finder"):
            super().run_stock_finder(payload)

    def run_strategy_lab(self, payload: dict[str, Any]) -> None:
        if self._require_capabilities(("library", "cloud"), "Strategy Lab"):
            super().run_strategy_lab(payload)

    def run_profit_first_validation(self) -> None:
        if self._require_capabilities(("library", "cloud"), "Strict cloud validation"):
            super().run_profit_first_validation()

    def _poll_onboarding_probe(self) -> None:
        """Persist partial capability success even when full first-run setup is incomplete."""

        if not self.active_job_id:
            return
        try:
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
                "Verifying saved connections. No brokerage orders or strategy research are submitted.",
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
            checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
            mark_setup_probe_result(self.runtime.data_dir, checks)
            refreshed_configuration = configuration_status(self.runtime.data_dir)
            result = dict(result)
            result["configuration"] = refreshed_configuration
            self.onboarding.render_probe(result)
            self.refresh_jobs()
            self.top_status.setText(
                "Setup verified · ready to start"
                if result.get("ready")
                else "Setup partially verified · available features remain usable"
            )
        except BaseException as exc:
            self.active_job_id = ""
            self.active_purpose = ""
            try:
                mark_setup_pending(self.runtime.data_dir)
            except Exception:
                pass
            self.onboarding.set_error(clean_error(exc))
            self.refresh_jobs()


__all__ = ["MainWindow", "clean_error", "write_metrics"]
