"""Deterministic local/cloud routing with visible, testable reasons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import ExecutionTarget, JobRequest


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    target: ExecutionTarget
    reason: str
    automatic: bool
    heavy_signals: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target.value,
            "reason": self.reason,
            "automatic": self.automatic,
            "heavy_signals": list(self.heavy_signals),
        }


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    local_max_estimated_seconds: float = 20.0
    local_max_configurations: int = 2_000
    local_max_symbols: int = 3
    local_max_history_days: int = 60
    local_max_memory_mb: int = 1_024
    cloud_only_job_types: frozenset[str] = frozenset(
        {
            "strategy.very_deep",
            "strategy.profit_first_validation",
            "strategy.stock_finder",
            "strategy.strategy_lab",
            "research.autonomous",
            "research.book_extract",
            "research.video_extract",
            "ml.backfill",
            "ml.train",
        }
    )
    local_preferred_job_types: frozenset[str] = frozenset(
        {
            "system.health",
            "cache.inspect",
            "chart.refresh",
            "chart.framework_fixture",
            "analysis.stock",
            "backtest.quick",
            "library.configuration",
            "library.summary",
            "library.results_summary",
            "library.strategy_lab_options",
            "library.research_ml_summary",
            "strategy.profit_first_plan",
        }
    )

    @staticmethod
    def _number(payload: Mapping[str, Any], name: str, default: float = 0.0) -> float:
        try:
            return float(payload.get(name, default) or default)
        except (TypeError, ValueError, OverflowError):
            return default

    def _heavy_signals(self, request: JobRequest) -> tuple[str, ...]:
        payload = request.payload
        signals: list[str] = []
        estimated = self._number(payload, "estimated_seconds")
        configurations = int(self._number(payload, "configurations"))
        symbols = int(self._number(payload, "symbols_count"))
        history_days = int(self._number(payload, "history_days"))
        memory_mb = int(self._number(payload, "estimated_memory_mb"))
        if estimated > self.local_max_estimated_seconds:
            signals.append(f"estimated runtime {estimated:g}s")
        if configurations > self.local_max_configurations:
            signals.append(f"{configurations:,} configurations")
        if symbols > self.local_max_symbols:
            signals.append(f"{symbols} symbols")
        if history_days > self.local_max_history_days:
            signals.append(f"{history_days} history days")
        if memory_mb > self.local_max_memory_mb:
            signals.append(f"estimated memory {memory_mb:,} MB")
        if bool(payload.get("continue_after_app_exit")):
            signals.append("must continue after app exit")
        return tuple(signals)

    def decide(self, request: JobRequest) -> RoutingDecision:
        heavy = self._heavy_signals(request)
        if request.job_type in self.cloud_only_job_types:
            return RoutingDecision(
                target=ExecutionTarget.CLOUD,
                reason=f"{request.job_type} is a persistent cloud workload",
                automatic=request.requested_target == ExecutionTarget.AUTO,
                heavy_signals=heavy,
            )
        if request.requested_target == ExecutionTarget.LOCAL:
            return RoutingDecision(
                target=ExecutionTarget.LOCAL,
                reason="Local execution was explicitly selected",
                automatic=False,
                heavy_signals=heavy,
            )
        if request.requested_target == ExecutionTarget.CLOUD:
            return RoutingDecision(
                target=ExecutionTarget.CLOUD,
                reason="Cloud execution was explicitly selected",
                automatic=False,
                heavy_signals=heavy,
            )
        if heavy:
            return RoutingDecision(
                target=ExecutionTarget.CLOUD,
                reason="Cloud selected automatically because " + ", ".join(heavy),
                automatic=True,
                heavy_signals=heavy,
            )
        if request.job_type in self.local_preferred_job_types:
            return RoutingDecision(
                target=ExecutionTarget.LOCAL,
                reason=f"{request.job_type} is optimized for immediate local response",
                automatic=True,
            )
        return RoutingDecision(
            target=ExecutionTarget.LOCAL,
            reason="Local selected because no heavy-work signal was present",
            automatic=True,
        )
