"""Hybrid desktop/runtime foundation for Trading Intelligence Lab.

The package is intentionally standard-library-only at import time. Optional
HTTP and desktop dependencies live in ``requirements-desktop.txt`` so the
existing Streamlit deployment remains unchanged during migration.
"""

from .contracts import ExecutionTarget, JobRequest, JobStatus
from .router import RoutingDecision, RoutingPolicy
from .service import HybridService
from .storage import HybridStore

__all__ = [
    "ExecutionTarget",
    "HybridService",
    "HybridStore",
    "JobRequest",
    "JobStatus",
    "RoutingDecision",
    "RoutingPolicy",
]
