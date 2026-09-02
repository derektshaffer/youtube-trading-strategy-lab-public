"""Deterministic local/cloud routing with a user-visible explanation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import ExecutionTarget, JobRequest


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    target: ExecutionTarget
    reason: str
    automatic: bool
    estimated_work_units: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target.value,
            "reason": self.reason,
            "automatic": self.automatic,
            "estimated_work_units": self.estimated_work_units,
        }


class RoutingPolicy:
    """Choose an execution target without silently weakening validation.

    The initial policy is intentionally conservative. Heavy research remains
    cloud-bound even before a cloud adapter is connected, so the local Mac never
    becomes an accidental substitute for strict long-running validation.
    """

    def __init__(self, *, local_work_limit: int = 4_000) -> None:
        self.local_work_limit = max(1, int(local_work_limit))
        self.local_job_types = {
            "system.health",
            "analysis.stock",
            "chart.framework_fixture",
            "library.configuration",
            "library.summary",
            "strategy.profit_first_plan",
            "strategy.quick_backtest",
        }
        self.cloud_only_job_types = {
            "strategy.profit_first_validation",
            "strategy.stock_finder",
            "strategy.very_deep",
            "strategy.adaptive_validation",
            "research.autonomous",
            "research.cross_stock",
            "research.book_video_extraction",
            "ml.train",
            "ml.backfill",
            "ml.large_replay",
        }

    @staticmethod
    def estimate_work(request: JobRequest) -> int:
        payload = dict(request.payload)
        for name in (
            "estimated_work_units",
            "estimated_configurations",
            "configurations",
            "variants",
        ):
            if payload.get(name) is not None:
                try:
                    return max(0, int(payload[name]))
                except (TypeError, ValueError, OverflowError):
                    break
        stocks = max(1, int(payload.get("stock_count") or len(payload.get("symbols") or []) or 1))
        strategies = max(
            1,
            int(
                payload.get("strategy_count")
                or len(payload.get("strategy_ids") or [])
                or 1
            ),
        )
        folds = max(1, int(payload.get("folds") or 1))
        variants = max(1, int(payload.get("variants_per_strategy") or 1))
        return stocks * strategies * folds * variants

    def decide(self, request: JobRequest | Mapping[str, Any]) -> RoutingDecision:
        normalized = (
            request
            if isinstance(request, JobRequest)
            else JobRequest.from_mapping(request)
        )
        work = self.estimate_work(normalized)
        if normalized.requested_target != ExecutionTarget.AUTO:
            requested = normalized.requested_target
            # A caller cannot force a known heavy/strict validation job onto the
            # local Mac. The UI may expose an advanced override for safe local
            # work, but validation fidelity is not an overrideable preference.
            if (
                requested == ExecutionTarget.LOCAL
                and normalized.job_type in self.cloud_only_job_types
            ):
                return RoutingDecision(
                    target=ExecutionTarget.CLOUD,
                    reason=(
                        "Strict or long-running research is cloud-only so it can "
                        "survive app exit and keep the validation boundary intact."
                    ),
                    automatic=True,
                    estimated_work_units=work,
                )
            return RoutingDecision(
                target=requested,
                reason=f"You explicitly selected {requested.value} execution.",
                automatic=False,
                estimated_work_units=work,
            )

        if normalized.job_type in self.cloud_only_job_types:
            return RoutingDecision(
                target=ExecutionTarget.CLOUD,
                reason=(
                    "This job is long-running or part of strict research/ML validation, "
                    "so cloud execution keeps it alive after the desktop app closes."
                ),
                automatic=True,
                estimated_work_units=work,
            )
        if normalized.job_type in self.local_job_types and work <= self.local_work_limit:
            return RoutingDecision(
                target=ExecutionTarget.LOCAL,
                reason="This is a bounded interactive job, so local execution minimizes latency.",
                automatic=True,
                estimated_work_units=work,
            )
        if work > self.local_work_limit:
            return RoutingDecision(
                target=ExecutionTarget.CLOUD,
                reason=(
                    f"Estimated work ({work:,} units) exceeds the local interactive limit "
                    f"({self.local_work_limit:,}); the UI remains responsive while cloud workers run it."
                ),
                automatic=True,
                estimated_work_units=work,
            )
        return RoutingDecision(
            target=ExecutionTarget.LOCAL,
            reason="The request is small enough to run locally without blocking the desktop experience.",
            automatic=True,
            estimated_work_units=work,
        )
