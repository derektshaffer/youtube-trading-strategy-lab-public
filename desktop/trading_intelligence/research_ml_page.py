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
from .workflow_widgets import readable_table
from .error_sanitizer import sanitize_display_text
from .display_time import format_timestamp as format_local_timestamp


def _display(value: Any, fallback: str = "—") -> str:
    return sanitize_display_text(value or fallback)


def _detail_display(field: str, value: Any) -> str:
    """Preserve scalar zero/False in details without changing table contracts."""
    if value is None:
        return "Not recorded"
    # The bounded summary uses negative confidence as its missing-value sentinel.
    if field == "confidence" and isinstance(value, (int, float)) and value < 0:
        return "Not recorded"
    if isinstance(value, (bool, int, float)):
        return sanitize_display_text(str(value))
    # Summary detail fields have no meaningful empty-collection contract.
    # Retain existing string/collection missing behavior rather than redefining it.
    return _display(value, "Not recorded")


def _display_when(value: Any, fallback: str = "—") -> str:
    return format_local_timestamp(value, fallback=fallback)


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
        self.load_state = "unloaded"
        self.sections = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        header = QHBoxLayout()
        copy = QVBoxLayout()
        eyebrow = QLabel("RESEARCH LIBRARY")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Explore the research library")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "Library-wide records, not filtered by the stock or strategy selected elsewhere. "
            "Refresh reads saved evidence; it never launches compute or changes trading decisions."
        )
        subtitle.setObjectName("Subtle")
        subtitle.setWordWrap(True)
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        self.refresh = QPushButton("Refresh Research")
        self.refresh.setObjectName("Primary")
        self.refresh.clicked.connect(self.refresh_requested.emit)
        root.addLayout(copy)
        header.addWidget(self.refresh)
        header.addStretch(1)
        root.addLayout(header)

        self.banner = Card()
        banner = QVBoxLayout(self.banner)
        self.status = QLabel("Load your saved research")
        self.status.setObjectName("BannerTitle")
        self.detail = QLabel(
            "Choose Refresh Research to read the connected library. No research, model training, validation or backtest will be started."
        )
        self.detail.setObjectName("Subtle")
        self.detail.setWordWrap(True)
        banner.addWidget(self.status)
        banner.addWidget(self.detail)
        root.addWidget(self.banner)

        metrics = QGridLayout()
        self.active = MetricCard("Active queue records")
        self.hypotheses_metric = MetricCard("Hypotheses")
        self.experiments_metric = MetricCard("Experiments")
        self.sources_metric = MetricCard("Sources")
        self.models_metric = MetricCard("ML runs")
        self.metric_cards = {
            "active_cloud_jobs": self.active, "hypotheses": self.hypotheses_metric,
            "experiments": self.experiments_metric, "sources": self.sources_metric,
            "predictive_ml_runs": self.models_metric,
        }
        for index, widget in enumerate(self.metric_cards.values()):
            widget.value.setText("Not loaded")
            metrics.addWidget(widget, index // 3, index % 3)
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
        self.tabs.tabBar().setExpanding(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.addTab(self._section(
            self.run_table, "research_runs", "Research runs",
            "Topic-led findings and worker activity across the library. Sources can inform hypotheses; "
            "worker records may only record processing status.",
            "No research runs saved yet.",
            "Runs are saved by the existing web research/autopilot and cloud workers. "
            "This desktop view cannot start a research cycle."), "Research Runs")
        self.tabs.addTab(self._section(
            self.source_table, "sources", "Sources",
            "Library-wide books, documents and other ingested material. A source can support multiple strategies; "
            "it is not evidence for the currently selected stock by default.",
            "No sources saved yet.",
            "Sources appear after the existing web document/book ingestion flow saves them. "
            "Standalone native source import is not available here."), "Sources")
        self.tabs.addTab(self._section(
            self.hypothesis_table, "hypotheses", "Hypotheses",
            "Unproven ideas derived from research. They may link to a research run and later to a strategy; "
            "confidence is not a validation verdict.",
            "No hypotheses saved yet.",
            "The research worker derives hypotheses from grounded research. Its review can queue further work; "
            "manual creation is not exposed on this read-only page."), "Hypotheses")
        self.tabs.addTab(self._section(
            self.experiment_table, "experiments", "Experiments",
            "Strategy-linked records of deterministic testing, stage outcomes and eligibility decisions. "
            "They are not a separate manual experiment runner.",
            "No experiments saved yet.",
            "Existing validation workflows create these records as candidates are tested. "
            "Viewing this tab never starts or retries that work."), "Experiments")
        ml = QWidget()
        ml_layout = QVBoxLayout(ml)
        ml_layout.setContentsMargins(0, 0, 0, 0)
        ml_layout.addWidget(self._section(
            self.ml_run_table, "predictive_ml_runs", "Predictive ML runs",
            "Saved training/evaluation runs use their own symbol sets and datasets, not the selected stock. "
            "Models are a separate branch of research, not an automatic next step for every experiment.",
            "No predictive ML runs saved yet.",
            "Runs are saved by the existing web ML workflow or cloud backfill worker. "
            "Training is not launched here."))
        ml_layout.addWidget(self._section(
            self.shadow_table, "ready_shadow_models", "Shadow model summaries",
            "Up to 12 eligible model summaries. Shadow scoring flags describe saved model configuration, "
            "not live deployment or production approval.",
            "No shadow model summaries available.",
            "Models appear after the existing model registry's eligibility checks. "
            "This view cannot activate a model or change live ranking."))
        self.tabs.addTab(ml, "Predictive ML")
        self.tabs.addTab(self._section(
            self.queue_table, "queue", "Cloud queue",
            "Last-reported queue records across job types. Active counts are recorded states, "
            "not proof of a live worker or a ticker-specific research run.",
            "No queue records saved yet.",
            "Existing web workflows and workers enqueue jobs. This view only reads status; "
            "it cannot dispatch, cancel or retry them."), "Cloud Queue")
        root.addWidget(self.tabs)
        self.safety = QLabel(
            "Research only. This view cannot place trades, change live ranking or bypass validation. "
            "Predictive models are shadow models and do not place trades; they are not production-approved."
        )
        self.safety.setObjectName("Subtle")
        self.safety.setWordWrap(True)
        root.addWidget(self.safety)

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
        readable_table(table)
        table.setMinimumWidth(0)
        return table

    def _section(self, table, key, title, description, empty, next_step):
        card = Card()
        layout = QVBoxLayout(card)
        heading = QLabel(title)
        heading.setObjectName("BannerTitle")
        note = QLabel(description)
        note.setWordWrap(True)
        state = QLabel("Not loaded. Choose Refresh Research to read saved records.")
        state.setWordWrap(True)
        state.setObjectName("Subtle")
        details = QLabel("Select a row for its saved details and identity.")
        details.setWordWrap(True)
        details.setTextFormat(Qt.TextFormat.PlainText)
        details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(heading)
        layout.addWidget(note)
        layout.addWidget(state)
        layout.addWidget(table)
        layout.addWidget(details)
        table.hide()
        details.hide()
        self.sections[key] = {
            "table": table, "state": state, "details": details,
            "empty": empty, "next": next_step, "rows": [],
        }
        table.itemSelectionChanged.connect(lambda: self._row_details(key))
        return card

    def _row_details(self, key):
        section = self.sections[key]
        row = section["table"].currentRow()
        if not 0 <= row < len(section["rows"]):
            section["details"].setText("Select a row for its saved details and identity.")
            return
        record = section["rows"][row]
        section["details"].setText("\n".join(
            str(field).replace("_", " ").title() + ": " + _detail_display(field, value)
            for field, value in record.items()
        ))

    @staticmethod
    def _fill(table: QTableWidget, rows: list[tuple[str, ...]]) -> None:
        table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                table.setItem(row_index, column, QTableWidgetItem(value))
        table.setFixedHeight(min(260, max(80, 46 + len(rows) * 38)))

    def _pending(self, message):
        for card in self.metric_cards.values():
            card.show()
            card.value.setText(message)
        for section in self.sections.values():
            section["table"].hide()
            section["details"].hide()
            section["state"].setText(message + ". Choose Refresh Research when available.")

    def set_working(self, title: str, detail: str) -> None:
        self.load_state = "loading"
        self._pending("Loading")
        self.refresh.setEnabled(False)
        self.banner.setProperty("state", "working")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText(title)
        self.detail.setText(detail)

    def set_error(self, message: str) -> None:
        self.load_state = "error"
        self._pending("Unavailable")
        self.refresh.setEnabled(True)
        self.banner.setProperty("state", "error")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText("Research library could not load")
        self.detail.setText(sanitize_display_text(message))

    def render_summary(self, result: dict[str, Any]) -> None:
        self.load_state = "ready"
        self.refresh.setEnabled(True)
        self.banner.setProperty("state", "ready")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
        library = result.get("library") if isinstance(result.get("library"), dict) else {}
        source = _display(library.get("source"), "authoritative library").replace("_", " ")
        system = result.get("research_system") if isinstance(result.get("research_system"), dict) else {}
        system_status = _display(system.get("status"), "durable queue available")
        self.status.setText(f"Research library loaded · {source}")
        self.detail.setText(
            f"Recorded system status: {system_status}. Recent rows only (up to {result.get('limit_per_section', 30)} per section); "
            "counts describe the loaded library, not just visible rows. Full artifacts stay in storage. "
            + sanitize_display_text(library.get("warning") or "")
        )
        for key, card in self.metric_cards.items():
            value = counts.get(key)
            known = isinstance(value, int) and not isinstance(value, bool) and value >= 0
            card.setVisible(known)
            card.value.setText(f"{value:,}" if known else "Unavailable")
        for key, section in self.sections.items():
            raw = result.get(key)
            section["rows"] = [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []
            present = bool(section["rows"])
            section["table"].setVisible(present)
            section["details"].setVisible(present)
            section["details"].setText("Select a row for its saved details and identity.")
            section["state"].setText(
                f"{len(section['rows'])} recent records. Select a row to inspect details." if present else
                (section["empty"] if isinstance(raw, list) else "This section was not reported by the library summary.")
                + "\n" + section["next"]
            )

        self._fill(
            self.queue_table,
            [
                (
                    _display_when(item.get("when")),
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
                    _display_when(item.get("when")),
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
                    _display_when(item.get("when")),
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
                    _display_when(item.get("when")),
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
                    _display_when(item.get("when")),
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
                    _display_when(item.get("when")),
                    _display(item.get("title")),
                    _display(item.get("source_type")),
                    _display(item.get("status")),
                    _display(item.get("url")),
                )
                for item in result.get("sources") or []
                if isinstance(item, dict)
            ],
        )
