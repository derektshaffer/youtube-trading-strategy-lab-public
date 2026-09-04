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

from .error_sanitizer import sanitize_display_text
from .time_utils import format_local_timestamp
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
    copy_snapshot_requested = Signal()
    save_snapshot_requested = Signal()

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
            "A read-only diagnostic view. It verifies the authenticated local service, data connections, credentials, caches, setup verification, and recent durable failures without changing trading state."
        )
        subtitle.setObjectName("Subtle")
        subtitle.setWordWrap(True)
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        actions = QHBoxLayout()
        self.copy_snapshot = QPushButton("Copy Support Snapshot")
        self.copy_snapshot.setEnabled(False)
        self.copy_snapshot.setToolTip(
            "Copies a small redacted diagnostic JSON snapshot for pasting into support/chat."
        )
        self.copy_snapshot.clicked.connect(self.copy_snapshot_requested.emit)
        self.save_snapshot = QPushButton("Save Snapshot…")
        self.save_snapshot.setEnabled(False)
        self.save_snapshot.setToolTip(
            "Saves the same redacted diagnostic JSON with owner-only file permissions."
        )
        self.save_snapshot.clicked.connect(self.save_snapshot_requested.emit)
        self.refresh = QPushButton("Refresh Health")
        self.refresh.setObjectName("Primary")
        self.refresh.clicked.connect(self.refresh_requested.emit)
        actions.addWidget(self.copy_snapshot)
        actions.addWidget(self.save_snapshot)
        actions.addWidget(self.refresh)
        header.addLayout(copy, 1)
        header.addLayout(actions)
        root.addLayout(header)

        self.banner = Card()
        banner = QVBoxLayout(self.banner)
        self.status = QLabel("Health status has not been checked yet.")
        self.status.setObjectName("BannerTitle")
        self.detail = QLabel("Refresh to verify the local runtime and current research-library connection.")
        self.detail.setObjectName("Subtle")
        self.detail.setWordWrap(True)
        self.snapshot_status = QLabel(
            "Refresh Health before creating a support snapshot. Snapshots never include Keychain secret values or research/strategy payloads."
        )
        self.snapshot_status.setObjectName("Subtle")
        self.snapshot_status.setWordWrap(True)
        banner.addWidget(self.status)
        banner.addWidget(self.detail)
        banner.addWidget(self.snapshot_status)
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
        self.copy_snapshot.setEnabled(False)
        self.save_snapshot.setEnabled(False)
        self.banner.setProperty("state", "working")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText("Checking system health…")
        self.detail.setText(detail)
        self.snapshot_status.setText("Support snapshot actions will be available after this health refresh finishes.")

    def set_error(self, message: str) -> None:
        self.refresh.setEnabled(True)
        self.copy_snapshot.setEnabled(False)
        self.save_snapshot.setEnabled(False)
        self.banner.setProperty("state", "error")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText("System health needs attention")
        self.detail.setText(message)
        self.snapshot_status.setText("Refresh Health successfully before copying or saving a support snapshot.")

    def set_snapshot_status(self, message: str) -> None:
        self.snapshot_status.setText(str(message or ""))

    @staticmethod
    def _item(value: Any) -> QTableWidgetItem:
        return QTableWidgetItem(str(value))

    def render_health(self, result: dict[str, Any]) -> None:
        self.refresh.setEnabled(True)
        self.copy_snapshot.setEnabled(True)
        self.save_snapshot.setEnabled(True)
        self.snapshot_status.setText(
            "Redacted support snapshot ready. Copy it to paste into chat, or save an owner-only JSON file."
        )
        ready = result.get("status") == "ready"
        setup = result.get("setup") if isinstance(result.get("setup"), dict) else {}
        setup_verification = str(setup.get("verification") or "unavailable").replace("_", " ")
        setup_capabilities = (
            setup.get("capabilities")
            if isinstance(setup.get("capabilities"), dict)
            else {}
        )
        setup_pending = setup_verification == "pending"

        self.banner.setProperty("state", "ready" if ready else "error")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText("System health ready" if ready else "System health needs attention")
        if setup_pending:
            missing = [
                label
                for key, label in (
                    ("library", "library"),
                    ("cloud", "cloud"),
                    ("market", "market data"),
                )
                if not bool(setup_capabilities.get(key))
            ]
            self.detail.setText(
                "Setup verification is pending. "
                + ("Needs verification: " + ", ".join(missing) + ". " if missing else "")
                + "Open Setup, correct the affected connection, and choose Save securely + verify."
            )
        else:
            self.detail.setText(
                "Required local runtime, library, and market-data configuration are available."
                if ready
                else "One or more required runtime, library, or market-data checks need attention. No trading state was changed."
            )

        checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
        required_names = result.get("required_checks") if isinstance(result.get("required_checks"), list) else []
        required_names = [str(name) for name in required_names]
        passed = sum(1 for name in required_names if checks.get(name))
        jobs = result.get("jobs") if isinstance(result.get("jobs"), dict) else {}
        failures = jobs.get("recent_failures") if isinstance(jobs.get("recent_failures"), list) else []
        storage = result.get("storage") if isinstance(result.get("storage"), dict) else {}
        market = storage.get("market") if isinstance(storage.get("market"), dict) else {}
        required_text = f"{passed}/{len(required_names)}"
        if setup_pending:
            required_text += " · Setup pending"
        self.required.value.setText(required_text)
        self.active_jobs.value.setText(f"{int(jobs.get('active') or 0):,}")
        self.failed_jobs.value.setText(f"{len(failures):,}")
        self.cache_size.value.setText(_bytes(market.get("bytes")))

        library = result.get("library") if isinstance(result.get("library"), dict) else {}
        connection = result.get("connection") if isinstance(result.get("connection"), dict) else {}
        github_required = bool(connection.get("github_required_for_library"))
        capability_text = (
            f"verification {setup_verification} · "
            f"library {'yes' if setup_capabilities.get('library') else 'no'} · "
            f"cloud {'yes' if setup_capabilities.get('cloud') else 'no'} · "
            f"market {'yes' if setup_capabilities.get('market') else 'no'}"
        )
        details = {
            "runtime_service": (
                "Authenticated loopback sidecar answered /health"
                if checks.get("runtime_service")
                else "The authenticated local Python service did not report healthy"
            ),
            "runtime_storage": "Local durable SQLite job database is readable",
            "library_connection": (
                f"{connection.get('library_mode') or 'unknown'} library connection · preference {connection.get('library_source_preference') or 'auto'}"
            ),
            "library_readable": f"Library source: {library.get('source') or 'unverified'}",
            "alpaca_credentials": (
                f"Alpaca credentials present · feed {connection.get('market_feed') or 'unknown'}"
                if checks.get("alpaca_credentials")
                else "Alpaca API key/secret missing from macOS Keychain"
            ),
            "github_library_credential": (
                "GitHub credential present for cloud library/research"
                if checks.get("github_library_credential")
                else (
                    "Required for the active GitHub library connection"
                    if github_required
                    else "Optional for this local-library setup; required for cloud research"
                )
            ),
            "setup_not_pending": capability_text,
            "market_cache_present": f"{int(market.get('files') or 0):,} cached files · {_bytes(market.get('bytes'))}",
        }
        labels = {
            "runtime_service": "Authenticated local service",
            "runtime_storage": "Durable job storage",
            "library_connection": "Research library connection",
            "library_readable": "Research library readable",
            "alpaca_credentials": "Alpaca market data credentials",
            "github_library_credential": "Cloud research credential",
            "setup_not_pending": "Setup verification",
            "market_cache_present": "Persistent market cache",
        }
        rows = list(labels)
        self.checks.setRowCount(len(rows))
        for row, name in enumerate(rows):
            is_required = name in required_names
            value = bool(checks.get(name))
            if name == "setup_not_pending":
                status = "OK" if value else "Attention"
            elif value:
                status = "OK"
            elif not is_required:
                status = "Optional"
            else:
                status = "Attention"
            self.checks.setItem(row, 0, self._item(labels[name]))
            self.checks.setItem(row, 1, self._item(status))
            self.checks.setItem(row, 2, self._item(details.get(name, "")))

        self.failures.setRowCount(len(failures))
        for row, failure in enumerate(failures):
            self.failures.setItem(
                row,
                0,
                self._item(format_local_timestamp(failure.get("updated_at"), fallback="—")),
            )
            self.failures.setItem(row, 1, self._item(failure.get("job_type") or "—"))
            self.failures.setItem(row, 2, self._item(failure.get("stage") or "—"))
            message = sanitize_display_text(failure.get("message"))
            self.failures.setItem(row, 3, self._item(message or "—"))
