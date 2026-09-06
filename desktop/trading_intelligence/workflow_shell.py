"""Web-style layout around existing native pages; never an alternate execution path."""
from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QComboBox, QLineEdit, QTableWidget, QScrollArea, QSizePolicy,
)
from .display_time import format_timestamp
from .workflow_widgets import Disclosure, WorkflowPageStack, PageWheelRouter, readable_table, ResponsiveAnalysisToolbar
from .workflow_style import STYLESHEET


def label(text, name="Subtle"):
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


class WorkflowShell(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.ticker = ""
        self.strategy_id = ""
        self.strategy_name = ""
        self.library_state = "Not recorded"
        self.snapshot = {}
        self.saved = {}
        self._syncing = False
        self._ensure_scroll_stack()
        window.setMinimumSize(900, 640)
        window.setStyleSheet(window.styleSheet() + STYLESHEET)
        self.routes = [
            ("Find Stocks", window.market_discovery), ("Analyze", window.analysis),
            ("Strategy", window.finder), ("Validation", window.strategy_lab),
            ("Results", window.results), ("Research", window.research_ml),
        ]
        self._navigation()
        self._header()
        self._pages()
        self.wheel = PageWheelRouter(window)
        window.market_discovery.table.itemSelectionChanged.connect(self._selected_stock)
        window.analysis.symbol.editingFinished.connect(lambda: self._edited_ticker(window.analysis.symbol))
        window.finder.symbol.editingFinished.connect(lambda: self._edited_ticker(window.finder.symbol))
        window.strategy_lab.ticker.editingFinished.connect(lambda: self._edited_ticker(window.strategy_lab.ticker))
        window.strategy_lab.strategy.activated.connect(self._selected_strategy)
        window.saved_validation.loaded.connect(self._saved_loaded)
        window.stack.currentChanged.connect(self.refresh)
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def _ensure_scroll_stack(self):
        w = self.window
        if hasattr(w.stack, "scroll_area"):
            return
        old = w.stack
        index = old.currentIndex()
        pages = [old.widget(i) for i in range(old.count())]
        new = WorkflowPageStack()
        parent = old.parentWidget()
        parent.layout().replaceWidget(old, new)
        for page in pages:
            old.removeWidget(page)
            new.addWidget(page)
        w.stack = new
        new.setCurrentIndex(index)
        old.hide()
        old.deleteLater()

    def _navigation(self):
        w = self.window
        sidebar = w.nav_buttons[0].parentWidget()
        self.sidebar = sidebar
        sidebar.setFixedWidth(190)
        if isinstance(sidebar.parentWidget(), QWidget) and isinstance(sidebar.parentWidget().parentWidget(), QScrollArea):
            sidebar.parentWidget().parentWidget().setFixedWidth(206)
        layout = sidebar.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().hide()
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(4)
        layout.addWidget(label("Trading\nIntelligence Lab", "SectionTitle"))
        layout.addWidget(label("RESEARCH WORKFLOW", "Eyebrow"))
        self.navigation = []
        for caption, page in self.routes:
            button = QPushButton(caption)
            button.setAccessibleName(caption)
            button.setObjectName("WorkflowNav")
            button.clicked.connect(lambda _=False, p=page: self.go(p))
            layout.addWidget(button)
            self.navigation.append((button, page))
        utility = QWidget()
        utility_layout = QVBoxLayout(utility)
        utility_layout.setContentsMargins(0, 0, 0, 0)
        primary_pages = {page for _, page in self.routes}
        for index, button in enumerate(w.nav_buttons):
            page_index = button.property("stack_index")
            page = w.stack.widget(int(page_index) if page_index is not None else index)
            if page not in primary_pages:
                copy = QPushButton(str(button.property("nav_caption") or button.text()).replace("&&", "&"))
                copy.setObjectName("WorkflowNav")
                copy.clicked.connect(lambda _=False, p=page: self.go(p))
                utility_layout.addWidget(copy)
                self.navigation.append((copy, page))
        self.utilities = Disclosure("Tools & connections", utility)
        layout.addWidget(self.utilities)
        layout.addStretch(1)
        layout.addWidget(label("Research only.\nNo trading approval.", "Subtle"))

    def _header(self):
        w = self.window
        self.header = QWidget()
        layout = QVBoxLayout(self.header)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(4)
        top = QHBoxLayout()
        self.location = label("Find Stocks", "Eyebrow")
        self.next = QPushButton("Select a stock")
        self.next.setObjectName("Primary")
        self.next.clicked.connect(self.advance)
        top.addWidget(self.location, 1)
        top.addWidget(self.next)
        layout.addLayout(top)
        self.context_ticker = label("No stock selected", "ContextTicker")
        self.context_strategy = label("Choose a stock in Find Stocks to carry its strategy through the workflow.", "ContextStrategy")
        self.context_status = label("Stock-specific evidence: no saved result opened")
        layout.addWidget(self.context_ticker)
        layout.addWidget(self.context_strategy)
        layout.addWidget(self.context_status)
        w.stack.parentWidget().layout().insertWidget(0, self.header)
        w.top_status.setWordWrap(True)

    def _disclose_body(self, page, caption, keep):
        layout = page.layout()
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 8, 0, 0)
        for index in range(layout.count() - 1, 0, -1):
            item = layout.itemAt(index)
            if item.widget() in keep:
                continue
            item = layout.takeAt(index)
            if item.widget():
                content.insertWidget(0, item.widget())
            elif item.layout():
                child_layout = item.layout()
                child_layout.setParent(None)
                content.insertLayout(0, child_layout)
            else:
                content.insertItem(0, item)
        disclosure = Disclosure(caption, body)
        layout.addWidget(disclosure)
        return disclosure

    def _pages(self):
        w = self.window
        # Existing mixed backtest controllers remain local and unreachable here.
        for _, panel in w.search_monitor.panels:
            if hasattr(panel, "open_result"):
                panel.open_result.hide()
                panel.open_result.setEnabled(False)
                panel.open_result.clicked.disconnect()
                panel.result_requested.disconnect()
                panel.table.cellClicked.disconnect()
                panel.table.itemActivated.disconnect()
            panel.table.setColumnHidden(4, True)  # Stage remains in the selected-run detail.
            panel.table.itemSelectionChanged.connect(lambda p=panel: self._run_detail(p))
        # Move the existing Research monitor to Results. Its polling/read behavior is unchanged.
        for i, (page, panel) in enumerate(w.search_monitor.panels):
            if page is w.research_ml:
                page.layout().removeWidget(panel)
                w.results.layout().insertWidget(1, panel)
                w.search_monitor.panels[i] = (w.results, panel)
                self.result_panel = panel
                break
        for panel, button in w.saved_validation.buttons:
            button.setObjectName("Primary")
        self.library_details = self._disclose_body(w.results, "Library-wide evidence (separate from this stock's result)", {self.result_panel})
        self.validation_settings = self._disclose_body(w.strategy_lab, "Configure a new validation (starts cloud compute)", {w.strategy_lab.banner, *[p for page, p in w.search_monitor.panels if page is w.strategy_lab]})
        self.strategy_details = self._disclose_body(w.finder, "Optional: search other strategies (starts cloud research)", set())
        self.strategy_summary = label("Select a stock to inspect its matched strategy.", "ContextStrategy")
        w.finder.layout().insertWidget(1, self.strategy_summary)
        w.research_ml.layout().insertWidget(1, label("RESEARCH CONTEXT ONLY\nIdeas and hypotheses are not holdout, walk-forward or validated trading evidence.", "SectionTitle"))
        # Keep source controls and their callbacks, but avoid a five-column scan form.
        d = w.market_discovery
        grid = d.strategy.parentWidget().layout()
        while grid.count():
            item = grid.takeAt(0)
            if isinstance(item.widget(), QLabel):
                item.widget().hide()
        grid.addWidget(label("Strategy scope"), 0, 0)
        grid.addWidget(d.strategy, 0, 1, 1, 3)
        grid.addWidget(d.include_research, 1, 1, 1, 3)
        grid.addWidget(label("Universe"), 2, 0)
        grid.addWidget(d.universe, 2, 1)
        grid.addWidget(label("Count"), 2, 2)
        grid.addWidget(d.count, 2, 3)
        grid.addWidget(label("Custom tickers"), 3, 0)
        grid.addWidget(d.custom_symbols, 3, 1, 1, 3)
        grid.addWidget(d.scan, 4, 1, 1, 3)
        d.table.horizontalHeaderItem(2).setText("Library status")
        d.table.setColumnHidden(6, True)
        d.table.setColumnHidden(7, True)
        self.discovery_detail = label("Select a stock, then Analyze. A rule match is not a validation verdict.")
        d.table.parentWidget().layout().addWidget(self.discovery_detail)
        for _, page in self.routes:
            for text in page.findChildren(QLabel):
                text.setWordWrap(True)
            for combo in page.findChildren(QComboBox):
                combo.setMinimumWidth(0)
                combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                combo.setMinimumContentsLength(12)
                combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            for table in page.findChildren(QTableWidget):
                readable_table(table)
        self.analysis_toolbar = ResponsiveAnalysisToolbar(w.analysis)
        # Six analyzer metrics become two readable rows, without changing values.
        metrics = [w.analysis.price_metric, w.analysis.change_metric, w.analysis.vwap_metric,
                   w.analysis.rvol_metric, w.analysis.atr_metric, w.analysis.cache_metric]
        root = w.analysis.layout()
        for i in range(root.count()):
            grid = root.itemAt(i).layout()
            if isinstance(grid, QGridLayout) and grid.indexOf(metrics[0]) >= 0:
                for metric in metrics:
                    grid.removeWidget(metric)
                for j, metric in enumerate(metrics):
                    grid.addWidget(metric, j // 3, j % 3)
                break

    def _run_detail(self, panel):
        row = panel.selected() or {}
        if hasattr(panel, "open_result"):
            panel.open_result.hide()
            panel.open_result.setEnabled(False)
        if row:
            panel.detail.setToolTip("Exact job: " + str(row.get("id")) + "\nStage: " + str(row.get("stage")))
        self.refresh()

    def _selected_stock(self):
        d = self.window.market_discovery
        row = d.table.currentRow()
        if not d.table.selectedItems() or not 0 <= row < len(d._results):
            return
        item = d._results[row]
        self.snapshot = dict(item)
        self.library_state = str(item.get("validation_status") or "Not recorded").replace("_", " ")
        self.adopt(str(item.get("symbol") or ""), str(item.get("best_strategy_id") or ""), str(item.get("best_strategy_name") or "No matched strategy recorded"))
        metrics = item.get("metrics") or {}
        self.discovery_detail.setText(f"Strategy library status: {self.library_state}. This is not a stock-specific validation verdict.\nDay move: {metrics.get('day_change_pct', 'Not recorded')}% | Relative volume: {metrics.get('relative_volume', 'Not recorded')}")
        d.analyze.setText("Analyze " + self.ticker)

    def adopt(self, ticker, strategy_id, strategy_name):
        self.ticker = ticker.strip().upper()
        self.strategy_id, self.strategy_name = strategy_id, strategy_name
        w = self.window
        self._syncing = True
        try:
            if w.analysis.symbol.isEnabled():
                w.analysis.symbol.setText(self.ticker)
                if self.snapshot.get("symbol", "").upper() == self.ticker:
                    w.analysis.set_discovery_context(self.snapshot)
            if not w.finder_job_id:
                w.finder.symbol.setText(self.ticker)
            if not w.strategy_lab_job_id:
                w.strategy_lab.ticker.setText(self.ticker)
                w.strategy_lab.select_strategy_id(self.strategy_id)
        finally:
            self._syncing = False
        self.refresh()

    def _edited_ticker(self, field):
        if not self._syncing and field.text().strip():
            self.adopt(field.text(), self.strategy_id, self.strategy_name)

    def _selected_strategy(self, *_):
        lab = self.window.strategy_lab
        self.strategy_id = str(lab.strategy.currentData() or "")
        self.strategy_name = lab.strategy.currentText().split(" \u00b7 ")[0]
        self.refresh()

    def _saved_loaded(self, response):
        self.saved[(response["ticker"], tuple(response["strategy_ids"]))] = response
        self.refresh()

    def go(self, page):
        if page is not None:
            self.window.show_page(self.window.stack.indexOf(page))
            self.refresh()

    def advance(self):
        page, w = self.window.stack.currentWidget(), self.window
        if page is w.market_discovery:
            if w.market_discovery.analyze.isEnabled():
                w.market_discovery.analyze.click()
        elif page is w.analysis:
            self.go(w.finder)
        elif page is w.finder:
            self.go(w.strategy_lab)
        else:
            self.go(w.results)

    def refresh(self, *_):
        w = self.window
        page = w.stack.currentWidget()
        caption = next((name for name, p in self.routes if p is page), "Tools & connections")
        self.location.setText("WORKFLOW / " + caption.upper())
        for button, target in self.navigation:
            active = target is page
            if button.property("active") != active:
                button.setProperty("active", active)
                button.style().unpolish(button)
                button.style().polish(button)
        self.context_ticker.setText(self.ticker or "No stock selected")
        self.context_strategy.setText((self.strategy_name + "\nStrategy ID: " + (self.strategy_id or "not selected")) if self.ticker else "Find stocks, select a match, then follow its evidence.")
        response = self.saved.get((self.ticker, (self.strategy_id,)))
        if response:
            result = response.get("result") or {}
            state = "Infrastructure failure; no strategy verdict" if response["status"] == "failed" else str((result.get("evidence_verdict") or {}).get("label") or "No saved verdict")
            self.context_status.setText(f"{self.ticker} saved validation: {state}")
        else:
            self.context_status.setText("Stock-specific evidence: no saved result opened for this selection")
        if self.snapshot.get("symbol", "").upper() == self.ticker:
            metrics = self.snapshot.get("metrics") or {}
            stamp = format_timestamp(metrics.get("trade_timestamp") or metrics.get("quote_timestamp"), "time not recorded")
            price = metrics.get("price")
            self.context_ticker.setToolTip(f"Discovery snapshot price: {price if price is not None else 'not recorded'}\nAs of {stamp}. Not a live quote.")
        self.strategy_summary.setText(f"{self.strategy_name or 'No strategy selected'}\nStrategy ID: {self.strategy_id or 'not selected'}\nStrategy library status: {self.library_state} (not a ticker-specific validation verdict).\n\n" + (w.analysis.signal_summary.text() if self.snapshot.get("symbol", "").upper() == self.ticker else "No saved match explanation for this ticker."))
        self.next.setText("Analyze " + self.ticker if page is w.market_discovery and self.ticker else
                          "View Strategy" if page is w.analysis else
                          "View Validation" if page is w.finder else
                          "View Results" if page is not w.results else "Select a saved run below")
        self.next.setEnabled(bool(self.ticker) and page is not w.results and
                             (page is not w.market_discovery or w.market_discovery.analyze.isEnabled()))
        if w.finder_job_id:
            self.strategy_details.set_expanded(True)
