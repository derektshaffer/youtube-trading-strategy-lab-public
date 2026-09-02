"""Native Results page for bounded authoritative Trading Intelligence evidence."""

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


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError, OverflowError):
        return "—"


def _number(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError, OverflowError):
        return "—"


class ResultsPage(QWidget):
    refresh_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        header = QHBoxLayout()
        copy = QVBoxLayout()
        eyebrow = QLabel("RESULTS")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Review durable research evidence")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "This page reads bounded summaries from the authoritative research library. "
            "Large optimization payloads stay in storage instead of being copied into the desktop UI."
        )
        subtitle.setObjectName("Subtle")
        subtitle.setWordWrap(True)
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        self.refresh = QPushButton("Refresh Results")
        self.refresh.setObjectName("Primary")
        self.refresh.clicked.connect(self.refresh_requested.emit)
        header.addLayout(copy, 1)
        header.addWidget(self.refresh, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.banner = Card()
        banner = QVBoxLayout(self.banner)
        self.status = QLabel("Results have not been loaded yet.")
        self.status.setObjectName("BannerTitle")
        self.detail = QLabel(
            "Refresh to load recent Finder, strict-validation, Strategy Lab, and strategy-status summaries."
        )
        self.detail.setObjectName("Subtle")
        self.detail.setWordWrap(True)
        banner.addWidget(self.status)
        banner.addWidget(self.detail)
        root.addWidget(self.banner)

        metrics = QGridLayout()
        self.finder_metric = MetricCard("Finder runs")
        self.validation_metric = MetricCard("Validation runs")
        self.lab_metric = MetricCard("Strategy Lab")
        self.validated_metric = MetricCard("Validated strategies")
        for index, widget in enumerate(
            (
                self.finder_metric,
                self.validation_metric,
                self.lab_metric,
                self.validated_metric,
            )
        ):
            metrics.addWidget(widget, 0, index)
        root.addLayout(metrics)

        self.tabs = QTabWidget()
        self.finder_table = self._table(
            ["When", "Symbol", "Profile", "Winner", "Verdict", "Timeframe", "Holdout P/L", "Configs"]
        )
        self.validation_table = self._table(
            ["When", "Strategy", "Symbol", "Status", "Verdict", "Method"]
        )
        self.lab_table = self._table(
            ["When", "Ticker", "Status", "Stage", "Winner", "Verdict", "Strength"]
        )
        self.strategy_table = self._table(
            ["Updated", "Strategy", "Category", "Symbol", "Validation status", "Source"]
        )
        self.tabs.addTab(self._card_for(self.finder_table), "Finder")
        self.tabs.addTab(self._card_for(self.validation_table), "Strict Validation")
        self.tabs.addTab(self._card_for(self.lab_table), "Strategy Lab")
        self.tabs.addTab(self._card_for(self.strategy_table), "Strategy Status")
        root.addWidget(self.tabs, 1)

        safety = QLabel(
            "Research evidence only · a row is not a live-trading approval. "
            "Only explicit validated status is counted as validated above."
        )
        safety.setObjectName("Subtle")
        safety.setWordWrap(True)
        root.addWidget(safety)

    @staticmethod
    def _card_for(table: QTableWidget) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(table)
        return card

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        if len(headers) > 3:
            table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        table.setMinimumHeight(340)
        return table

    @staticmethod
    def _fill(table: QTableWidget, rows: list[tuple[str, ...]]) -> None:
        table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                table.setItem(row_index, column, item)

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
        self.status.setText("Results could not load")
        self.detail.setText(message)

    def render_results(self, result: dict[str, Any]) -> None:
        self.refresh.setEnabled(True)
        self.banner.setProperty("state", "ready")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        library = result.get("library") if isinstance(result.get("library"), dict) else {}
        source = _display(library.get("source"), "authoritative library").replace("_", " ")
        warning = _display(library.get("warning"), "")
        limit = int(result.get("limit") or 25)
        self.status.setText(f"Results ready · {source}")
        detail = f"Showing up to {limit} recent rows per section; full evidence remains in durable storage."
        if warning:
            detail += " " + warning
        self.detail.setText(detail)

        counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
        self.finder_metric.value.setText(f"{int(counts.get('finder_runs') or 0):,}")
        self.validation_metric.value.setText(f"{int(counts.get('validation_runs') or 0):,}")
        self.lab_metric.value.setText(f"{int(counts.get('strategy_lab_runs') or 0):,}")
        self.validated_metric.value.setText(f"{int(counts.get('validated_strategies') or 0):,}")

        finder_rows = []
        for item in result.get("finder_runs") or []:
            if not isinstance(item, dict):
                continue
            finder_rows.append(
                (
                    _display(item.get("generated_at")),
                    _display(item.get("symbol")),
                    _display(item.get("profile")),
                    _display(item.get("winner")),
                    _display(item.get("verdict")),
                    _display(item.get("timeframe")),
                    _money(item.get("holdout_pnl")),
                    f"{int(item.get('configurations') or 0):,}",
                )
            )
        self._fill(self.finder_table, finder_rows)

        validation_rows = []
        for item in result.get("validation_runs") or []:
            if not isinstance(item, dict):
                continue
            validation_rows.append(
                (
                    _display(item.get("generated_at")),
                    _display(item.get("strategy_name") or item.get("strategy_id")),
                    _display(item.get("symbol")),
                    _display(item.get("status")),
                    _display(item.get("verdict")),
                    _display(item.get("method")),
                )
            )
        self._fill(self.validation_table, validation_rows)

        lab_rows = []
        for item in result.get("strategy_lab_runs") or []:
            if not isinstance(item, dict):
                continue
            lab_rows.append(
                (
                    _display(item.get("saved_at")),
                    _display(item.get("ticker")),
                    _display(item.get("status")),
                    _display(item.get("stage")),
                    _display(item.get("winner")),
                    _display(item.get("verdict")),
                    _number(item.get("strength_score")),
                )
            )
        self._fill(self.lab_table, lab_rows)

        strategy_rows = []
        for item in result.get("strategies") or []:
            if not isinstance(item, dict):
                continue
            strategy_rows.append(
                (
                    _display(item.get("updated_at")),
                    _display(item.get("name") or item.get("id")),
                    _display(item.get("category")),
                    _display(item.get("optimized_for_symbol")),
                    _display(item.get("validation_status")),
                    _display(item.get("source_type")),
                )
            )
        self._fill(self.strategy_table, strategy_rows)
