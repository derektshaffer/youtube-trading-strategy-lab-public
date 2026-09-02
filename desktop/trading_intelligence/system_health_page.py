"""Native diagnostic surface for desktop runtime, connections, caches, and jobs."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .pages import Card, MetricCard


def _bytes(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if number < 1024 or unit == "GB":
            return f"{number:.1f} {unit}"
        number /= 1024
    return "—"


class SystemHealthPage(QWidget):
    refresh_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        header = QHBoxLayout()
        copy = QVBoxLayout()
        eyebrow = QLabel("SYSTEM HEALTH")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Connections, caches, and durable job health")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "A read-only diagnostic view. It checks configuration and durable state without placing trades or launching research."
        )
        subtitle.setObjectName("Subtle")
        subtitle.setWordWrap(True)
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        self.refresh = QPushButton("Refresh Health")
        self.refresh.setObjectName("Primary")
        self.refresh.clicked.connect(self.refresh_requested.emit)
        header.addLayout(copy, 1)
        header.addWidget(self.refresh)
        root.addLayout(header)

        self.banner = Card()
        banner = QVBoxLayout(self.banner)
        self.status = QLabel("Health status has not been checked yet.")
        self.status.setObjectName("BannerTitle")
        self.detail = QLabel("Refresh to verify the local runtime and current private-library connection.")
        self.detail.setObjectName("Subtle")
        self.detail.setWordWrap(True)
        banner.addWidget(self.status)
        banner.addWidget(self.detail)
        root.addWidget(self.banner)

        metrics = QGridLayout()
        self.required = MetricCard("Required checks")
        self.active_jobs = MetricCard("Active jobs")
        self.failed_jobs = MetricCard("Recent failed jobs")
        self.cache_size = MetricCard("Market cache")
        for index, widget in enumerate((self.required, self.active_jobs, self.failed_jobs, self.cache_size)):
            metrics.addWidget(widget, 0, index)
        root.addLayout(metrics)

        self.checks = QTableWidget(0, 3)
        self.checks.setHorizontalHeaderLabels(["Check", "Status", "Detail"])
        self.checks.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.checks.verticalHeader().setVisible(False)
        self.checks.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.checks.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        checks_card = Card()
        checks_layout = QVBoxLayout(checks_card)
        checks_layout.addWidget(self.checks)
        root.addWidget(checks_card)

        self.failures = QTableWidget(0, 4)
        self.failures.setHorizontalHeaderLabels(["Updated", "Job", "Stage", "Message"])
        self.failures.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.failures.verticalHeader().setVisible(False)
        self.failures.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.failures.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        failures_card = Card()
        failures_layout = QVBoxLayout(failures_card)
        failures_title = QLabel("Recent durable failures")
        failures_title.setObjectName("BannerTitle")
        failures_layout.addWidget(failures_title)
        failures_layout.addWidget(self.failures)
        root.addWidget(failures_card, 1)

    def set_working(self, detail: str) -> None:
        self.refresh.setEnabled(False)
        self.banner.setProperty("state", "working")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText("Checking system health…")
        self.detail.setText(detail)

    def set_error(self, message: str) -> None:
        self.refresh.setEnabled(True)
        self.banner.setProperty("state", "error")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText("System health needs attention")
        self.detail.setText(message)

    @staticmethod
    def _item(value: Any) -> QTableWidgetItem:
        return QTableWidgetItem(str(value))

    def render_health(self, result: dict[str, Any]) -> None:
        self.refresh.setEnabled(True)
        ready = result.get("status") == "ready"
        self.banner.setProperty("state", "ready" if ready else "error")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText("System health ready" if ready else "System health needs attention")
        self.detail.setText(
            "Required local/cloud configuration is available."
            if ready
            else "One or more required connections or credentials are missing/unreadable. No trading state was changed."
        )

        checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
        required_names = (
            "runtime_storage",
            "github_library_configured",
            "github_library_credential",
            "library_readable",
            "alpaca_credentials",
        )
        passed = sum(1 for name in required_names if checks.get(name))
        jobs = result.get("jobs") if isinstance(result.get("jobs"), dict) else {}
        failures = jobs.get("recent_failures") if isinstance(jobs.get("recent_failures"), list) else []
        storage = result.get("storage") if isinstance(result.get("storage"), dict) else {}
        market = storage.get("market") if isinstance(storage.get("market"), dict) else {}
        self.required.value.setText(f"{passed}/{len(required_names)}")
        self.active_jobs.value.setText(f"{int(jobs.get('active') or 0):,}")
        self.failed_jobs.value.setText(f"{len(failures):,}")
        self.cache_size.value.setText(_bytes(market.get("bytes")))

        library = result.get("library") if isinstance(result.get("library"), dict) else {}
        connection = result.get("connection") if isinstance(result.get("connection"), dict) else {}
        details = {
            "runtime_storage": "Local durable SQLite job database",
            "github_library_configured": f"{connection.get('github_repository') or 'No repository'} · {connection.get('github_path') or 'No path'}",
            "github_library_credential": "GitHub token present in macOS Keychain" if checks.get("github_library_credential") else "GitHub token missing from macOS Keychain",
            "library_readable": f"Library source: {library.get('source') or 'unverified'}",
            "alpaca_credentials": f"Alpaca credentials present · feed {connection.get('market_feed') or 'unknown'}" if checks.get("alpaca_credentials") else "Alpaca API key/secret missing from macOS Keychain",
            "market_cache_present": f"{int(market.get('files') or 0):,} cached files · {_bytes(market.get('bytes'))}",
        }
        labels = {
            "runtime_storage": "Durable job storage",
            "github_library_configured": "Private library configured",
            "github_library_credential": "Private library credential",
            "library_readable": "Private library readable",
            "alpaca_credentials": "Alpaca market data credentials",
            "market_cache_present": "Persistent market cache",
        }
        rows = list(labels)
        self.checks.setRowCount(len(rows))
        for row, name in enumerate(rows):
            self.checks.setItem(row, 0, self._item(labels[name]))
            self.checks.setItem(row, 1, self._item("OK" if checks.get(name) else "Attention"))
            self.checks.setItem(row, 2, self._item(details.get(name, "")))

        self.failures.setRowCount(len(failures))
        for row, failure in enumerate(failures):
            self.failures.setItem(row, 0, self._item(failure.get("updated_at") or "—"))
            self.failures.setItem(row, 1, self._item(failure.get("job_type") or "—"))
            self.failures.setItem(row, 2, self._item(failure.get("stage") or "—"))
            self.failures.setItem(row, 3, self._item(failure.get("message") or "—"))
