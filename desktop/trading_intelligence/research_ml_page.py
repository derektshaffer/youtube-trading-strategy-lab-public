"""Native bounded status surface for autonomous research and predictive ML."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .pages import Card, MetricCard


def _display(value: Any, fallback: str = "—") -> str:
    text = " ".join(str(value or "").split())
    return text if text else fallback


def _percent(value: Any) -> str:
    try:
        return f"{max(0.0, min(1.0, float(value))) * 100:.0f}%"
    except (TypeError, ValueError, OverflowError):
        return "—"


def _confidence(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return "—"
    if number < 0:
        return "—"
    return f"{number:.2f}"


class ResearchMLPage(QWidget):
    refresh_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        header = QHBoxLayout()
        copy = QVBoxLayout()
        eyebrow = QLabel("RESEARCH + ML")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Autonomous research and model status")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "A bounded read-only view of the durable cloud queue, research history, "
            "hypotheses, sources, and predictive shadow models. It never launches compute or changes trading decisions."
        )
        subtitle.setObjectName("Subtle")
        subtitle.setWordWrap(True)
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        self.refresh = QPushButton("Refresh Research + ML")
        self.refresh.setObjectName("Primary")
        self.refresh.clicked.connect(self.refresh_requested.emit)
        header.addLayout(copy, 1)
        header.addWidget(self.refresh, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.banner = Card()
        banner = QVBoxLayout(self.banner)
        self.status = QLabel("Research and ML status has not been loaded yet.")
        self.status.setObjectName("BannerTitle")
        self.detail = QLabel(
            "Refresh to read the current private research library. Cloud workers continue independently of this page."
        )
        self.detail.setObjectName("Subtle")
        self.detail.setWordWrap(True)
        banner.addWidget(self.status)
        banner.addWidget(self.detail)
        root.addWidget(self.banner)

        metrics = QGridLayout()
        self.active = MetricCard("Active cloud jobs")
        self.hypotheses_metric = MetricCard("Hypotheses")
        self.experiments_metric = MetricCard("Experiments")
        self.sources_metric = MetricCard("Sources")
        self.models_metric = MetricCard("Shadow-ready models")
        for index, widget in enumerate(
            (
                self.active,
                self.hypotheses_metric,
                self.experiments_metric,
                self.sources_metric,
                self.models_metric,
            )
        ):
            metrics.addWidget(widget, 0, index)
        root.addLayout(metrics)

        self.tabs = QTabWidget()
        self.queue_table = self._table(
            ["Updated", "Type", "Status", "Stage", "Progress", "Message"],
            stretch_column=5,
        )
        self.run_table = self._table(
            ["When", "Kind", "Status", "Topic", "Model", "Hypotheses", "Sources"],
            stretch_column=3,
        )
        self.hypothesis_table = self._table(
            ["When", "Hypothesis", "Category", "Direction", "Status", "Confidence"],
            stretch_column=1,
        )
        self.experiment_table = self._table(
            ["Updated", "Experiment", "Stage", "Stage result", "Promotion", "Why"],
            stretch_column=5,
        )
        self.ml_run_table = self._table(
            ["When", "Status", "Models", "Symbols", "Rows", "Integrity", "Method"],
            stretch_column=6,
        )
        self.shadow_table = self._table(
            ["Model ID", "Target", "Session", "Model type", "Shadow scoring"],
            stretch_column=0,
        )
        self.source_table = self._table(
            ["When", "Source", "Type", "Status", "URL"],
            stretch_column=1,
        )
        self.tabs.addTab(self._card_for(self.queue_table), "Cloud Queue")
        self.tabs.addTab(self._card_for(self.run_table), "Research Runs")
        self.tabs.addTab(self._card_for(self.hypothesis_table), "Hypotheses")
        self.tabs.addTab(self._card_for(self.experiment_table), "Experiments")
        self.tabs.addTab(self._ml_card(), "Predictive ML")
        self.tabs.addTab(self._card_for(self.source_table), "Sources")
        root.addWidget(self.tabs, 1)

        safety = QLabel(
            "Research-only status · predictive models shown here are shadow models and do not place trades, "
            "change live ranking, or bypass validation gates."
        )
        safety.setObjectName("Subtle")
        safety.setWordWrap(True)
        root.addWidget(safety)

    @staticmethod
    def _table(headers: list[str], *, stretch_column: int) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        if 0 <= stretch_column < len(headers):
            table.horizontalHeader().setSectionResizeMode(
                stretch_column, QHeaderView.ResizeMode.Stretch
            )
        table.setMinimumHeight(330)
        return table

    @staticmethod
    def _card_for(table: QTableWidget) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(table)
        return card

    def _ml_card(self) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        title = QLabel("Recent predictive ML runs")
        title.setObjectName("BannerTitle")
        layout.addWidget(title)
        layout.addWidget(self.ml_run_table)
        shadow_title = QLabel("Shadow-ready probability models")
        shadow_title.setObjectName("BannerTitle")
        layout.addWidget(shadow_title)
        layout.addWidget(self.shadow_table)
        return card

    @staticmethod
    def _fill(table: QTableWidget, rows: list[tuple[str, ...]]) -> None:
        table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                table.setItem(row_index, column, QTableWidgetItem(value))

    def set_working(self, title: str, detail: str) -> None:
        self.refresh.setEnabled(False)
        self.banner.setProperty("state", "working")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText(title)
        self.detail.setText(detail)

    def set_error(self, message: str) -> None:
        self.refresh.setEnabled(True)
        self.banner.setProperty("state", "error")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText("Research + ML status could not load")
        self.detail.setText(message)

    def render_summary(self, result: dict[str, Any]) -> None:
        self.refresh.setEnabled(True)
        self.banner.setProperty("state", "ready")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
        library = result.get("library") if isinstance(result.get("library"), dict) else {}
        source = _display(library.get("source"), "authoritative library").replace("_", " ")
        system = result.get("research_system") if isinstance(result.get("research_system"), dict) else {}
        system_status = _display(system.get("status"), "durable queue available")
        self.status.setText(f"Research + ML ready · {source}")
        self.detail.setText(
            f"Research system: {system_status}. Showing bounded summaries only; full evidence and model artifacts remain in durable storage."
        )
        self.active.value.setText(f"{int(counts.get('active_cloud_jobs') or 0):,}")
        self.hypotheses_metric.value.setText(f"{int(counts.get('hypotheses') or 0):,}")
        self.experiments_metric.value.setText(f"{int(counts.get('experiments') or 0):,}")
        self.sources_metric.value.setText(f"{int(counts.get('sources') or 0):,}")
        self.models_metric.value.setText(f"{int(counts.get('ready_shadow_models') or 0):,}")

        self._fill(
            self.queue_table,
            [
                (
                    _display(item.get("when")),
                    _display(item.get("type")),
                    _display(item.get("status")),
                    _display(item.get("stage")),
                    _percent(item.get("progress")),
                    _display(item.get("message")),
                )
                for item in result.get("queue") or []
                if isinstance(item, dict)
            ],
        )
        self._fill(
            self.run_table,
            [
                (
                    _display(item.get("when")),
                    _display(item.get("kind")),
                    _display(item.get("status")),
                    _display(item.get("topic")),
                    _display(item.get("model")),
                    f"{int(item.get('hypothesis_count') or 0):,}",
                    f"{int(item.get('source_count') or 0):,}",
                )
                for item in result.get("research_runs") or []
                if isinstance(item, dict)
            ],
        )
        self._fill(
            self.hypothesis_table,
            [
                (
                    _display(item.get("when")),
                    _display(item.get("name")),
                    _display(item.get("category")),
                    _display(item.get("direction")),
                    _display(item.get("status")),
                    _confidence(item.get("confidence")),
                )
                for item in result.get("hypotheses") or []
                if isinstance(item, dict)
            ],
        )
        self._fill(
            self.experiment_table,
            [
                (
                    _display(item.get("when")),
                    _display(item.get("strategy_name")),
                    _display(item.get("stage")).replace("_", " ").title(),
                    _display(item.get("stage_status")).replace("_", " ").title(),
                    _display(item.get("promotion_status")).replace("_", " ").title(),
                    _display(item.get("reason")),
                )
                for item in result.get("experiments") or []
                if isinstance(item, dict)
            ],
        )
        self._fill(
            self.ml_run_table,
            [
                (
                    _display(item.get("when")),
                    _display(item.get("status")),
                    f"{int(item.get('model_count') or 0):,}",
                    f"{int(item.get('symbol_count') or 0):,}",
                    f"{int(item.get('row_count') or 0):,}",
                    _display(item.get("integrity_contract")),
                    _display(item.get("method")),
                )
                for item in result.get("predictive_ml_runs") or []
                if isinstance(item, dict)
            ],
        )
        self._fill(
            self.shadow_table,
            [
                (
                    _display(item.get("id")),
                    _display(item.get("target")),
                    _display(item.get("session_mode")),
                    _display(item.get("model_type")),
                    "Enabled" if item.get("shadow_scoring_enabled") else "Off",
                )
                for item in result.get("ready_shadow_models") or []
                if isinstance(item, dict)
            ],
        )
        self._fill(
            self.source_table,
            [
                (
                    _display(item.get("when")),
                    _display(item.get("title")),
                    _display(item.get("source_type")),
                    _display(item.get("status")),
                    _display(item.get("url")),
                )
                for item in result.get("sources") or []
                if isinstance(item, dict)
            ],
        )
