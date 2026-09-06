"""Read-only native display of the canonical compact validation projection."""
import math
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QTabWidget, QHeaderView

from .display_time import format_timestamp

PERIODS = (("Historical training", "training_metrics"), ("Validation", "validation_metrics"),
           ("Holdout", "holdout_metrics"), ("Stress", "stress_metrics"), ("Full history (descriptive)", "full_metrics"))
CHECKS = (("Walk-forward", "walk_forward"), ("Parameter stability", "parameter_stability"),
          ("Execution fidelity", "paper_execution_fidelity"), ("Historical spread", "historical_spread_audit"),
          ("Market data integrity", "market_data_integrity"), ("Holdout reuse", "holdout_reuse_audit"))


def plain(text):
    label = QLabel(str(text))
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def number(value):
    if value is None:
        return "Not recorded"
    try:
        n = float(value)
        return f"{n:g}" if math.isfinite(n) else str(n)
    except (ValueError, TypeError):
        return "Not recorded"


def table(headers, rows):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    widget.horizontalHeader().setStretchLastSection(True)
    widget.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            widget.setItem(i, j, item)
    widget.resizeColumnsToContents()
    widget.setMinimumHeight(180)
    return widget


class SavedValidationPage(QWidget):
    def __init__(self, response):
        super().__init__()
        root = QVBoxLayout(self)
        result = response.get("result") or {}
        self.identity = plain(f"Job: {response['job_id']}\nCloud job: {response.get('cloud_job_id') or 'not recorded'}\nRun: {response['run_id']}\nTicker: {response['ticker']}\nStrategy ID: {', '.join(response['strategy_ids'])}")
        root.addWidget(self.identity)
        self.timestamp = plain("Saved evidence: " + format_timestamp(
            result.get("saved_at") or (response.get("error") or {}).get("last_checkpoint_saved_at") or response.get("updated_at"),
            "not recorded", naive_utc=True))
        root.addWidget(self.timestamp)
        self.verdict = plain("")
        root.addWidget(self.verdict)
        if response["status"] == "failed":
            error = response.get("error") or {}
            self.verdict.setText("INFRASTRUCTURE / APPLICATION FAILURE\nTerminal state: failed (not running).\nNo strategy-validation verdict was produced.\n" + str(error.get("message") or "Execution failed."))
            root.addWidget(plain("Failure kind: " + str(error.get("kind") or error.get("terminal_reason") or error.get("type") or "not recorded")))
            root.addStretch(1)
            return
        verdict = result.get("evidence_verdict") or {}
        outcome = "Strategy validation: FAILED (execution completed).\n" if verdict.get("code") == "no_robust_strategy" else "Validation execution completed.\n"
        self.verdict.setText(outcome + str(verdict.get("label") or "No saved strategy verdict") + "\n" + str(verdict.get("reason") or ""))
        strength = result.get("strength") or {}
        self.strength = plain("Validation strength: " + (number(strength["score"]) + "/100" if strength.get("score") is not None else "Not recorded") + " | " + str(strength.get("label") or "Not recorded") + "\nNot a probability of profit.")
        root.addWidget(self.strength)
        self.selected_title = plain(str(result.get("winner_strategy_name") or "Saved selected strategy") + "\nStrategy ID: " + str(result.get("winner_strategy_id") or "not recorded"))
        root.addWidget(self.selected_title)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        rows = []
        for label, key in PERIODS:
            metrics = result.get(key) or {}
            count = metrics.get("trade_count")
            state = "No trades" if count == 0 else "Recorded" if metrics else "Not saved / not run"
            rows.append((label, state, number(count), number(metrics.get("net_pnl")), number(metrics.get("return_pct")), number(metrics.get("profit_factor"))))
        self.tests = table(["Test", "Evidence", "Trades", "P/L", "Return (%)", "Profit factor"], rows)
        self.tabs.addTab(self.tests, "Test results")
        rows = []
        for label, key in CHECKS:
            check = result.get(key) or {}
            check = check.get("summary") or check
            status = str(check.get("label") or check.get("classification") or check.get("status") or ("Recorded" if check else "Not saved / not run"))
            if check.get("status") and check["status"] != status:
                status += " (" + str(check["status"]) + ")"
            details = "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in check.items() if k not in {"status", "label", "classification"} and not isinstance(v, (dict, list)))
            rows.append((label, status, details))
        self.checks = table(["Check", "Saved status", "Details"], rows)
        self.tabs.addTab(self.checks, "Robustness && safeguards")
        rows = [("Strength", number(strength.get("score")) + "/100" if strength.get("score") is not None else "Not recorded"),
                ("Classification", strength.get("label") or "Not recorded")]
        rows.extend(("Reason", x) for x in strength.get("reasons") or [])
        self.confidence = table(["Confidence evidence", "Saved value"], rows)
        self.tabs.addTab(self.confidence, "Confidence")
        warnings = [("Optimizer warning", x) for x in (result.get("optimizer_summary") or {}).get("warnings") or []]
        warnings.extend(("Limitation", x) for x in result.get("backtest_limitations") or [])
        warnings.extend(("Walk-forward warning", x) for x in (result.get("walk_forward") or {}).get("warnings") or [])
        self.warnings = table(["Evidence note", "Saved value"], warnings)
        self.tabs.addTab(self.warnings, "Warnings")
        root.addWidget(plain("Read-only historical evidence. No execution, parameter changes or trading approval."))
