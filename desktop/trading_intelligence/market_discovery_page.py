"""Native strategy-to-stock Market Discovery page matching the web workflow."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .pages import Card, MetricCard


class MarketDiscoveryPage(QWidget):
    """Find stocks whose current conditions match saved strategy rules."""

    options_requested = Signal()
    run_requested = Signal(dict)
    analyze_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.options_loaded = False
        self._results: list[dict[str, Any]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        header = QHBoxLayout()
        copy = QVBoxLayout()
        eyebrow = QLabel("FIND STOCKS")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Find stocks worth watching")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "Scan the current market for stocks that fit the measurable rules of your "
            "faithful strategy families. Validation status and current setup quality stay separate."
        )
        subtitle.setObjectName("Subtle")
        subtitle.setWordWrap(True)
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        self.refresh_options = QPushButton("Refresh strategies")
        self.refresh_options.clicked.connect(self.options_requested.emit)
        header.addLayout(copy, 1)
        header.addWidget(self.refresh_options, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        controls_card = Card()
        controls = QGridLayout(controls_card)
        controls.setHorizontalSpacing(12)
        controls.setVerticalSpacing(10)
        self.strategy = QComboBox()
        self.strategy.addItem("All usable strategy families", "")
        self.strategy.setMinimumWidth(270)
        self.include_research = QCheckBox("Include research-only strategies")
        self.include_research.setChecked(True)
        self.include_research.setToolTip(
            "Research-only matches remain visible as leads; they are never presented as validated edges."
        )
        self.universe = QComboBox()
        self.universe.addItem("Momentum universe", "momentum")
        self.universe.addItem("Top gainers", "gainers")
        self.universe.addItem("Most active", "active")
        self.universe.addItem("Custom watchlist", "custom")
        self.count = QSpinBox()
        self.count.setRange(5, 200)
        self.count.setSingleStep(5)
        self.count.setValue(50)
        self.custom_symbols = QLineEdit()
        self.custom_symbols.setPlaceholderText("SDOT LUCY REAX …")
        self.custom_symbols.setEnabled(False)
        self.universe.currentIndexChanged.connect(self._sync_custom_state)
        self.scan = QPushButton("Scan for strategy matches")
        self.scan.setObjectName("Primary")
        # Keep the native clicked(bool) signature explicit across packaged Qt
        # bindings before forwarding to the custom payload signal.
        self.scan.clicked.connect(self._scan_clicked)

        controls.addWidget(QLabel("Strategy rules"), 0, 0)
        controls.addWidget(self.strategy, 0, 1, 1, 3)
        controls.addWidget(self.include_research, 0, 4)
        controls.addWidget(QLabel("Stocks to scan"), 1, 0)
        controls.addWidget(self.universe, 1, 1)
        controls.addWidget(QLabel("Count"), 1, 2)
        controls.addWidget(self.count, 1, 3)
        controls.addWidget(self.scan, 1, 4)
        controls.addWidget(QLabel("Custom tickers"), 2, 0)
        controls.addWidget(self.custom_symbols, 2, 1, 1, 4)
        controls.setColumnStretch(1, 1)
        root.addWidget(controls_card)

        self.banner = Card()
        banner = QVBoxLayout(self.banner)
        self.status = QLabel("Choose a strategy scope and stock universe.")
        self.status.setObjectName("BannerTitle")
        self.detail = QLabel(
            "This is the web app's Market Discovery direction: strategy rules → matching stocks."
        )
        self.detail.setObjectName("Subtle")
        self.detail.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        banner.addWidget(self.status)
        banner.addWidget(self.detail)
        banner.addWidget(self.progress)
        root.addWidget(self.banner)

        metrics = QGridLayout()
        self.match_metric = MetricCard("Strong matches")
        self.validated_metric = MetricCard("Validated matches")
        self.stock_metric = MetricCard("Stocks evaluated")
        self.strategy_metric = MetricCard("Strategies checked")
        for index, widget in enumerate(
            (
                self.match_metric,
                self.validated_metric,
                self.stock_metric,
                self.strategy_metric,
            )
        ):
            metrics.addWidget(widget, 0, index)
        root.addLayout(metrics)

        results_card = Card()
        results = QVBoxLayout(results_card)
        heading = QHBoxLayout()
        title = QLabel("Live strategy matches")
        title.setObjectName("SectionTitle")
        self.analyze = QPushButton("Analyze selected stock")
        self.analyze.setEnabled(False)
        self.analyze.clicked.connect(self._emit_selected_analysis)
        heading.addWidget(title)
        heading.addStretch(1)
        heading.addWidget(self.analyze)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                "Stock",
                "Best current match",
                "Validation",
                "Setup",
                "Rule match",
                "Price",
                "Day move",
                "RVOL",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (0, 2, 3, 4, 5, 6, 7):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.table.setMinimumHeight(230)
        self.table.itemSelectionChanged.connect(self._sync_selection)
        self.table.cellDoubleClicked.connect(
            lambda _row, _column: self._emit_selected_analysis()
        )
        results.addLayout(heading)
        results.addWidget(self.table, 1)
        root.addWidget(results_card, 1)

        safety = QLabel(
            "Research only · A rule match is not proof of profitability or a trade recommendation. "
            "Unvalidated strategies remain clearly labeled."
        )
        safety.setObjectName("Subtle")
        safety.setWordWrap(True)
        root.addWidget(safety)

    def _sync_custom_state(self) -> None:
        custom = str(self.universe.currentData() or "") == "custom"
        self.custom_symbols.setEnabled(custom and self.scan.isEnabled())
        maximum = 200 if custom else (50 if self.universe.currentData() == "gainers" else 100)
        self.count.setMaximum(maximum)
        if self.count.value() > maximum:
            self.count.setValue(maximum)

    def render_options(self, result: dict[str, Any]) -> None:
        # Loading options uses set_working(), which disables every scan control.
        # Restore the complete control set before rendering the ready state.
        self._set_controls_enabled(True)
        selected = str(self.strategy.currentData() or "")
        self.strategy.clear()
        self.strategy.addItem("All usable strategy families", "")
        strategies = [
            item for item in result.get("strategies") or [] if isinstance(item, dict)
        ]
        for item in strategies:
            name = str(item.get("name") or "Unnamed strategy")
            validation = str(item.get("validation_status") or "research only").replace("_", " ")
            self.strategy.addItem(f"{name} · {validation}", str(item.get("id") or ""))
        index = self.strategy.findData(selected)
        self.strategy.setCurrentIndex(max(0, index))
        self.options_loaded = True
        self.status.setText(f"{len(strategies):,} faithful strategy families ready")
        blocked = int(result.get("blocked_count") or 0)
        self.detail.setText(
            "Choose one strategy or scan every usable family. "
            f"{blocked:,} low-fidelity families remain excluded."
        )
        self.progress.setValue(0)
        self.progress.setVisible(False)

    @Slot(bool)
    def _scan_clicked(self, _checked: bool = False) -> None:
        """Acknowledge the native button event before dispatching the scan."""

        self.status.setText("Starting Find Stocks scan…")
        self.detail.setText(
            "Sending the selected strategy rules and stock universe to the local service."
        )
        self.emit_run()

    def emit_run(self) -> None:
        if str(self.universe.currentData() or "") == "custom":
            custom = self.custom_symbols.text().strip()
            if not custom:
                self.set_error("Enter at least one ticker for the custom watchlist.")
                return
        self.run_requested.emit(
            {
                "strategy_id": str(self.strategy.currentData() or ""),
                "include_research": self.include_research.isChecked(),
                "universe": str(self.universe.currentData() or "momentum"),
                "candidate_count": int(self.count.value()),
                "custom_symbols": self.custom_symbols.text().strip(),
            }
        )

    def set_working(self, title: str, detail: str, fraction: float) -> None:
        self.scan.setEnabled(False)
        self.refresh_options.setEnabled(False)
        self.strategy.setEnabled(False)
        self.universe.setEnabled(False)
        self.count.setEnabled(False)
        self.include_research.setEnabled(False)
        self.custom_symbols.setEnabled(False)
        self.banner.setProperty("state", "working")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText(title)
        self.detail.setText(detail)
        self.progress.setVisible(True)
        self.progress.setValue(round(max(0.0, min(1.0, fraction)) * 1000))

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.scan.setEnabled(enabled)
        self.refresh_options.setEnabled(enabled)
        self.strategy.setEnabled(enabled)
        self.universe.setEnabled(enabled)
        self.count.setEnabled(enabled)
        self.include_research.setEnabled(enabled)
        self._sync_custom_state()

    def set_error(self, message: str) -> None:
        self._set_controls_enabled(True)
        self.banner.setProperty("state", "error")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText("Find Stocks could not continue")
        self.detail.setText(message)
        self.progress.setVisible(False)
        self.progress.setValue(0)

    @staticmethod
    def _number(value: Any, *, digits: int = 2, suffix: str = "") -> str:
        try:
            return f"{float(value):,.{digits}f}{suffix}"
        except (TypeError, ValueError, OverflowError):
            return "—"

    def render_results(self, result: dict[str, Any]) -> None:
        self._set_controls_enabled(True)
        self.banner.setProperty("state", "ready")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self._results = [
            item for item in result.get("results") or [] if isinstance(item, dict)
        ]
        self.status.setText(
            f"{result.get('universe_label') or 'Market'} scan complete · "
            f"{len(self._results):,} ranked stocks"
        )
        self.detail.setText(
            "Each stock is paired with its strongest current rule match. "
            "Validated and research-only strategies remain labeled separately."
        )
        self.progress.setVisible(True)
        self.progress.setValue(1000)
        self.match_metric.value.setText(f"{int(result.get('match_count') or 0):,}")
        self.validated_metric.value.setText(
            f"{int(result.get('validated_match_count') or 0):,}"
        )
        self.stock_metric.value.setText(f"{len(self._results):,}")
        self.strategy_metric.value.setText(f"{int(result.get('strategy_count') or 0):,}")

        self.table.setRowCount(len(self._results))
        for row, item in enumerate(self._results):
            metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
            values = (
                str(item.get("symbol") or "—"),
                str(item.get("best_strategy_name") or "—"),
                str(item.get("validation_status") or "unvalidated").replace("_", " ").title(),
                str(item.get("status") or "UNKNOWN").upper(),
                self._number(item.get("score"), digits=0, suffix="%"),
                "$" + self._number(metrics.get("price"), digits=4),
                self._number(metrics.get("day_change_pct"), digits=2, suffix="%"),
                self._number(metrics.get("relative_volume"), digits=2, suffix="×"),
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column != 1:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, cell)
        self.table.clearSelection()
        self._sync_selection()

    def _selected_symbol(self) -> str:
        rows = sorted({item.row() for item in self.table.selectedItems()})
        if not rows or rows[0] >= len(self._results):
            return ""
        return str(self._results[rows[0]].get("symbol") or "").strip().upper()

    def _sync_selection(self) -> None:
        self.analyze.setEnabled(bool(self._selected_symbol()))

    def _emit_selected_analysis(self) -> None:
        symbol = self._selected_symbol()
        if symbol:
            self.analyze_requested.emit(symbol)


__all__ = ["MarketDiscoveryPage"]
