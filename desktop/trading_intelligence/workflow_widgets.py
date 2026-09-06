"""Shared presentation widgets. No runtime, provider, job or strategy execution."""
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QToolButton, QScrollArea, QFrame,
    QStackedWidget, QAbstractItemView, QAbstractSpinBox, QComboBox,
    QHeaderView, QSizePolicy, QLabel, QGridLayout,
)

from .scrollable_pages import ScrollablePageStack as WorkflowPageStack, scrollable as page_scroll


class Disclosure(QWidget):
    def __init__(self, title, body, *, expanded=False):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setCheckable(True)
        self.toggle.setAccessibleName(title)
        self.body = body
        layout.addWidget(self.toggle)
        layout.addWidget(body)
        self.toggle.toggled.connect(self.set_expanded)
        self.set_expanded(expanded)

    def set_expanded(self, expanded):
        self.toggle.setChecked(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.body.setVisible(expanded)


class PageWheelRouter(QObject):
    """Page-first wheel gestures; Option-wheel or scrollbar is intentional inner scroll."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, QWidget):
            return False
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            return False
        node, inner, area, belongs = watched, False, None, False
        while node is not None:
            if isinstance(node, (QAbstractItemView, QAbstractSpinBox, QComboBox)):
                inner = True
            if isinstance(node, QScrollArea):
                area = node
            if node is self.window:
                belongs = True
                break
            node = node.parentWidget()
        if not belongs or not inner or area is None:
            return False
        # Send to the page viewport, never to the table or an editable control.
        # A page boundary deliberately does not fall through into the table.
        QApplication.sendEvent(area.viewport(), event)
        event.accept()
        return True


def readable_table(table, *, height=260):
    table.setWordWrap(True)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    table.setMinimumHeight(130)
    table.setMaximumHeight(height)
    table.setToolTip("Scroll the page normally. Hold Option while scrolling, use the table scrollbar, or use arrow keys to browse table rows.")
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(True)
    header.setMinimumSectionSize(72)
    for column in range(table.columnCount()):
        item = table.horizontalHeaderItem(column)
        if item:
            width = header.fontMetrics().horizontalAdvance(item.text()) + 38
            table.setColumnWidth(column, max(width, min(table.columnWidth(column), 220)))
    table.verticalHeader().setDefaultSectionSize(38)


class ResponsiveAnalysisToolbar(QObject):
    """Reflow the existing controls; preserve their signals, state and primary action."""
    def __init__(self, page):
        toolbar = page.symbol.parentWidget()
        super().__init__(toolbar)
        self.toolbar = toolbar
        self.page = page
        old = toolbar.layout()
        captions = []
        while old.count():
            item = old.takeAt(0)
            if isinstance(item.widget(), QLabel):
                captions.append(item.widget())
        # Qt transfers the empty old layout, leaving the same toolbar widget free
        # for its responsive grid. No control or connection is recreated.
        holder = QWidget()
        holder.setLayout(old)
        self.grid = QGridLayout(toolbar)
        self.grid.setContentsMargins(14, 12, 14, 12)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)
        holder.deleteLater()
        self.symbol_label, self.candle_label = captions
        page.symbol.setMinimumWidth(110)
        page.symbol.setMaximumWidth(16777215)
        page.symbol.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.narrow = None
        toolbar.installEventFilter(self)
        self.reflow()

    def reflow(self):
        narrow = self.toolbar.width() < 760
        if self.narrow == narrow:
            return
        self.narrow = narrow
        p = self.page
        while self.grid.count():
            self.grid.takeAt(0)
        for column in range(7):
            self.grid.setColumnStretch(column, 0)
        self.grid.addWidget(self.symbol_label, 0, 0)
        if narrow:
            self.grid.addWidget(p.symbol, 0, 1, 1, 4)
            self.grid.addWidget(p.run, 0, 5, 1, 2)
            self.grid.addWidget(self.candle_label, 1, 0)
            self.grid.addWidget(p.timeframe, 1, 1, 1, 3)
            self.grid.addWidget(p.vwap, 1, 4)
            self.grid.addWidget(p.ema, 1, 5, 1, 2)
        else:
            self.grid.addWidget(p.symbol, 0, 1)
            self.grid.addWidget(self.candle_label, 0, 2)
            self.grid.addWidget(p.timeframe, 0, 3)
            self.grid.addWidget(p.vwap, 0, 4)
            self.grid.addWidget(p.ema, 0, 5)
            self.grid.addWidget(p.run, 0, 6)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnStretch(3, 1)

    def eventFilter(self, watched, event):
        if watched is self.toolbar and event.type() == QEvent.Type.Resize:
            self.reflow()
        return False
