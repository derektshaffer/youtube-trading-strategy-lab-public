"""Native Stock Strategy Finder page backed by the existing distributed cloud worker."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
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

from .pages import Card, MetricCard


_PROFILE_HELP = {
    "Quick": "Broad fast pass on the 5-minute timeframe.",
    "Deep": "Default multi-timeframe search across every technically eligible strategy family.",
    "Current Regime": "Recent-behavior search emphasizing roughly the latest month.",
    "Very Deep": "Maximum built-in search depth with the largest optimization and walk-forward workload.",
}


class StockFinderPage(QWidget):
    run_requested = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        header = QHBoxLayout()
        copy = QVBoxLayout()
        eyebrow = QLabel("STOCK STRATEGY FINDER")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Find the strongest strategy for one stock")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "Finder uses the existing strict research engine and distributed cloud workers. "
            "The desktop submits and follows the job; it does not duplicate the optimizer on your Mac."
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
        self.symbol.setMaxLength(12)
        self.symbol.setFixedWidth(170)
        self.symbol.returnPressed.connect(self.emit_run)
        self.profile = QComboBox()
        for name in ("Quick", "Deep", "Current Regime", "Very Deep"):
            self.profile.addItem(name, name)
        self.profile.setCurrentText("Deep")
        self.profile.currentIndexChanged.connect(self._update_profile_help)
        self.run = QPushButton("Run Finder in Cloud")
        self.run.setObjectName("Primary")
        self.run.clicked.connect(self.emit_run)
        controls.addWidget(QLabel("Symbol"))
        controls.addWidget(self.symbol)
        controls.addWidget(QLabel("Search depth"))
        controls.addWidget(self.profile)
        controls.addStretch(1)
        controls.addWidget(self.run)
        root.addWidget(controls_card)

        self.profile_help = QLabel()
        self.profile_help.setObjectName("Subtle")
        self.profile_help.setWordWrap(True)
        root.addWidget(self.profile_help)
        self._update_profile_help()

        self.banner = Card()
        banner = QVBoxLayout(self.banner)
        self.status = QLabel("Choose a stock and search depth.")
        self.status.setObjectName("BannerTitle")
        self.detail = QLabel(
            "Cloud Finder can continue after this app closes. Completed results are restored from the authoritative research library."
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
        self.winner_metric = MetricCard("Winner")
        self.verdict_metric = MetricCard("Verdict")
        self.robustness_metric = MetricCard("Robustness")
        self.config_metric = MetricCard("Configurations")
        self.timeframe_metric = MetricCard("Timeframe")
        for index, widget in enumerate(
            (
                self.winner_metric,
                self.verdict_metric,
                self.robustness_metric,
                self.config_metric,
                self.timeframe_metric,
            )
        ):
            metrics.addWidget(widget, 0, index)
        root.addLayout(metrics)

        result_card = Card()
        result_layout = QVBoxLayout(result_card)
        result_title = QLabel("Validation evidence")
        result_title.setObjectName("SectionTitle")
        self.result_detail = QLabel(
            "Holdout, walk-forward, parameter stability, and execution-fidelity evidence will appear here after Finder completes."
        )
        self.result_detail.setObjectName("Subtle")
        self.result_detail.setWordWrap(True)
        self.result_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        result_layout.addWidget(result_title)
        result_layout.addWidget(self.result_detail)
        root.addWidget(result_card, 1)

        safety = QLabel(
            "Research only · Finder does not bypass strict validation gates and does not place brokerage orders."
        )
        safety.setObjectName("Subtle")
        safety.setWordWrap(True)
        root.addWidget(safety)

    def _update_profile_help(self) -> None:
        name = str(self.profile.currentData() or self.profile.currentText() or "Deep")
        self.profile_help.setText(
            f"{name}: {_PROFILE_HELP.get(name, '')} The search runs on cloud workers so the desktop stays responsive."
        )

    def emit_run(self) -> None:
        symbol = self.symbol.text().strip().upper()
        if not symbol:
            self.set_error("Enter one stock ticker first.")
            return
        self.symbol.setText(symbol)
        self.run_requested.emit(
            {
                "symbol": symbol,
                "profile": str(self.profile.currentData() or "Deep"),
                "continue_after_app_exit": True,
            }
        )

    def set_working(self, title: str, detail: str, fraction: float) -> None:
        self.run.setEnabled(False)
        self.symbol.setEnabled(False)
        self.profile.setEnabled(False)
        self.banner.setProperty("state", "working")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText(title)
        self.detail.setText(detail)
        self.progress.setValue(round(max(0.0, min(1.0, fraction)) * 1000))

    def set_error(self, message: str) -> None:
        self.run.setEnabled(True)
        self.symbol.setEnabled(True)
        self.profile.setEnabled(True)
        self.banner.setProperty("state", "error")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText("Stock Finder could not continue")
        self.detail.setText(message)
        self.progress.setValue(0)

    def set_waiting(self, message: str, fraction: float = 0.01) -> None:
        self.set_working("Stock Finder is waiting for cloud access", message, fraction)

    @staticmethod
    def _number(value: Any, digits: int = 2) -> str:
        try:
            return f"{float(value):,.{digits}f}"
        except (TypeError, ValueError, OverflowError):
            return "—"

    @staticmethod
    def _money(value: Any) -> str:
        try:
            return f"${float(value):,.2f}"
        except (TypeError, ValueError, OverflowError):
            return "—"

    @staticmethod
    def _verdict_text(verdict: dict[str, Any]) -> str:
        return str(
            verdict.get("label")
            or verdict.get("code")
            or verdict.get("status")
            or "research only"
        ).replace("_", " ")

    def render_result(self, result: dict[str, Any]) -> None:
        self.run.setEnabled(True)
        self.symbol.setEnabled(True)
        self.profile.setEnabled(True)
        report = result.get("finder_report") if isinstance(result.get("finder_report"), dict) else {}
        optimization = report.get("optimization") if isinstance(report.get("optimization"), dict) else {}
        winner = optimization.get("winner") if isinstance(optimization.get("winner"), dict) else {}
        robustness = report.get("robustness") if isinstance(report.get("robustness"), dict) else {}
        verdict = report.get("verdict") if isinstance(report.get("verdict"), dict) else {}
        holdout = winner.get("holdout_metrics") if isinstance(winner.get("holdout_metrics"), dict) else {}
        validation = winner.get("validation_metrics") if isinstance(winner.get("validation_metrics"), dict) else {}
        stress = winner.get("stress_metrics") if isinstance(winner.get("stress_metrics"), dict) else {}
        walk = report.get("walk_forward") if isinstance(report.get("walk_forward"), dict) else {}
        walk_summary = walk.get("summary") if isinstance(walk.get("summary"), dict) else {}
        stability = report.get("parameter_stability") if isinstance(report.get("parameter_stability"), dict) else {}
        fidelity = report.get("paper_execution_fidelity") if isinstance(report.get("paper_execution_fidelity"), dict) else {}

        symbol = str(report.get("symbol") or result.get("symbol") or self.symbol.text()).upper()
        profile = str((report.get("profile") or {}).get("name") or result.get("profile") or self.profile.currentData() or "")
        self.banner.setProperty("state", "ready")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText(f"{symbol} · {profile} Finder complete")
        self.detail.setText(
            "The distributed cloud result was restored from the authoritative research library. "
            "Finder evidence remains research-only until the normal strict promotion gates pass."
        )
        self.progress.setValue(1000)
        winner_name = str(report.get("winner_strategy_name") or winner.get("strategy_name") or "No winner")
        self.winner_metric.value.setText(winner_name[:38])
        self.verdict_metric.value.setText(self._verdict_text(verdict).title()[:28])
        self.robustness_metric.value.setText(self._number(robustness.get("score"), 1))
        self.config_metric.value.setText(f"{int(report.get('unique_configurations_tested') or 0):,}")
        self.timeframe_metric.value.setText(str(report.get("timeframe") or "—"))

        parts = [
            f"Validation P/L {self._money(validation.get('net_pnl'))} · trades {int(validation.get('trade_count') or 0):,}",
            f"Holdout P/L {self._money(holdout.get('net_pnl'))} · trades {int(holdout.get('trade_count') or 0):,}",
            f"Stress P/L {self._money(stress.get('net_pnl'))} · trades {int(stress.get('trade_count') or 0):,}",
        ]
        if walk_summary:
            profitable = walk_summary.get("profitable_fold_pct")
            if profitable is None:
                profitable = walk_summary.get("profitable_pct")
            parts.append(
                f"Walk-forward profitable folds {self._number(profitable, 1)}%"
            )
        if stability:
            parts.append(
                "Nearby-parameter stability "
                + str(stability.get("status") or "measured").replace("_", " ")
            )
        if fidelity:
            parts.append(
                "Execution fidelity "
                + str(fidelity.get("status") or fidelity.get("label") or "checked").replace("_", " ")
            )
        self.result_detail.setText("\n".join(parts))
