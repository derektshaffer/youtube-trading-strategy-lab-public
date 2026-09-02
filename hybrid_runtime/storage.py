"""Public SQLite store assembled from focused persistence components."""

from .storage_base import (
    HybridStoreError,
    InvalidJobTransition,
    JobNotFound,
    SCHEMA_VERSION,
    StorageBase,
)
from .storage_cache import CacheStoreMixin
from .storage_jobs import JobStoreMixin
from .storage_workers import WorkerStoreMixin


class HybridStore(JobStoreMixin, WorkerStoreMixin, CacheStoreMixin, StorageBase):
    """Durable job/event/cache store used by local and future cloud adapters."""


__all__ = [
    "HybridStore",
    "HybridStoreError",
    "InvalidJobTransition",
    "JobNotFound",
    "SCHEMA_VERSION",
]
