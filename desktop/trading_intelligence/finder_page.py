"""Native Stock Strategy Finder page for local Quick and durable cloud searches."""

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


PROFILE_COPY = {
    "Quick": (
        "Local",
        "60 days · 5-minute search · fast first pass using the persistent raw-history cache.",
    ),
    "Current Regime": (
        "Cloud",
        "35 days · 1m/5m/15m · focuses on the current market regime using durable cloud compute.",
    ),
    "Deep": (
        "Cloud",
        "140 days · 1m/5m/15m · broader optimization, walk-forward, stability, and execution audit.",
    ),
    "Very Deep": (
        "Cloud",
        "260 days · 1m/5m/15m · largest bounded search, distributed across cloud shards.",
    ),
}


class FinderPage(QWidget):
    search_requested = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        top = QHBoxLayout()
        copy = QVBoxLayout()
        eyebrow = QLabel("STOCK STRATEGY FINDER")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Find the strongest strategy family for one stock")
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "Quick searches run on your Mac and reuse finalized research history. "
            "Current Regime, Deep, and Very Deep run through the existing distributed cloud Finder "
            "and continue after the desktop app closes."
        )
        subtitle.setObjectName("Subtle")
        subtitle.setWordWrap(True)
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        top.addLayout(copy, 1)
        root.addLayout(top)

        controls = Card()
        form = QGridLayout(controls)
        form.setContentsMargins(14, 12, 14, 12)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        self.symbol = QLineEdit()
        self.symbol.setPlaceholderText("Ticker, e.g. SDOT")
        self.symbol.setMaxLength(10)
        self.symbol.returnPressed.connect(self.emit_search)
        self.profile = QComboBox()
        for name in ("Quick", "Current Regime", "Deep", "Very Deep"):
            self.profile.addItem(name, name)
        self.profile.currentIndexChanged.connect(self._profile_changed)
        self.route = QLabel()
        self.route.setObjectName("Badge")
        self.profile_detail = QLabel()
        self.profile_detail.setObjectName("Subtle")
        self.profile_detail.setWordWrap(True)
        self.run = QPushButton("Find best strategy")
        self.run.setObjectName("Primary")
        self.run.clicked.connect(self.emit_search)
        form.addWidget(QLabel("Symbol"), 0, 0)
        form.addWidget(self.symbol, 0, 1)
        form.addWidget(QLabel("Search depth"), 0, 2)
        form.addWidget(self.profile, 0, 3)
        form.addWidget(self.route, 0, 4)
        form.addWidget(self.run, 0, 5)
        form.addWidget(self.profile_detail, 1, 0, 1, 6)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        root.addWidget(controls)
        self._profile_changed()

        self.banner = Card()
        banner = QVBoxLayout(self.banner)
        self.status = QLabel("Ready to search")
        self.status.setObjectName("BannerTitle")
        self.detail = QLabel(
            "Finder does not treat an optimized backtest as validation. Walk-forward, robustness, "
            "parameter stability, paper fidelity, split integrity, and historical spread evidence remain in force."
        )
        self.detail.setObjectName("Subtle")
        self.detail.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        banner.addWidget(self.status)
        banner.addWidget(self.detail)
        banner.addWidget(self.progress)
        root.addWidget(self.banner)

        metrics = QGridLayout()
        self.winner = MetricCard("Winner")
        self.timeframe = MetricCard("Timeframe")
        self.verdict = MetricCard("Verdict")
        self.robustness = MetricCard("Robustness")
        self.configurations = MetricCard("Configurations")
        self.execution = MetricCard("Execution")
        for index, widget in enumerate(
            (
                self.winner,
                self.timeframe,
                self.verdict,
                self.robustness,
                self.configurations,
                self.execution,
            )
        ):
            metrics.addWidget(widget, 0, index)
        root.addLayout(metrics)

        evidence = Card()
        evidence_layout = QVBoxLayout(evidence)
        evidence_heading = QLabel("Evidence summary")
        evidence_heading.setObjectName("SectionTitle")
        self.evidence = QLabel(
            "Run Finder to see walk-forward, stability, holdout/execution, and cache evidence."
        )
        self.evidence.setObjectName("Subtle")
        self.evidence.setWordWrap(True)
        self.evidence.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        evidence_layout.addWidget(evidence_heading)
        evidence_layout.addWidget(self.evidence)
        root.addWidget(evidence, 1)

    def _profile_changed(self) -> None:
        profile = str(self.profile.currentData() or "Quick")
        target, detail = PROFILE_COPY.get(profile, ("Auto", ""))
        self.route.setText(target)
        self.profile_detail.setText(detail)
        self.run.setText("Find best strategy" if target == "Local" else "Run Finder in cloud")

    def emit_search(self) -> None:
        symbol = self.symbol.text().strip().upper()
        if not symbol:
            self.set_error("Enter a ticker before starting Finder.")
            return
        self.symbol.setText(symbol)
        profile = str(self.profile.currentData() or "Quick")
        self.search_requested.emit(
            {
                "symbol": symbol,
                "profile": profile,
                "continue_after_app_exit": profile != "Quick",
            }
        )

    def set_working(self, stage: str, detail: str, progress: float) -> None:
        self.run.setEnabled(False)
        self.symbol.setEnabled(False)
        self.profile.setEnabled(False)
        self.banner.setProperty("state", "working")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText(stage)
        self.detail.setText(detail)
        self.progress.setValue(round(max(0.0, min(1.0, progress)) * 1000))

    def set_error(self, message: str) -> None:
        self.run.setEnabled(True)
        self.symbol.setEnabled(True)
        self.profile.setEnabled(True)
        self.banner.setProperty("state", "error")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.status.setText("Finder could not complete")
        self.detail.setText(message)
        self.progress.setValue(0)

    @staticmethod
    def _verdict_text(result: dict[str, Any]) -> str:
        verdict = result.get("verdict") if isinstance(result.get("verdict"), dict) else {}
        return str(
            verdict.get("label")
            or verdict.get("status")
            or verdict.get("code")
            or "research only"
        ).replace("_", " ")

    def render_result(self, result: dict[str, Any]) -> None:
        self.run.setEnabled(True)
        self.symbol.setEnabled(True)
        self.profile.setEnabled(True)
        self.banner.setProperty("state", "ready")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)

        symbol = str(result.get("symbol") or self.symbol.text()).upper()
        profile = str(result.get("profile") or self.profile.currentData() or "")
        winner = result.get("winner") if isinstance(result.get("winner"), dict) else {}
        robustness = result.get("robustness") if isinstance(result.get("robustness"), dict) else {}
        walk = result.get("walk_forward") if isinstance(result.get("walk_forward"), dict) else {}
        stability = (
            result.get("parameter_stability")
            if isinstance(result.get("parameter_stability"), dict)
            else {}
        )
        fidelity = (
            result.get("paper_execution_fidelity")
            if isinstance(result.get("paper_execution_fidelity"), dict)
            else {}
        )
        cache = (
            result.get("research_history_cache")
            if isinstance(result.get("research_history_cache"), dict)
            else {}
        )
        execution_target = str(result.get("execution_target") or "local").lower()
        verdict_text = self._verdict_text(result)
        self.status.setText(f"{symbol} · {profile} Finder complete")
        self.detail.setText(
            "This is research evidence, not a guaranteed profitable strategy. "
            "A fail-closed verdict remains research-only until the strict gates are satisfied."
        )
        self.progress.setValue(1000)
        self.winner.value.setText(
            str(winner.get("source_name") or winner.get("strategy_name") or "No robust winner")
        )
        self.timeframe.value.setText(str(result.get("timeframe") or "—"))
        self.verdict.value.setText(verdict_text)
        try:
            self.robustness.value.setText(f"{float(robustness.get('score') or 0.0):.1f}")
        except (TypeError, ValueError, OverflowError):
            self.robustness.value.setText("—")
        self.configurations.value.setText(
            f"{int(result.get('unique_configurations_tested') or 0):,}"
        )
        self.execution.value.setText(execution_target.title())

        profitable_folds = walk.get("profitable_fold_pct")
        stable = stability.get("stable")
        paper_ok = fidelity.get("meets_paper_execution_fidelity")
        cache_reused = int(cache.get("reused_request_count") or 0)
        cache_network = int(cache.get("network_request_count") or 0)
        lines = [
            f"Walk-forward profitable folds: {profitable_folds if profitable_folds is not None else '—'}%.",
            f"Parameter stability: {'passed' if stable is True else 'not passed' if stable is False else '—'}.",
            f"Paper execution fidelity: {'passed' if paper_ok is True else 'not passed' if paper_ok is False else '—'}.",
        ]
        if cache:
            lines.append(
                f"Research history cache: {cache_reused} request(s) reused, {cache_network} provider refresh(es)."
            )
        if result.get("remote_job_id"):
            lines.append(f"Cloud job: {result.get('remote_job_id')}.")
        self.evidence.setText(" ".join(lines))
