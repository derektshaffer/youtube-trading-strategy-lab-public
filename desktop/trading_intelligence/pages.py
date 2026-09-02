"""Profit First, durable-jobs, and secure-connection pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from hybrid_runtime.desktop_settings import DesktopSettings


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")


class MetricCard(Card):
    def __init__(self, label: str, value: str = "—") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        caption = QLabel(label.upper())
        caption.setObjectName("MetricCaption")
        self.value = QLabel(value)
        self.value.setObjectName("MetricValue")
        layout.addWidget(caption)
        layout.addWidget(self.value)


class ProfitFirstPage(QWidget):
    refresh_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        top = QHBoxLayout()
        titles = QVBoxLayout()
        eyebrow = QLabel("PROFIT FIRST")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Find the strongest strategy candidates first")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "The desktop app reads the same authoritative research library and ranks "
            "only candidates eligible for strict validation. It does not weaken any gate."
        )
        subtitle.setObjectName("Subtle")
        subtitle.setWordWrap(True)
        titles.addWidget(eyebrow)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        self.refresh = QPushButton("Refresh Profit First")
        self.refresh.setObjectName("Primary")
        self.refresh.clicked.connect(self.refresh_requested.emit)
        top.addLayout(titles, 1)
        top.addWidget(self.refresh, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(top)

        self.banner = Card()
        banner_layout = QVBoxLayout(self.banner)
        self.banner_title = QLabel("Connecting to the research library…")
        self.banner_title.setObjectName("BannerTitle")
        self.banner_detail = QLabel(
            "The web application remains available while the desktop cache is prepared."
        )
        self.banner_detail.setObjectName("Subtle")
        self.banner_detail.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        banner_layout.addWidget(self.banner_title)
        banner_layout.addWidget(self.banner_detail)
        banner_layout.addWidget(self.progress)
        root.addWidget(self.banner)

        metrics = QGridLayout()
        self.strategy_metric = MetricCard("Strategies")
        self.validation_metric = MetricCard("Validation runs")
        self.eligible_metric = MetricCard("Eligible now")
        self.blocked_metric = MetricCard("Fidelity blocked")
        for index, widget in enumerate(
            (
                self.strategy_metric,
                self.validation_metric,
                self.eligible_metric,
                self.blocked_metric,
            )
        ):
            metrics.addWidget(widget, 0, index)
        root.addLayout(metrics)

        candidates = Card()
        candidates_layout = QVBoxLayout(candidates)
        heading = QLabel("Next strict validation candidates")
        heading.setObjectName("SectionTitle")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Strategy", "Phase", "Score", "Positive periods", "Last validation"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        self.table.setMinimumHeight(215)
        candidates_layout.addWidget(heading)
        candidates_layout.addWidget(self.table)
        root.addWidget(candidates, 1)

        next_card = Card()
        next_layout = QHBoxLayout(next_card)
        next_copy = QVBoxLayout()
        next_heading = QLabel("What happens next")
        next_heading.setObjectName("SectionTitle")
        self.next_detail = QLabel(
            "Connect a library to calculate the next Profit First action."
        )
        self.next_detail.setObjectName("Subtle")
        self.next_detail.setWordWrap(True)
        next_copy.addWidget(next_heading)
        next_copy.addWidget(self.next_detail)
        self.validation = QPushButton("Cloud validation bridge pending")
        self.validation.setEnabled(False)
        self.validation.setToolTip(
            "Strict validation remains in the existing cloud worker until the durable cloud bridge is connected."
        )
        next_layout.addLayout(next_copy, 1)
        next_layout.addWidget(self.validation)
        root.addWidget(next_card)

    def set_working(self, status: str, detail: str, progress: float = 0.0) -> None:
        self.refresh.setEnabled(False)
        self.banner_title.setText(status)
        self.banner_detail.setText(detail)
        self.progress.setValue(round(max(0.0, min(1.0, progress)) * 1000))

    def set_error(self, message: str) -> None:
        self.refresh.setEnabled(True)
        self.banner.setProperty("state", "error")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.banner_title.setText("Profit First could not load")
        self.banner_detail.setText(message)
        self.progress.setValue(0)
        self.table.setRowCount(0)
        self.next_detail.setText(
            "Open Connection Settings and choose the current private GitHub backup or a local library file."
        )

    def render_plan(self, plan: dict[str, Any]) -> None:
        self.refresh.setEnabled(True)
        self.banner.setProperty("state", "ready")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        library = plan.get("library") if isinstance(plan.get("library"), dict) else {}
        source = str(library.get("source") or "unknown").replace("_", " ")
        warning = str(library.get("warning") or "").strip()
        queue_status = str(plan.get("queue_status") or "unknown")
        phase = str(plan.get("phase") or "unknown").replace("_", " ")
        self.banner_title.setText(
            f"Library ready · {source} · {queue_status.replace('_', ' ')}"
        )
        detail = f"Profit First phase: {phase}."
        if warning:
            detail += " " + warning
        self.banner_detail.setText(detail)
        self.progress.setValue(1000)
        self.strategy_metric.value.setText(f"{int(library.get('strategies') or 0):,}")
        self.validation_metric.value.setText(
            f"{int(library.get('validation_runs') or 0):,}"
        )
        self.eligible_metric.value.setText(f"{int(plan.get('eligible_count') or 0):,}")
        self.blocked_metric.value.setText(
            f"{int(plan.get('fidelity_blocked_count') or 0):,}"
        )

        rows = [item for item in plan.get("candidates") or [] if isinstance(item, dict)]
        self.table.setRowCount(len(rows))
        for row_index, candidate in enumerate(rows):
            values = (
                str(candidate.get("strategy_name") or candidate.get("strategy_id") or "Unknown"),
                str(candidate.get("phase") or "").replace("_", " "),
                f"{float(candidate.get('score') or 0.0):.2f}",
                str(int(candidate.get("positive_evidence_periods") or 0)),
                str(candidate.get("latest_validation_generated_at") or "Never"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in {2, 3}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row_index, column, item)

        descriptions = {
            "active": (
                "A strict Profit First validation job is already active in the authoritative cloud queue. "
                "The desktop cloud bridge will attach to that exact job rather than duplicate it."
            ),
            "ready": (
                "These candidates are ready for strict cloud validation. The next milestone connects this "
                "button to the existing durable validation worker without moving the computation onto your Mac."
            ),
            "already-attempted": (
                "This exact candidate batch has already been attempted. Profit First will wait for new evidence "
                "instead of wasting compute on an identical rerun."
            ),
            "no-eligible-candidates": (
                "There are no untested candidates that currently pass the strategy-fidelity gate. "
                "Blocked or already-tested ideas are not presented as promising strategies."
            ),
        }
        self.next_detail.setText(
            descriptions.get(
                queue_status,
                "Profit First loaded, but the current queue status needs review before validation.",
            )
        )


class JobsPage(QWidget):
    refresh_requested = Signal()
    cancel_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        eyebrow = QLabel("DURABLE JOBS")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Research work survives navigation and reconnects")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "Local jobs are stored in SQLite. Cloud jobs will use the same IDs and state model."
        )
        subtitle.setObjectName("Subtle")
        titles.addWidget(eyebrow)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        refresh = QPushButton("Refresh jobs")
        refresh.clicked.connect(self.refresh_requested.emit)
        top.addLayout(titles, 1)
        top.addWidget(refresh, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(top)

        card = Card()
        layout = QVBoxLayout(card)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Type", "Status", "Stage", "Route", "Progress", "Updated"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 6):
            self.table.horizontalHeader().setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        self.table.setMinimumHeight(420)
        controls = QHBoxLayout()
        self.summary = QLabel("No jobs loaded yet")
        self.summary.setObjectName("Subtle")
        self.cancel = QPushButton("Cancel selected")
        self.cancel.clicked.connect(self._emit_cancel)
        controls.addWidget(self.summary, 1)
        controls.addWidget(self.cancel)
        layout.addWidget(self.table)
        layout.addLayout(controls)
        root.addWidget(card, 1)

    def _emit_cancel(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        job_id = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        if job_id:
            self.cancel_requested.emit(job_id)

    def render_jobs(self, jobs: list[dict[str, Any]]) -> None:
        self.table.setRowCount(len(jobs))
        active = 0
        for row, job in enumerate(jobs):
            if not bool(job.get("terminal")):
                active += 1
            values = (
                str(job.get("job_type") or ""),
                str(job.get("status") or ""),
                str(job.get("stage") or ""),
                str(job.get("execution_target") or ""),
                f"{float(job.get('progress') or 0.0) * 100:.0f}%",
                str(job.get("updated_at") or ""),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, str(job.get("id") or ""))
                self.table.setItem(row, column, item)
        self.summary.setText(f"{len(jobs):,} recent jobs · {active:,} active")


class ConnectionPage(QWidget):
    saved = Signal(dict, str)
    test_requested = Signal()

    def __init__(self, settings: DesktopSettings) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        eyebrow = QLabel("CONNECTION SETTINGS")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Connect the authoritative research library")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "Credentials are stored in macOS Keychain. They are never written into job payloads, "
            "the SQLite queue, application logs, or this repository."
        )
        subtitle.setObjectName("Subtle")
        subtitle.setWordWrap(True)
        root.addWidget(eyebrow)
        root.addWidget(title)
        root.addWidget(subtitle)

        card = Card()
        form = QGridLayout(card)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)
        self.source = QComboBox()
        self.source.addItem("Automatic: local file, then private GitHub", "auto")
        self.source.addItem("Local JSON library", "local_file")
        self.source.addItem("Private GitHub backup", "github_backup")
        self.local_path = QLineEdit()
        self.local_path.setPlaceholderText("/path/to/strategy_library.json")
        browse = QPushButton("Choose…")
        browse.clicked.connect(self.choose_file)
        local_row = QHBoxLayout()
        local_row.addWidget(self.local_path, 1)
        local_row.addWidget(browse)
        local_container = QWidget()
        local_container.setLayout(local_row)
        self.repository = QLineEdit()
        self.branch = QLineEdit()
        self.github_path = QLineEdit()
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Leave blank to keep the existing Keychain token")

        rows = (
            ("Source", self.source),
            ("Local library", local_container),
            ("Private repository", self.repository),
            ("Branch", self.branch),
            ("Library path", self.github_path),
            ("GitHub token", self.token),
        )
        for row, (caption, widget) in enumerate(rows):
            label = QLabel(caption)
            label.setObjectName("FormLabel")
            form.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            form.addWidget(widget, row, 1)
        form.setColumnStretch(1, 1)
        root.addWidget(card)

        controls = QHBoxLayout()
        self.status = QLabel("Connection changes have not been tested yet.")
        self.status.setObjectName("Subtle")
        self.status.setWordWrap(True)
        test = QPushButton("Test connection")
        test.clicked.connect(self.test_requested.emit)
        save = QPushButton("Save securely")
        save.setObjectName("Primary")
        save.clicked.connect(self.emit_save)
        controls.addWidget(self.status, 1)
        controls.addWidget(test)
        controls.addWidget(save)
        root.addLayout(controls)
        root.addStretch(1)
        self.populate(settings)

    def populate(self, settings: DesktopSettings) -> None:
        index = self.source.findData(settings.library_source)
        self.source.setCurrentIndex(max(0, index))
        self.local_path.setText(settings.local_library_path)
        self.repository.setText(settings.github_repository)
        self.branch.setText(settings.github_branch)
        self.github_path.setText(settings.github_path)
        self.token.clear()

    def choose_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose Trading Intelligence library",
            str(Path.home()),
            "JSON files (*.json);;All files (*)",
        )
        if path:
            self.local_path.setText(path)
            self.source.setCurrentIndex(self.source.findData("local_file"))

    def emit_save(self) -> None:
        payload = {
            "library_source": str(self.source.currentData() or "auto"),
            "local_library_path": self.local_path.text().strip(),
            "github_repository": self.repository.text().strip(),
            "github_branch": self.branch.text().strip(),
            "github_path": self.github_path.text().strip(),
            "refresh_on_launch": True,
        }
        self.saved.emit(payload, self.token.text().strip())
