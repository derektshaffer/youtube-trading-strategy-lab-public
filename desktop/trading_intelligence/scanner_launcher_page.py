"""Launcher page for the standalone Momentum Scanner / Stock Analyzer app."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .pages import Card


class ScannerLauncherPage(QWidget):
    open_requested = Signal(str)
    choose_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        eyebrow = QLabel("MOMENTUM SCANNER")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Open the standalone Momentum Scanner")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "The Momentum Scanner and Stock Analyzer remain a separate product and engine, "
            "but you can launch them directly from the Trading Intelligence desktop."
        )
        subtitle.setObjectName("Subtle")
        subtitle.setWordWrap(True)
        root.addWidget(eyebrow)
        root.addWidget(title)
        root.addWidget(subtitle)

        card = Card()
        card_layout = QVBoxLayout(card)
        heading = QLabel("Scanner location")
        heading.setObjectName("SectionTitle")
        instructions = QLabel(
            "Paste the scanner's current Streamlit address, or choose a local Momentum Scanner .app/.command launcher once. "
            "The desktop remembers only this non-secret location."
        )
        instructions.setObjectName("Subtle")
        instructions.setWordWrap(True)
        controls = QHBoxLayout()
        self.target = QLineEdit()
        self.target.setPlaceholderText("https://…streamlit.app or /Applications/Momentum Scanner.app")
        self.target.returnPressed.connect(self.emit_open)
        self.choose = QPushButton("Choose local app…")
        self.choose.clicked.connect(self.choose_requested.emit)
        self.open = QPushButton("Open Momentum Scanner")
        self.open.setObjectName("Primary")
        self.open.clicked.connect(self.emit_open)
        controls.addWidget(self.target, 1)
        controls.addWidget(self.choose)
        controls.addWidget(self.open)
        card_layout.addWidget(heading)
        card_layout.addWidget(instructions)
        card_layout.addLayout(controls)
        root.addWidget(card)

        self.status_card = Card()
        status_layout = QVBoxLayout(self.status_card)
        self.status = QLabel("Scanner and Trading Lab data stay independent.")
        self.status.setObjectName("BannerTitle")
        self.detail = QLabel(
            "Opening the scanner does not copy its rankings into the Lab or weaken either app's validation rules."
        )
        self.detail.setObjectName("Subtle")
        self.detail.setWordWrap(True)
        status_layout.addWidget(self.status)
        status_layout.addWidget(self.detail)
        root.addWidget(self.status_card)
        root.addStretch(1)

    def set_target(self, target: str) -> None:
        self.target.setText(str(target or ""))

    def emit_open(self) -> None:
        self.open_requested.emit(self.target.text().strip())

    def set_error(self, message: str) -> None:
        self.status_card.setProperty("state", "error")
        self.status_card.style().unpolish(self.status_card)
        self.status_card.style().polish(self.status_card)
        self.status.setText("Momentum Scanner could not open")
        self.detail.setText(message)

    def set_opened(self, target: str) -> None:
        self.status_card.setProperty("state", "ready")
        self.status_card.style().unpolish(self.status_card)
        self.status_card.style().polish(self.status_card)
        self.status.setText("Momentum Scanner opened")
        self.detail.setText(
            "The standalone Scanner/Analyzer is running separately. Trading Intelligence remains available here."
        )
        self.target.setText(target)


__all__ = ["ScannerLauncherPage"]
