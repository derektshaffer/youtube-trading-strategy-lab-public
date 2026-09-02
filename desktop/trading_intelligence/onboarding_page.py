"""Native first-run setup for library, cloud, and market-data credentials."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from hybrid_runtime.desktop_settings import DesktopSettings
from .pages import Card, MetricCard


class OnboardingPage(QWidget):
    save_and_verify_requested = Signal(dict)
    skip_requested = Signal()
    complete_requested = Signal()

    def __init__(self, settings: DesktopSettings, configuration: dict[str, Any]) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        header = QHBoxLayout()
        copy = QVBoxLayout()
        eyebrow = QLabel("FIRST-RUN SETUP")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Connect Trading Intelligence once, then use the app normally")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "Your GitHub and Alpaca credentials are written only to macOS Keychain. "
            "The verification step checks the real research library, private cloud repository, "
            "and selected Alpaca data feed without placing trades."
        )
        subtitle.setObjectName("Subtle")
        subtitle.setWordWrap(True)
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        header.addLayout(copy, 1)
        root.addLayout(header)

        self.banner = Card()
        banner = QVBoxLayout(self.banner)
        self.banner_title = QLabel("Complete the three connections below")
        self.banner_title.setObjectName("BannerTitle")
        self.banner_detail = QLabel(
            "Blank password fields keep any credential already stored in macOS Keychain."
        )
        self.banner_detail.setObjectName("Subtle")
        self.banner_detail.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        banner.addWidget(self.banner_title)
        banner.addWidget(self.banner_detail)
        banner.addWidget(self.progress)
        root.addWidget(self.banner)

        metrics = QGridLayout()
        self.library_metric = MetricCard("Research library", "Needs setup")
        self.cloud_metric = MetricCard("Cloud research", "Needs setup")
        self.market_metric = MetricCard("Market data", "Needs setup")
        metrics.addWidget(self.library_metric, 0, 0)
        metrics.addWidget(self.cloud_metric, 0, 1)
        metrics.addWidget(self.market_metric, 0, 2)
        root.addLayout(metrics)

        library_card = Card()
        library = QGridLayout(library_card)
        library.setHorizontalSpacing(14)
        library.setVerticalSpacing(10)
        library_title = QLabel("1 · Research library")
        library_title.setObjectName("SectionTitle")
        library.addWidget(library_title, 0, 0, 1, 3)
        self.source = QComboBox()
        self.source.addItem("Automatic: local file, then private GitHub", "auto")
        self.source.addItem("Local JSON library", "local_file")
        self.source.addItem("Private GitHub backup", "github_backup")
        self.local_path = QLineEdit()
        self.local_path.setPlaceholderText("Optional local intelligence_library.json")
        browse = QPushButton("Choose…")
        browse.clicked.connect(self.choose_file)
        library.addWidget(QLabel("Source"), 1, 0)
        library.addWidget(self.source, 1, 1, 1, 2)
        library.addWidget(QLabel("Local file"), 2, 0)
        library.addWidget(self.local_path, 2, 1)
        library.addWidget(browse, 2, 2)
        library.setColumnStretch(1, 1)
        root.addWidget(library_card)

        cloud_card = Card()
        cloud = QGridLayout(cloud_card)
        cloud.setHorizontalSpacing(14)
        cloud.setVerticalSpacing(10)
        cloud_title = QLabel("2 · Private GitHub + cloud research")
        cloud_title.setObjectName("SectionTitle")
        cloud.addWidget(cloud_title, 0, 0, 1, 2)
        self.repository = QLineEdit()
        self.branch = QLineEdit()
        self.github_path = QLineEdit()
        self.github_token = QLineEdit()
        self.github_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.github_token.setPlaceholderText("Leave blank to keep existing Keychain token")
        for row, (caption, widget) in enumerate(
            (
                ("Repository", self.repository),
                ("Branch", self.branch),
                ("Library path", self.github_path),
                ("GitHub token", self.github_token),
            ),
            start=1,
        ):
            cloud.addWidget(QLabel(caption), row, 0)
            cloud.addWidget(widget, row, 1)
        cloud.setColumnStretch(1, 1)
        root.addWidget(cloud_card)

        market_card = Card()
        market = QGridLayout(market_card)
        market.setHorizontalSpacing(14)
        market.setVerticalSpacing(10)
        market_title = QLabel("3 · Alpaca market data")
        market_title.setObjectName("SectionTitle")
        market.addWidget(market_title, 0, 0, 1, 2)
        self.alpaca_key = QLineEdit()
        self.alpaca_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.alpaca_key.setPlaceholderText("Leave blank to keep existing Alpaca API key")
        self.alpaca_secret = QLineEdit()
        self.alpaca_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.alpaca_secret.setPlaceholderText("Leave blank to keep existing Alpaca secret")
        self.market_feed = QComboBox()
        self.market_feed.addItem("SIP — consolidated U.S. market feed", "sip")
        self.market_feed.addItem("IEX — Alpaca/IEX feed", "iex")
        market.addWidget(QLabel("API key"), 1, 0)
        market.addWidget(self.alpaca_key, 1, 1)
        market.addWidget(QLabel("Secret key"), 2, 0)
        market.addWidget(self.alpaca_secret, 2, 1)
        market.addWidget(QLabel("Data feed"), 3, 0)
        market.addWidget(self.market_feed, 3, 1)
        market.setColumnStretch(1, 1)
        root.addWidget(market_card)

        self.check_detail = QLabel(
            "Verification has not run yet. It uses AAPL daily bars only as a harmless market-data connectivity probe."
        )
        self.check_detail.setObjectName("Subtle")
        self.check_detail.setWordWrap(True)
        root.addWidget(self.check_detail)

        controls = QHBoxLayout()
        self.skip = QPushButton("Use app for now")
        self.skip.setToolTip(
            "Continue without marking setup complete. Missing cloud or market features may remain unavailable."
        )
        self.skip.clicked.connect(self.skip_requested.emit)
        self.verify = QPushButton("Save securely + verify")
        self.verify.setObjectName("Primary")
        self.verify.clicked.connect(self.emit_save_and_verify)
        self.start = QPushButton("Start Trading Intelligence")
        self.start.setEnabled(False)
        self.start.clicked.connect(self.complete_requested.emit)
        controls.addWidget(self.skip)
        controls.addStretch(1)
        controls.addWidget(self.verify)
        controls.addWidget(self.start)
        root.addLayout(controls)
        root.addStretch(1)

        self.populate(settings, configuration)

    def choose_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose Trading Intelligence research library",
            str(Path.home()),
            "JSON files (*.json);;All files (*)",
        )
        if path:
            self.local_path.setText(path)
            self.source.setCurrentIndex(self.source.findData("local_file"))

    def populate(self, settings: DesktopSettings, configuration: dict[str, Any]) -> None:
        source_index = self.source.findData(settings.library_source)
        self.source.setCurrentIndex(max(0, source_index))
        self.local_path.setText(settings.local_library_path)
        self.repository.setText(settings.github_repository)
        self.branch.setText(settings.github_branch)
        self.github_path.setText(settings.github_path)
        feed_index = self.market_feed.findData(settings.market_feed)
        self.market_feed.setCurrentIndex(max(0, feed_index))
        self.clear_secrets()
        self.render_configuration(configuration)

    def clear_secrets(self) -> None:
        self.github_token.clear()
        self.alpaca_key.clear()
        self.alpaca_secret.clear()

    def settings_payload(self) -> dict[str, Any]:
        return {
            "library_source": str(self.source.currentData() or "auto"),
            "local_library_path": self.local_path.text().strip(),
            "github_repository": self.repository.text().strip(),
            "github_branch": self.branch.text().strip(),
            "github_path": self.github_path.text().strip(),
            "market_feed": str(self.market_feed.currentData() or "sip"),
            "refresh_on_launch": True,
            "_github_token": self.github_token.text().strip(),
            "_alpaca_api_key": self.alpaca_key.text().strip(),
            "_alpaca_secret_key": self.alpaca_secret.text().strip(),
        }

    def emit_save_and_verify(self) -> None:
        self.save_and_verify_requested.emit(self.settings_payload())

    def render_configuration(self, configuration: dict[str, Any]) -> None:
        self.library_metric.value.setText(
            "Configured" if configuration.get("library_configured") else "Needs setup"
        )
        self.cloud_metric.value.setText(
            "Configured" if configuration.get("cloud_configured") else "Needs setup"
        )
        self.market_metric.value.setText(
            "Configured" if configuration.get("market_configured") else "Needs setup"
        )

    def set_verifying(self, stage: str, detail: str, progress: float = 0.0) -> None:
        self.verify.setEnabled(False)
        self.start.setEnabled(False)
        self.banner.setProperty("state", "working")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.banner_title.setText(stage)
        self.banner_detail.setText(detail)
        self.progress.setValue(round(max(0.0, min(1.0, progress)) * 1000))

    def set_error(self, message: str) -> None:
        self.verify.setEnabled(True)
        self.start.setEnabled(False)
        self.banner.setProperty("state", "error")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.banner_title.setText("Setup needs attention")
        self.banner_detail.setText(message)
        self.progress.setValue(0)

    def render_probe(self, result: dict[str, Any]) -> None:
        self.verify.setEnabled(True)
        ready = bool(result.get("ready"))
        checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
        configuration = (
            result.get("configuration")
            if isinstance(result.get("configuration"), dict)
            else {}
        )
        self.render_configuration(configuration)
        library = checks.get("library") if isinstance(checks.get("library"), dict) else {}
        cloud = checks.get("cloud") if isinstance(checks.get("cloud"), dict) else {}
        market = checks.get("market") if isinstance(checks.get("market"), dict) else {}
        self.library_metric.value.setText("Verified" if library.get("ready") else "Attention")
        self.cloud_metric.value.setText("Verified" if cloud.get("ready") else "Attention")
        self.market_metric.value.setText("Verified" if market.get("ready") else "Attention")
        messages = [
            f"Library: {library.get('message') or 'not verified'}",
            f"Cloud: {cloud.get('message') or 'not verified'}",
            f"Market: {market.get('message') or 'not verified'}",
        ]
        self.check_detail.setText("\n".join(messages))
        self.banner.setProperty("state", "ready" if ready else "error")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.banner_title.setText(
            "Setup verified" if ready else "Setup saved · one or more checks need attention"
        )
        self.banner_detail.setText(
            "All three connections are verified. You can start the app."
            if ready
            else "Review the check messages below, correct the affected field, and verify again."
        )
        self.progress.setValue(1000 if ready else 0)
        self.start.setEnabled(ready)
        self.clear_secrets()
