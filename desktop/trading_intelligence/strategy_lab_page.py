"""Native Strategy Lab controls backed by the real cloud executor."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .pages import Card, MetricCard


def _metric(value: Any, *, money: bool = False, suffix: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return "—"
    if money:
        return f"${number:,.2f}"
    return f"{number:,.1f}{suffix}"


class StrategyLabPage(QWidget):
    options_requested = Signal()
    run_requested = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        header = QHBoxLayout()
        copy = QVBoxLayout()
        eyebrow = QLabel("STRATEGY LAB")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Optimize + validate in cloud")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "The cloud worker re-loads the selected strategy from the authoritative library, "
            "re-checks fidelity, and runs the existing deterministic Strategy Lab executor. "
            "The run can continue after this app or your Mac closes."
        )
        subtitle.setObjectName("Subtle")
        subtitle.setWordWrap(True)
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        self.refresh_options = QPushButton("Refresh Strategies")
        self.refresh_options.clicked.connect(self.options_requested.emit)
        header.addLayout(copy, 1)
        header.addWidget(self.refresh_options)
        root.addLayout(header)

        self.banner = Card()
        banner_layout = QVBoxLayout(self.banner)
        self.status = QLabel("Loading faithful strategies…")
        self.status.setObjectName("BannerTitle")
        self.detail = QLabel(
            "Only strategies whose current integrity report is Fully Modeled are selectable."
        )
        self.detail.setObjectName("Subtle")
        self.detail.setWordWrap(True)
        self.progress = QDoubleSpinBox()
        self.progress.hide()
        banner_layout.addWidget(self.status)
        banner_layout.addWidget(self.detail)
        root.addWidget(self.banner)

        top = Card()
        form = QFormLayout(top)
        form.setContentsMargins(14, 14, 14, 14)
        form.setSpacing(10)
        self.strategy = QComboBox()
        self.strategy.setMinimumWidth(380)
        self.compare_all = QCheckBox(
            "Compare all faithful strategies and let validation choose the winner"
        )
        self.ticker = QLineEdit("SDOT")
        self.ticker.setMaxLength(10)
        self.timeframe = QComboBox()
        for label in ("1Min", "5Min", "15Min"):
            self.timeframe.addItem(label, label)
        self.timeframe.setCurrentIndex(1)
        self.depth = QComboBox()
        for label, value in (
            ("Quick · 12 base variants", 12),
            ("Balanced · 36 base variants", 36),
            ("Deep · 96 base variants", 96),
            ("Very Deep · 160 base variants", 160),
        ):
            self.depth.addItem(label, value)
        self.depth.setCurrentIndex(1)
        self.history_days = QSpinBox()
        self.history_days.setRange(7, 180)
        self.history_days.setValue(30)
        form.addRow("Strategy", self.strategy)
        form.addRow("", self.compare_all)
        form.addRow("Stock ticker", self.ticker)
        form.addRow("Candle size", self.timeframe)
        form.addRow("Optimization depth", self.depth)
        form.addRow("Historical calendar days", self.history_days)
        root.addWidget(top)

        advanced = Card()
        advanced_grid = QGridLayout(advanced)
        advanced_grid.setContentsMargins(14, 14, 14, 14)
        advanced_grid.setSpacing(10)
        self.starting_cash = QDoubleSpinBox()
        self.starting_cash.setRange(1000.0, 1_000_000.0)
        self.starting_cash.setValue(2000.0)
        self.starting_cash.setSingleStep(1000.0)
        self.risk = QDoubleSpinBox()
        self.risk.setRange(0.1, 10.0)
        self.risk.setDecimals(1)
        self.risk.setValue(10.0)
        self.position = QDoubleSpinBox()
        self.position.setRange(1.0, 100.0)
        self.position.setDecimals(1)
        self.position.setValue(100.0)
        self.drawdown = QDoubleSpinBox()
        self.drawdown.setRange(1.0, 20.0)
        self.drawdown.setDecimals(1)
        self.drawdown.setValue(15.0)
        advanced_grid.addWidget(QLabel("Starting simulation cash ($)"), 0, 0)
        advanced_grid.addWidget(self.starting_cash, 1, 0)
        advanced_grid.addWidget(QLabel("Risk budget / trade (%)"), 0, 1)
        advanced_grid.addWidget(self.risk, 1, 1)
        advanced_grid.addWidget(QLabel("Maximum total position (%)"), 0, 2)
        advanced_grid.addWidget(self.position, 1, 2)
        advanced_grid.addWidget(QLabel("Drawdown ceiling (%)"), 0, 3)
        advanced_grid.addWidget(self.drawdown, 1, 3)

        self.training = QDoubleSpinBox()
        self.training.setRange(0.40, 0.75)
        self.training.setSingleStep(0.05)
        self.training.setDecimals(2)
        self.training.setValue(0.60)
        self.validation = QDoubleSpinBox()
        self.validation.setRange(0.10, 0.35)
        self.validation.setSingleStep(0.05)
        self.validation.setDecimals(2)
        self.validation.setValue(0.20)
        self.min_train = QSpinBox()
        self.min_train.setRange(1, 50)
        self.min_train.setValue(5)
        self.min_validation = QSpinBox()
        self.min_validation.setRange(1, 25)
        self.min_validation.setValue(2)
        advanced_grid.addWidget(QLabel("Training share"), 2, 0)
        advanced_grid.addWidget(self.training, 3, 0)
        advanced_grid.addWidget(QLabel("Validation share"), 2, 1)
        advanced_grid.addWidget(self.validation, 3, 1)
        advanced_grid.addWidget(QLabel("Minimum training trades"), 2, 2)
        advanced_grid.addWidget(self.min_train, 3, 2)
        advanced_grid.addWidget(QLabel("Minimum validation/holdout trades"), 2, 3)
        advanced_grid.addWidget(self.min_validation, 3, 3)

        self.walk_forward = QCheckBox("Run rolling walk-forward re-optimization")
        self.walk_forward.setChecked(True)
        self.wf_history = QSpinBox()
        self.wf_history.setRange(5, 60)
        self.wf_history.setValue(8)
        self.wf_test = QSpinBox()
        self.wf_test.setRange(1, 10)
        self.wf_test.setValue(2)
        self.wf_folds = QSpinBox()
        self.wf_folds.setRange(2, 6)
        self.wf_folds.setValue(3)
        advanced_grid.addWidget(self.walk_forward, 4, 0, 1, 2)
        advanced_grid.addWidget(QLabel("Prior sessions / fold"), 4, 2)
        advanced_grid.addWidget(self.wf_history, 5, 2)
        advanced_grid.addWidget(QLabel("Unseen sessions / fold"), 4, 3)
        advanced_grid.addWidget(self.wf_test, 5, 3)
        advanced_grid.addWidget(QLabel("Walk-forward folds"), 6, 2)
        advanced_grid.addWidget(self.wf_folds, 7, 2)
        root.addWidget(advanced)

        self.run = QPushButton("Run Strategy Lab in Cloud")
        self.run.setObjectName("Primary")
        self.run.setMinimumHeight(44)
        self.run.clicked.connect(self._emit_run)
        root.addWidget(self.run)

        metrics = QGridLayout()
        self.holdout = MetricCard("Holdout P/L")
        self.validation_metric = MetricCard("Validation P/L")
        self.strength = MetricCard("Validation strength")
        self.verdict = MetricCard("Evidence verdict")
        for index, widget in enumerate(
            (self.holdout, self.validation_metric, self.strength, self.verdict)
        ):
            metrics.addWidget(widget, 0, index)
        root.addLayout(metrics)

        self.result_card = Card()
        result_layout = QVBoxLayout(self.result_card)
        self.result_title = QLabel("No Strategy Lab result loaded yet.")
        self.result_title.setObjectName("BannerTitle")
        self.result_detail = QLabel(
            "Completed cloud results are summarized here; full evidence remains in durable checkpoint storage and Results."
        )
        self.result_detail.setObjectName("Subtle")
        self.result_detail.setWordWrap(True)
        result_layout.addWidget(self.result_title)
        result_layout.addWidget(self.result_detail)
        root.addWidget(self.result_card)
        root.addStretch(1)

    def set_options(self, result: dict[str, Any]) -> None:
        current = self.strategy.currentData()
        self.strategy.clear()
        for item in result.get("strategies") or []:
            if not isinstance(item, dict):
                continue
            strategy_id = str(item.get("id") or "")
            if not strategy_id:
                continue
            source = str(item.get("source_title") or item.get("source_type") or "research")
            label = f"{item.get('name') or 'Unnamed strategy'} · {source}"
            self.strategy.addItem(label, strategy_id)
        if current:
            index = self.strategy.findData(current)
            if index >= 0:
                self.strategy.setCurrentIndex(index)
        faithful = int(result.get("faithful_count") or 0)
        blocked = int(result.get("blocked_count") or 0)
        self.status.setText(f"{faithful:,} faithful strategies available")
        self.detail.setText(
            f"{blocked:,} strategy records are excluded because current source logic is not fully modeled. "
            "The cloud worker checks this gate again before spending compute."
        )
        self.run.setEnabled(faithful > 0)
        self.refresh_options.setEnabled(True)

    def set_working(self, title: str, detail: str, progress: float = 0.0) -> None:
        self.banner.setProperty("state", "working")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText(title)
        self.detail.setText(detail + (f" · {progress * 100:.0f}%" if progress > 0 else ""))
        self.run.setEnabled(False)
        self.refresh_options.setEnabled(False)

    def set_error(self, message: str) -> None:
        self.banner.setProperty("state", "error")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText("Strategy Lab needs attention")
        self.detail.setText(message)
        self.run.setEnabled(self.strategy.count() > 0)
        self.refresh_options.setEnabled(True)

    def render_result(self, result: dict[str, Any]) -> None:
        self.run.setEnabled(self.strategy.count() > 0)
        self.refresh_options.setEnabled(True)
        winner = str(result.get("winner_strategy_name") or "No winner")
        evidence = result.get("evidence_verdict") if isinstance(result.get("evidence_verdict"), dict) else {}
        strength = result.get("strength") if isinstance(result.get("strength"), dict) else {}
        holdout = result.get("holdout_metrics") if isinstance(result.get("holdout_metrics"), dict) else {}
        validation = result.get("validation_metrics") if isinstance(result.get("validation_metrics"), dict) else {}
        verdict_text = str(evidence.get("label") or evidence.get("code") or evidence.get("status") or "research only")
        strength_text = strength.get("score")
        self.holdout.value.setText(_metric(holdout.get("net_pnl"), money=True))
        self.validation_metric.value.setText(_metric(validation.get("net_pnl"), money=True))
        self.strength.value.setText(_metric(strength_text))
        self.verdict.value.setText(verdict_text[:32])
        self.result_title.setText(f"{result.get('ticker') or 'Stock'} · {winner}")
        self.result_detail.setText(
            f"{result.get('timeframe') or '—'} · {verdict_text}. "
            "This is research evidence, not a live-trading approval."
        )
        self.status.setText("Strategy Lab cloud run complete")
        self.detail.setText("The durable cloud checkpoint was reconciled successfully.")

    def _emit_run(self) -> None:
        strategy_id = str(self.strategy.currentData() or "")
        ticker = self.ticker.text().strip().upper()
        if not ticker:
            self.set_error("Enter a stock ticker first.")
            return
        if not strategy_id and not self.compare_all.isChecked():
            self.set_error("Choose a faithful strategy or enable Compare All.")
            return
        if self.training.value() + self.validation.value() > 0.90:
            self.set_error("Training + validation must leave at least 10% untouched for final holdout.")
            return
        self.run_requested.emit(
            {
                "strategy_ids": [strategy_id] if strategy_id else [],
                "compared_all": self.compare_all.isChecked(),
                "ticker": ticker,
                "timeframe": str(self.timeframe.currentData() or "5Min"),
                "history_days": self.history_days.value(),
                "search_depth": int(self.depth.currentData() or 36),
                "starting_cash": self.starting_cash.value(),
                "risk_per_trade": self.risk.value(),
                "max_position": self.position.value(),
                "max_drawdown": self.drawdown.value(),
                "training_fraction": self.training.value(),
                "validation_fraction": self.validation.value(),
                "minimum_training_trades": self.min_train.value(),
                "minimum_validation_trades": self.min_validation.value(),
                "run_walk_forward": self.walk_forward.isChecked(),
                "wf_history_sessions": self.wf_history.value(),
                "wf_test_sessions": self.wf_test.value(),
                "wf_folds": self.wf_folds.value(),
            }
        )
