"""Real-market quick-analysis page for the Trading Intelligence desktop."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from .chart import CandleChart
from .pages import Card, MetricCard


class AnalysisPage(QWidget):
    analyze_requested = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        header = QHBoxLayout()
        copy = QVBoxLayout()
        eyebrow = QLabel("QUICK ANALYSIS")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Fast local chart and market context")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "The first request refreshes Alpaca history. Repeated requests reuse a persistent local cache "
            "and download only the newest overlap before updating indicators."
        )
        subtitle.setObjectName("Subtle")
        subtitle.setWordWrap(True)
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        header.addLayout(copy, 1)
        root.addLayout(header)

        controls_card = Card()
        controls = QHBoxLayout(controls_card)
        controls.setContentsMargins(14, 12, 14, 12)
        self.symbol = QLineEdit()
        self.symbol.setPlaceholderText("Ticker, e.g. SDOT")
        self.symbol.setMaxLength(10)
        self.symbol.setFixedWidth(170)
        self.symbol.returnPressed.connect(self.emit_analysis)
        self.timeframe = QComboBox()
        self.timeframe.addItem("1 minute", "1Min")
        self.timeframe.addItem("5 minutes", "5Min")
        self.timeframe.addItem("15 minutes", "15Min")
        self.timeframe.addItem("1 hour", "1Hour")
        self.timeframe.setCurrentIndex(1)
        self.vwap = QCheckBox("VWAP")
        self.vwap.setChecked(True)
        self.ema = QCheckBox("EMA 9")
        self.ema.setChecked(True)
        self.run = QPushButton("Analyze")
        self.run.setObjectName("Primary")
        self.run.clicked.connect(self.emit_analysis)
        controls.addWidget(QLabel("Symbol"))
        controls.addWidget(self.symbol)
        controls.addWidget(QLabel("Candles"))
        controls.addWidget(self.timeframe)
        controls.addSpacing(8)
        controls.addWidget(self.vwap)
        controls.addWidget(self.ema)
        controls.addStretch(1)
        controls.addWidget(self.run)
        root.addWidget(controls_card)

        self.banner = Card()
        banner = QVBoxLayout(self.banner)
        self.status = QLabel("Enter a ticker to load real market data.")
        self.status.setObjectName("BannerTitle")
        self.detail = QLabel(
            "Prices are labeled by candle timestamp so cached or delayed data is never presented as a live quote."
        )
        self.detail.setObjectName("Subtle")
        self.detail.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        banner.addWidget(self.status)
        banner.addWidget(self.detail)
        banner.addWidget(self.progress)
        root.addWidget(self.banner)

        metrics = QGridLayout()
        self.price_metric = MetricCard("Latest candle")
        self.change_metric = MetricCard("Session change")
        self.vwap_metric = MetricCard("VWAP")
        self.rvol_metric = MetricCard("RVOL 20")
        self.atr_metric = MetricCard("ATR 14")
        self.cache_metric = MetricCard("Cache")
        for index, widget in enumerate(
            (
                self.price_metric,
                self.change_metric,
                self.vwap_metric,
                self.rvol_metric,
                self.atr_metric,
                self.cache_metric,
            )
        ):
            metrics.addWidget(widget, 0, index)
        root.addLayout(metrics)

        chart_card = Card()
        chart_layout = QVBoxLayout(chart_card)
        chart_top = QHBoxLayout()
        self.chart_title = QLabel("Market chart")
        self.chart_title.setObjectName("SectionTitle")
        self.structure = QLabel("Support / resistance will appear after analysis")
        self.structure.setObjectName("Subtle")
        reset = QPushButton("Reset chart")
        reset.clicked.connect(self._reset_chart)
        chart_top.addWidget(self.chart_title)
        chart_top.addWidget(self.structure, 1, Qt.AlignmentFlag.AlignRight)
        chart_top.addWidget(reset)
        self.chart = CandleChart()
        chart_layout.addLayout(chart_top)
        chart_layout.addWidget(self.chart, 1)
        root.addWidget(chart_card, 1)

        self.vwap.toggled.connect(self._toggle_indicators)
        self.ema.toggled.connect(self._toggle_indicators)

    def _toggle_indicators(self) -> None:
        self.chart.show_vwap = self.vwap.isChecked()
        self.chart.show_ema = self.ema.isChecked()
        self.chart.update()

    def _reset_chart(self) -> None:
        self.chart.reset_view()

    def emit_analysis(self) -> None:
        symbol = self.symbol.text().strip().upper()
        if not symbol:
            self.set_error("Enter a ticker first.")
            return
        self.symbol.setText(symbol)
        self.analyze_requested.emit(
            {
                "symbol": symbol,
                "timeframe": str(self.timeframe.currentData() or "5Min"),
                "history_days": 20,
                "max_cache_age_seconds": 20,
            }
        )

    def set_working(self, stage: str, detail: str, progress: float) -> None:
        self.run.setEnabled(False)
        self.symbol.setEnabled(False)
        self.timeframe.setEnabled(False)
        self.banner.setProperty("state", "working")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText(stage)
        self.detail.setText(detail)
        self.progress.setValue(round(max(0.0, min(1.0, progress)) * 1000))

    def set_error(self, message: str) -> None:
        self.run.setEnabled(True)
        self.symbol.setEnabled(True)
        self.timeframe.setEnabled(True)
        self.banner.setProperty("state", "error")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText("Analysis could not load")
        self.detail.setText(message)
        self.progress.setValue(0)

    @staticmethod
    def _money(value: Any) -> str:
        try:
            return f"${float(value):,.2f}"
        except (TypeError, ValueError, OverflowError):
            return "—"

    @staticmethod
    def _number(value: Any, suffix: str = "") -> str:
        try:
            return f"{float(value):,.2f}{suffix}"
        except (TypeError, ValueError, OverflowError):
            return "—"

    def render_analysis(self, result: dict[str, Any]) -> None:
        self.run.setEnabled(True)
        self.symbol.setEnabled(True)
        self.timeframe.setEnabled(True)
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        cache = result.get("cache") if isinstance(result.get("cache"), dict) else {}
        candles = [item for item in result.get("candles") or [] if isinstance(item, dict)]
        symbol = str(result.get("symbol") or self.symbol.text()).upper()
        timeframe = str(result.get("timeframe") or self.timeframe.currentData() or "")
        feed = str(result.get("feed") or "").upper()
        age = float(summary.get("data_age_seconds") or 0.0)
        self.banner.setProperty("state", "ready")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText(f"{symbol} ready · {timeframe} · {feed}")
        network = bool(cache.get("network_request"))
        provider_rows = int(cache.get("provider_rows") or 0)
        cache_copy = (
            "Fresh cache reused with no Alpaca request."
            if not network
            else f"Incremental Alpaca refresh merged {provider_rows:,} returned candles into the persistent cache."
        )
        self.detail.setText(
            f"{result.get('price_label') or 'Latest completed candle'} · as of {summary.get('as_of') or 'unknown'} · "
            f"data age {age:,.0f}s. {cache_copy}"
        )
        self.progress.setValue(1000)
        self.price_metric.value.setText(self._money(summary.get("latest_bar_close")))
        self.change_metric.value.setText(self._number(summary.get("session_change_pct"), "%"))
        self.vwap_metric.value.setText(self._money(summary.get("vwap")))
        self.rvol_metric.value.setText(self._number(summary.get("rvol_20"), "×"))
        self.atr_metric.value.setText(self._money(summary.get("atr_14")))
        self.cache_metric.value.setText("Reused" if not network else "Updated")
        self.chart_title.setText(f"{symbol} · {timeframe} · {len(candles):,} cached candles")
        self.structure.setText(
            f"Support 20 {self._money(summary.get('support_20'))} · "
            f"Resistance 20 {self._money(summary.get('resistance_20'))}"
        )
        self.chart.set_candles(candles)
        self._toggle_indicators()
