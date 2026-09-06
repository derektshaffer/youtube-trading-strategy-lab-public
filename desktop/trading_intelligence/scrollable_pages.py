"""Keep each desktop page readable when its content exceeds the window."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QLayout, QScrollArea, QStackedWidget, QWidget,
)


def scrollable(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setWidgetResizable(True)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    area.setObjectName("PageScroll")
    if widget.layout() is not None:
        widget.layout().setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
    area.setWidget(widget)
    return area


class ScrollablePageStack(QStackedWidget):
    """Preserve page identities/indices while giving each page its own scrollbars."""

    def __init__(self) -> None:
        super().__init__()
        self._areas: dict[QWidget, QScrollArea] = {}

    def addWidget(self, page: QWidget) -> int:
        if page in self._areas:
            return self.indexOf(page)
        area = scrollable(page)
        self._areas[page] = area
        return super().addWidget(area)

    def indexOf(self, page: QWidget) -> int:
        return super().indexOf(self._areas.get(page, page))

    def widget(self, index: int) -> QWidget | None:
        area = super().widget(index)
        return area.widget() if isinstance(area, QScrollArea) else area

    def currentWidget(self) -> QWidget | None:
        return self.widget(self.currentIndex())

    def setCurrentWidget(self, page: QWidget) -> None:
        super().setCurrentWidget(self._areas.get(page, page))

    def scroll_area(self, page: QWidget) -> QScrollArea:
        return self._areas[page]
