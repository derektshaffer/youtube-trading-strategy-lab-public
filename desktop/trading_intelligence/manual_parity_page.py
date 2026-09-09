"""Local fixed-configuration historical parity; never a research dispatch."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

import pandas as pd
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QDoubleSpinBox, QPushButton, QPlainTextEdit, QTabWidget, QFileDialog,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QScrollArea)

import backtest_calibration as calibration


class ManualParityPage(QWidget):
    def __init__(self):
        super().__init__()
        self.result = None
        self.dataset = None
        root = QVBoxLayout(self)
        root.addWidget(QLabel('Manual / Parity Backtest'))
        self.status = QLabel('One fixed configuration. Research only; no order or promotion authority.')
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        controls = QWidget()
        form = QFormLayout(controls)
        scroll.setWidget(controls)
        self.tabs.addTab(scroll, 'Fixed configuration')
        self.ticker = QLineEdit('SDOT')
        self.start = QLineEdit('2026-08-12T17:13:21.246942Z')
        self.end = QLineEdit('2026-08-25T17:13:21.246942Z')
        self.interval = QComboBox()
        self.interval.addItems(['1Min', '5Min', '15Min'])
        self.interval.setCurrentText('5Min')
        self.sessions = QComboBox()
        self.sessions.addItems(['Regular hours: 09:30–16:00 ET'])
        for label, widget in [('Ticker', self.ticker), ('Start (timezone required)', self.start),
                              ('End (timezone required)', self.end), ('Candle interval', self.interval),
                              ('Session hours (historical control)', self.sessions)]:
            form.addRow(label, widget)
        saved_path = calibration.FIXTURES/'sdot_20260825_strategy.json'
        saved = json.loads(saved_path.read_text()) if saved_path.exists() else {}
        self.strategy = saved.get('strategy', dict(name='Fixed strategy', direction='long', machine_rules={},
            optimized_backtest_settings=asdict(calibration.fixed_settings({}))))
        self.strategy_name = QLabel(self.strategy['name'])
        form.addRow('Strategy snapshot', self.strategy_name)
        self.fields = {}
        labels = dict(starting_cash='Starting cash ($)', risk_per_trade_pct='Risk per trade (%)',
            max_position_pct='Maximum total position (%)', default_stop_pct='Fallback stop (%)',
            default_reward_risk='Fallback reward/risk', spread_bps='Full spread estimate (bps)',
            slippage_bps='Slippage per fill (bps)', fee_per_order='Fee per order ($)')
        for key, label in labels.items():
            field = QDoubleSpinBox()
            field.setDecimals(4)
            field.setRange(0, 1000000 if key == 'starting_cash' else 10000)
            field.setValue(self.strategy['optimized_backtest_settings'][key])
            self.fields[key] = field
            form.addRow(label, field)
        self.rules = QPlainTextEdit(json.dumps(self.strategy['machine_rules'], indent=2))
        self.rules.setMinimumHeight(230)
        form.addRow('Exact executable rules (saved stop/target override fallbacks)', self.rules)
        self.frozen = QLabel('Frozen: one position; whole shares; long only; 70% legacy metric partition; '
            'same-session prior-candle signal / next available open; feed and adjustment recorded from the frozen dataset; '
            'session-aligned aggregation using the first available minute timestamp; no extra warmup download. '
            'Respect saved session-end and price rules; no inferred pullback breakout, dynamic exits, '
            'automatic costs, sizing search, neighborhood search or validation rejection. '
            'Both archived and current fill models are shown separately.')
        self.frozen.setWordWrap(True)
        form.addRow(self.frozen)
        self.load_data = QPushButton('Load frozen 1-minute dataset')
        self.load_data.clicked.connect(self.load_dataset)
        form.addRow(self.load_data)
        self.run = QPushButton('Run fixed configuration locally')
        self.run.clicked.connect(self.run_fixed)
        root.addWidget(self.run)
        self.export = QPushButton('Export ledger, bars and comparison')
        self.export.setEnabled(False)
        self.export.clicked.connect(self.export_result)
        root.addWidget(self.export)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.tabs.addTab(self.summary, 'Comparison / calibration')
        self.ledger = QTableWidget()
        self.ledger.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabs.addTab(self.ledger, 'Current trade ledger')
        self.bars = QPlainTextEdit()
        self.bars.setReadOnly(True)
        self.tabs.addTab(self.bars, 'First divergence / signals')
        data_path = calibration.FIXTURES/'sdot_20260825_refetched_1min.json'
        if data_path.exists():
            self.dataset = json.loads(data_path.read_text())
        existing = calibration.ROOT/'reports/sdot_calibration_execution.json'
        if existing.exists():
            self.render(json.loads(existing.read_text()), saved=True)
        for field in self.fields.values():
            field.valueChanged.connect(self.inputs_changed)
        for field in (self.ticker, self.start, self.end):
            field.textChanged.connect(self.inputs_changed)
        self.interval.currentTextChanged.connect(self.inputs_changed)
        self.rules.textChanged.connect(self.inputs_changed)

    def inputs_changed(self, *_):
        self.export.setEnabled(False)
        self.status.setText('Controls changed. The displayed ledger belongs to the previous fixed run; run these inputs to compare them.')

    def run_fixed(self):
        try:
            if self.dataset is None:
                raise ValueError('No frozen dataset loaded')
            if self.ticker.text().strip().upper() != self.dataset['symbol']:
                raise ValueError('The frozen dataset is ' + self.dataset['symbol'] + '; no other symbol is substituted or downloaded')
            start, end = pd.Timestamp(self.start.text()), pd.Timestamp(self.end.text())
            if start.tzinfo is None or end.tzinfo is None or start >= end:
                raise ValueError('Specify an increasing, timezone-aware historical range')
            if start < pd.Timestamp(self.dataset['request_start']) or end > pd.Timestamp(self.dataset['request_end']):
                raise ValueError('Requested dates exceed the frozen provider response coverage')
            minutes = [r for r in self.dataset['rows'] if start <= pd.Timestamp(r['t']) <= end]
            rows = calibration.historical_engine().resample_intraday_bars(minutes, self.interval.currentText())
            strategy = deepcopy(self.strategy)
            strategy['machine_rules'] = json.loads(self.rules.toPlainText())
            settings = {k: w.value() for k, w in self.fields.items()}
            result = calibration.run_parity(rows, strategy, self.ticker.text().strip().upper(), settings,
                provenance=dict(source_kind=self.dataset['source_kind'], provider=self.dataset['provider'],
                    feed=self.dataset['feed'], adjustment=self.dataset['adjustment'],
                    source_rows_sha256=self.dataset['rows_sha256'], start=str(start), end=str(end),
                    timeframe=self.interval.currentText(), construction='historical resample from 1Min'))
            self.render(result)
        except Exception as exc:
            self.status.setText('Fixed run failed: ' + str(exc))

    def load_dataset(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Load frozen provider dataset', '', 'JSON (*.json)')
        if not filename:
            return
        try:
            data = json.loads(Path(filename).read_text())
            for field in ('symbol', 'source_kind', 'provider', 'feed', 'adjustment', 'request_start', 'request_end', 'rows_sha256'):
                if not data.get(field):
                    raise ValueError('Dataset provenance missing ' + field)
            if data.get('timeframe') != '1Min':
                raise ValueError('Historical construction requires original 1-minute candles')
            calibration.validate_rows(data['rows'])
            if calibration.digest(data['rows']) != data['rows_sha256']:
                raise ValueError('Dataset candle hash does not match')
            self.dataset = data
            self.ticker.setText(data['symbol'])
            self.start.setText(data['request_start'])
            self.end.setText(data['request_end'])
            self.status.setText('Frozen dataset loaded. Review the exact rules before running.')
        except Exception as exc:
            self.status.setText('Dataset rejected: ' + str(exc))

    def render(self, result, *, saved=False):
        self.result = result
        current = result['current_result']['metrics']
        old = result['legacy_result']['metrics']
        self.status.setText(('Saved control. ' if saved else 'Fixed run complete. ') +
            f"Exact legacy/current parity: {result['execution_parity']}. Legacy: {old['trade_count']} trades, "
            f"${old['net_pnl']:,.2f}. Current: {current['trade_count']} trades, ${current['net_pnl']:,.2f}. Research only.")
        summary = {k: v for k, v in result.items() if k not in ('legacy_bar_trace', 'current_bar_trace',
            'legacy_ledger', 'current_ledger', 'legacy_result', 'current_result')}
        summary.update(legacy_metrics=old, current_metrics=current)
        for kind in ('assessment', 'discovery', 'discovery_deep', 'validation', 'fidelity'):
            path = calibration.ROOT/f'reports/sdot_calibration_{kind}.json'
            if path.exists():
                data = json.loads(path.read_text())
                compact = {k: v for k, v in data.items() if k not in ('trials', 'result')}
                if data.get('result'):
                    winner = data['result'].get('winner') or {}
                    compact['winner_metrics'] = winner.get('full_metrics')
                    compact['winner_rules'] = {k:v for k,v in (winner.get('optimized_rules') or {}).items() if v is not None}
                summary['saved_control_' + kind] = compact
        self.summary.setPlainText(json.dumps(summary, indent=2, default=str))
        ledger = result['current_ledger']
        columns = list(ledger[0]) if ledger else []
        self.ledger.setColumnCount(len(columns))
        self.ledger.setHorizontalHeaderLabels(columns)
        self.ledger.setRowCount(len(ledger))
        for i, row in enumerate(ledger):
            for j, key in enumerate(columns):
                value = row[key]
                self.ledger.setItem(i, j, QTableWidgetItem(f'{value:.6f}' if isinstance(value, float) else str(value)))
        index = (result.get('first_divergence') or {}).get('index', 0)
        self.bars.setPlainText(json.dumps(dict(first_divergence=result.get('first_divergence'),
            current_bars=result['current_bar_trace'][max(0,index-2):index+3],
            legacy_bars=result['legacy_bar_trace'][max(0,index-2):index+3]), indent=2))
        self.export.setEnabled(True)

    def export_result(self):
        if not self.result:
            return
        folder = QFileDialog.getExistingDirectory(self, 'Export parity evidence')
        if folder:
            # Unique run hash prevents mixing results from different parameters.
            target = Path(folder)/('manual-parity-' + self.result['input_hash'][:16])
            target.mkdir(exist_ok=True)
            (target/'comparison.json').write_text(json.dumps(self.result, indent=2, default=str))
            for kind in ('legacy', 'current'):
                (target/(kind+'-ledger.csv')).write_text(calibration.ledger_csv(self.result[kind+'_ledger']))
            self.status.setText('Exported fixed-run evidence to ' + str(target))
