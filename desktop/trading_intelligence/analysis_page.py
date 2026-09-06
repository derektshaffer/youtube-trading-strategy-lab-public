"""Real-market quick-analysis page for the Trading Intelligence desktop."""

from __future__ import annotations

from typing import Any
from .display_time import format_timestamp

from PySide6.QtCore import Qt, Signal, QTimer
from .market_data_labels import timestamp_label, snapshot_label
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
        self._analysis_result = {}
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

        self.discovery_context = {}
        self.signal_card = Card()
        signal_layout = QVBoxLayout(self.signal_card)
        self.signal_summary = QLabel()
        self.signal_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.signal_summary.setWordWrap(True)
        self.signal_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        signal_layout.addWidget(self.signal_summary)
        root.addWidget(self.signal_card)
        self.signal_card.hide()
        self.symbol.textChanged.connect(self._sync_discovery_context)

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
        self.freshness_timer = QTimer(self)
        self.freshness_timer.setInterval(1000)
        self.freshness_timer.timeout.connect(self._refresh_freshness)
        self.freshness_timer.start()

    def set_discovery_context(self, context: dict[str, Any]) -> None:
        """Display the selected scan's evidence, never recompute or invent rules."""
        self.discovery_context = dict(context)
        self._sync_discovery_context()

    def _sync_discovery_context(self, *_args) -> None:
        if self._analysis_result and str(self._analysis_result.get("symbol") or "").upper() != self.symbol.text().strip().upper():
            self._clear_analysis()
        context = self.discovery_context
        matching = bool(context) and str(context.get("symbol") or "").upper() == self.symbol.text().strip().upper()
        self.signal_card.setVisible(matching)
        if not matching:
            self.signal_summary.clear()
            return
        signal = context.get("signal") or {}
        metrics = context.get("metrics") or {}
        lines = [
            "Current Signal / Why Matched - discovery snapshot",
            f"{context.get('best_strategy_name') or 'Unknown strategy'} | ID: {context.get('best_strategy_id') or 'not recorded'}",
            f"Setup: {signal.get('status') or 'UNKNOWN'} | Rule match: {signal.get('score', 'not recorded')}%",
            f"Library validation status: {context.get('validation_status') or 'not recorded'} (separate from this rule match; inspect saved validation results).",
            "Market snapshot: " + snapshot_label(metrics),
        ]
        for check in signal.get("checks") or []:
            lines.append(f"{check.get('label') or 'Check'}: {check.get('status') or 'unknown'} | actual: {check.get('actual')} | required: {check.get('required')}")
        if not signal.get("checks"):
            lines.append("No rule-check breakdown was saved for this discovery result.")
        lines.append("Saved scan context, not a freshly recomputed signal or a trading approval. Analysis candles below are a separate data view.")
        self.signal_summary.setText("\n".join(lines))

    def _toggle_indicators(self) -> None:
        self.chart.show_vwap = self.vwap.isChecked()
        self.chart.show_ema = self.ema.isChecked()
        self.chart.update()

    def _reset_chart(self) -> None:
        self.chart.reset_view()

    def emit_analysis(self) -> None:
        if not self.run.isEnabled():
            return
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
        self._clear_analysis()
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
        self._clear_analysis()
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
        self._analysis_result = dict(result)
        self.banner.setProperty("state", "ready")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText(f"{symbol} ready · {timeframe} · {feed}")
        network = bool(cache.get("network_request"))
        provider_rows = int(cache.get("provider_rows") or 0)
        cache_copy = (
            "Previously refreshed cache reused with no Alpaca request."
            if not network
            else f"Incremental Alpaca refresh merged {provider_rows:,} returned candles into the persistent cache."
        )
        self._cache_copy = cache_copy
        self._refresh_freshness()
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

    def _clear_analysis(self) -> None:
        self._analysis_result = {}
        for metric in (self.price_metric, self.change_metric, self.vwap_metric,
                       self.rvol_metric, self.atr_metric, self.cache_metric):
            metric.value.setText("—")
        self.chart.set_candles([])
        self.chart_title.setText("Market chart · no current analysis result")
        self.structure.setText("Previous analysis cleared; wait for a successful refresh.")

    def _refresh_freshness(self) -> None:
        result = self._analysis_result
        if result:
            summary = result.get("summary") or {}
            self.detail.setText(
                f"{result.get('price_label') or 'Historical candle close'} · {timestamp_label(summary.get('as_of'))}. "
                f"Historical candle data, not a live quote. {self._cache_copy} {result.get('refresh_warning') or ''}"
            )
        self._sync_discovery_context()
