"""Read-only microstructure research family; never starts trading or collection."""
import json
from pathlib import Path
from decimal import Decimal, InvalidOperation

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QComboBox, QPushButton, QFileDialog, QPlainTextEdit
from systematic_trader.events import digest
from .error_sanitizer import sanitize_display_text

FAMILIES = {
    "Ordinary Equities": (
        "Opening Micro-Momentum — experiment C: observation windows and breakout entries",
        "Opening Pullback — experiment C: controlled pullback and flow recovery",
        "Tiny-Target Scalping — Experimental — experiment B: +1/+2/+3/+5¢ falsification",
        "Closing-hour and weekday comparisons — experiments D/E, same execution model",
        "Liquidity-Shock Reversion — deferred",
        "Microstructure-Assisted Momentum — deferred",
    ),
    "Special Securities": (
        "Micro-Scalping — experiment A: RIV.RT forensic replay; date and tick coverage required",
        "Relative Value — reference-data hook only; deferred",
        "Explosive Momentum — deferred",
    ),
}


def verified_report(path):
    p = Path(path)
    if p.stat().st_size > 32*1024*1024:
        raise ValueError("Report exceeds bounded reader size")
    r = json.loads(p.read_text())
    if (r.get("version") != "short-horizon-result-v1" or r.get("execution_authority") != "none"
        or r.get("production_eligible") is not False
        or r.get("result_hash") != digest({k:v for k,v in r.items() if k != "result_hash"})):
        raise ValueError("Report integrity or research-only contract failed")
    if r["admission"] == "BLOCKED" and r["metrics"]["net_executable_expectancy_per_opportunity"] is not None:
        raise ValueError("Blocked data cannot establish expectancy")
    return r


class ShortHorizonSection(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        intro = QLabel("Short-Horizon & Microstructure\nResearch only · No live orders · No strategy promotion")
        intro.setWordWrap(True); layout.addWidget(intro)
        self.family = QComboBox(); self.family.addItems(FAMILIES); layout.addWidget(self.family)
        self.catalog = QLabel(); self.catalog.setWordWrap(True); layout.addWidget(self.catalog)
        self.status = QLabel("No experiment result loaded. SIP/NBBO quotes and individual trades are required; IEX and candles cannot establish executable expectancy.")
        self.status.setWordWrap(True); layout.addWidget(self.status)
        load = QPushButton("Open saved research result")
        load.clicked.connect(self._choose); layout.addWidget(load)
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True); layout.addWidget(self.detail)
        self.family.currentTextChanged.connect(self._family)
        self._family(self.family.currentText())

    def _family(self, name):
        self.catalog.setText("\n".join(FAMILIES[name]))

    def _choose(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open saved short-horizon result", "", "Research result (*.json)")
        if path:
            self.load_result(path)

    def load_result(self, path):
        try:
            r = verified_report(path)
            fixture = r['research_status'] == 'fixture_only'
            self.status.setText(sanitize_display_text(
                ('Synthetic engineering test — not market evidence. ' if fixture else 'Historical research — not production validated. ')
                + ('Dataset blocked.' if r['admission'] == 'BLOCKED' else 'Conditional L1 simulation.')))
            def value(key):
                v = r['metrics'].get(key)
                if v is None:
                    return 'Not established'
                try:
                    return format(Decimal(str(v)).quantize(Decimal('0.0001')), 'f').rstrip('0').rstrip('.') or '0'
                except InvalidOperation:
                    return str(v)
            e=r['experiment'];p=r.get('execution_policy',{});c=r.get('costs',{})
            target = (f"${e['target']}/share" if e['target_mode'] == 'cents' else
                      f"{Decimal(e['target'])*100}% of entry price" if e['target_mode'] == 'percentage' else
                      f"{e['target']} × {e['target_mode']}")
            lines=[f"Experiment {e['family']} · {e['style'].capitalize()}",
                f"Observe for {e['observation_minutes']} minutes · Hold up to {e['hold_seconds']} seconds",
                f"Target: {target} · Requested shares: {e['qty']}", '',
                'Execution assumptions',
                f"Order latency: {p.get('latency_ns',0)/1_000_000:g} ms · Fees: {c.get('name','Not recorded').replace('_',' ')}",
                'Aggressive orders use the observed bid/ask at arrival. Displayed quantity can limit fills.',
                'Passive touches do not fill. Unresolved exits prevent an expectancy conclusion.', '',
                'Synthetic results — engineering checks only' if fixture else 'Research results']
            for key,label in (
                ('opportunities','Observed episodes'),('closed_trades','Completed round trips'),
                ('unresolved_episodes','Unresolved episodes'),('gross_target_hits','Gross target hits'),
                ('executable_target_hits','Executable target hits'),
                ('net_executable_expectancy_per_opportunity','Net dollars per opportunity'),
                ('net_executable_expectancy_per_trade','Net dollars per completed trade'),
                ('spread_cost','Round-trip spread cost, dollars'),('slippage_cost','Slippage cost, dollars')):
                lines.append(f'{label}: {value(key)}')
            lines += ['', 'Missing evidence'] + (r['missing'] or ['No admission fields missing; independent evidence review is still required.'])
            lines += ['', 'Validation', 'Final holdout remains locked. No strategy promotion or live-order authority.']
            self.detail.setPlainText('\n'.join(lines))
        except (OSError, ValueError, TypeError, KeyError):
            self.status.setText("Result unavailable or failed integrity checks. No clearance inferred.")
            self.detail.clear()
