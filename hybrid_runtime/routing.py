"""Explainable local/cloud routing for the hybrid Trading Intelligence runtime."""

from __future__ import annotations

from .contracts import ExecutionTarget, JobKind, RouteDecision, WorkEstimate


LOCAL_CONFIGURATION_LIMIT = 2_000
LOCAL_ROW_LIMIT = 500_000
LOCAL_SYMBOL_LIMIT = 2
LOCAL_SOURCE_SIZE_LIMIT = 10 * 1024 * 1024


class RouteUnavailableError(RuntimeError):
    """Raised when an explicit or safety-required execution target is unavailable."""


def _target(value: ExecutionTarget | str) -> ExecutionTarget:
    try:
        return value if isinstance(value, ExecutionTarget) else ExecutionTarget(str(value))
    except ValueError as exc:
        raise ValueError(f"Unknown execution target: {value!r}") from exc


def _kind(value: JobKind | str) -> JobKind:
    try:
        return value if isinstance(value, JobKind) else JobKind(str(value))
    except ValueError as exc:
        raise ValueError(f"Unknown hybrid job kind: {value!r}") from exc


def _estimated_weight(estimate: WorkEstimate) -> int:
    return int(
        estimate.configuration_count
        + estimate.expected_rows // 500
        + estimate.symbol_count * 250
        + estimate.history_days * 10
        + estimate.source_size_bytes // 32_768
        + (50_000 if estimate.requires_continuation else 0)
        + (100_000 if estimate.requires_accelerator else 0)
    )


def _local_safe(kind: JobKind, estimate: WorkEstimate) -> bool:
    if kind in {JobKind.QUICK_ANALYSIS, JobKind.CHART_REFRESH}:
        return True
    if kind == JobKind.SMALL_BACKTEST:
        return (
            estimate.symbol_count <= LOCAL_SYMBOL_LIMIT
            and estimate.configuration_count <= LOCAL_CONFIGURATION_LIMIT
            and estimate.expected_rows <= LOCAL_ROW_LIMIT
            and not estimate.requires_accelerator
        )
    if kind == JobKind.SOURCE_INGESTION:
        return (
            estimate.source_size_bytes <= LOCAL_SOURCE_SIZE_LIMIT
            and not estimate.requires_continuation
            and not estimate.requires_accelerator
        )
    return False


def resolve_execution_target(
    kind: JobKind | str,
    estimate: WorkEstimate | dict | None = None,
    *,
    requested: ExecutionTarget | str = ExecutionTarget.AUTO,
    cloud_available: bool = True,
    local_available: bool = True,
) -> RouteDecision:
    """Resolve execution without silently overriding an explicit user choice.

    Automatic routing keeps quick interactions local and sends long-running,
    interruption-sensitive research to a durable cloud worker.  If a required
    target is unavailable, the call fails clearly rather than pretending the job
    was launched somewhere else.
    """

    resolved_kind = _kind(kind)
    work = (
        estimate
        if isinstance(estimate, WorkEstimate)
        else WorkEstimate.from_mapping(estimate)
    )
    requested_target = _target(requested)
    weight = _estimated_weight(work)

    if requested_target == ExecutionTarget.LOCAL:
        if not local_available:
            raise RouteUnavailableError("Local execution was requested but is unavailable.")
        return RouteDecision(
            requested=requested_target,
            resolved=ExecutionTarget.LOCAL,
            reason="Local execution was selected explicitly.",
            automatic=False,
            cloud_available=cloud_available,
            local_available=local_available,
            estimated_weight=weight,
        )

    if requested_target == ExecutionTarget.CLOUD:
        if not cloud_available:
            raise RouteUnavailableError("Cloud execution was requested but is unavailable.")
        return RouteDecision(
            requested=requested_target,
            resolved=ExecutionTarget.CLOUD,
            reason="Cloud execution was selected explicitly.",
            automatic=False,
            cloud_available=cloud_available,
            local_available=local_available,
            estimated_weight=weight,
        )

    local_safe = _local_safe(resolved_kind, work)
    if local_safe and local_available:
        reason = {
            JobKind.QUICK_ANALYSIS: "Quick analysis stays local for immediate feedback.",
            JobKind.CHART_REFRESH: "Chart refresh stays local so the interface remains responsive.",
            JobKind.SMALL_BACKTEST: "This bounded backtest fits the local safety limits.",
            JobKind.SOURCE_INGESTION: "This source is small enough for uninterrupted local ingestion.",
        }.get(resolved_kind, "This bounded job fits the local safety limits.")
        return RouteDecision(
            requested=requested_target,
            resolved=ExecutionTarget.LOCAL,
            reason=reason,
            automatic=True,
            cloud_available=cloud_available,
            local_available=local_available,
            estimated_weight=weight,
        )

    if cloud_available:
        reasons = {
            JobKind.VERY_DEEP_SEARCH: "Very Deep search uses cloud execution so it survives app closure.",
            JobKind.LARGE_OPTIMIZATION: "Large optimization is interruption-sensitive and belongs on a durable worker.",
            JobKind.ADAPTIVE_WALK_FORWARD: "Adaptive walk-forward validation uses cloud execution to preserve every fold and checkpoint.",
            JobKind.CROSS_STOCK_RESEARCH: "Cross-stock research exceeds the bounded local interactive workload.",
            JobKind.PREDICTIVE_ML_BACKFILL: "ML backfill uses cloud execution for durable compute and checkpointing.",
            JobKind.SOURCE_INGESTION: "The source is large or resumable, so cloud execution protects progress.",
            JobKind.SMALL_BACKTEST: "The requested backtest exceeds the local safety limits.",
        }
        return RouteDecision(
            requested=requested_target,
            resolved=ExecutionTarget.CLOUD,
            reason=reasons.get(
                resolved_kind,
                "The workload exceeds the bounded local interactive limits.",
            ),
            automatic=True,
            cloud_available=cloud_available,
            local_available=local_available,
            estimated_weight=weight,
        )

    if local_available and local_safe:
        return RouteDecision(
            requested=requested_target,
            resolved=ExecutionTarget.LOCAL,
            reason="Cloud is unavailable, but this job remains within local safety limits.",
            automatic=True,
            cloud_available=cloud_available,
            local_available=local_available,
            estimated_weight=weight,
        )

    raise RouteUnavailableError(
        "This job requires durable cloud execution, but no cloud worker is available. "
        "It was not started locally because doing so could freeze or lose the search."
    )
