"""Read-only view of saved report claims and the unrun research queue."""
import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QComboBox, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QPlainTextEdit, QSplitter,
)
from .error_sanitizer import sanitize_display_text


class ResearchMapSection(QWidget):
    VIEWS = (
        ('reports', 'Research reports', ('title','classification','page_count')),
        ('hypotheses', 'Canonical hypotheses', ('name','testability','family_id')),
        ('candidate_queue', 'Next candidates — not authorized to run', ('rank','description','coverage_status')),
        ('deferred_queue', 'Deferred ideas', ('name','reason','status')),
        ('method_controls', 'Validation controls', ('name','status','missing')),
        ('adaptive_capabilities', 'Adaptive architecture', ('name','current_lab_support','stage')),
        ('contradictions', 'Disagreements and distinctions', ('kind','description','resolution')),
        ('gap_matrix', 'Current Lab gaps', ('recommendation','status','missing')),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = {}; self.rows = []
        layout = QVBoxLayout(self)
        self.status = QLabel('Research map not loaded. Refresh Research to read saved records.')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.view = QComboBox()
        for _, title, _ in self.VIEWS: self.view.addItem(title)
        layout.addWidget(self.view)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 3)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True)
        split.addWidget(self.table); split.addWidget(self.detail); layout.addWidget(split)
        self.view.currentIndexChanged.connect(self._populate)
        self.table.itemSelectionChanged.connect(self._details)

    def render(self, data):
        self.data = data if isinstance(data, dict) else {}
        if self.data.get('status') != 'verified_inventory':
            self.data = {}
            self.status.setText('Research map unavailable or not yet ingested. No clearance inferred.')
        else:
            self.status.setText(
                f"{len(self.data.get('reports', []))} reports · {len(self.data.get('hypotheses', []))} canonical hypotheses. "
                'Report claims remain unvalidated. Ranked candidates are planning only; this view cannot run them.'
            )
        self._populate()

    def _populate(self, *_):
        key, _, fields = self.VIEWS[self.view.currentIndex()]
        self.rows = self.data.get(key, [])
        self.table.setRowCount(0)
        self.table.setHorizontalHeaderLabels([f.replace('_',' ').capitalize() for f in fields])
        self.detail.clear()
        for row, item in enumerate(self.rows):
            self.table.insertRow(row)
            for col, field in enumerate(fields):
                value = item.get(field, '')
                text = sanitize_display_text(str(value))
                cell = QTableWidgetItem(text); cell.setToolTip(text)
                self.table.setItem(row, col, cell)
        if self.rows: self.table.selectRow(0)

    def _details(self):
        row = self.table.currentRow()
        if not 0 <= row < len(self.rows): return
        blocks = []
        record = self.rows[row]
        preferred = ('name','title','description','research_claim','evidence_strength',
                     'testability','missing_data','timing_boundary','required_raw_data',
                     'required_derived_features','confounders','known_failure_modes','sources')
        ordered = [key for key in preferred if key in record]
        ordered.extend(key for key in record if key not in ordered)
        for key in ordered:
            value = record[key]
            if key == 'sources':
                text = '\n'.join(
                    f"{s['report_id']}, page {s['page']} — {s['section_or_claim']}\n"
                    f"Literal source: {s['literal_excerpt']}\nSource SHA-256: {s['source_sha256']}"
                    for s in value
                )
            elif isinstance(value, list):
                text = '\n'.join(str(v) if not isinstance(v, dict) else json.dumps(v, ensure_ascii=False) for v in value)
            elif isinstance(value, bool): text = 'Yes' if value else 'No'
            else: text = str(value)
            blocks.append(key.replace('_',' ').capitalize() + '\n' + text)
        # The shared sanitizer flattens whitespace. Sanitize each line separately
        # so the readable paragraph structure survives without allowing markup.
        self.detail.setPlainText('\n'.join(sanitize_display_text(line) for line in '\n\n'.join(blocks).split('\n')))
