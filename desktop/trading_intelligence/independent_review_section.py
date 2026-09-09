"""Compact read-only review display. Model text is always rendered as plain text."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget, QComboBox

from .workflow_widgets import Disclosure
from .error_sanitizer import sanitize_display_text

LABELS = {
    'PRIMARY_PENDING': 'Primary AI result pending',
    'INDEPENDENT_REVIEW_PENDING': 'Independent review pending',
    'AI_REVIEW_DISAGREEMENT': 'Material disagreement',
    'DETERMINISTIC_VALIDATION_FAILED': 'Deterministic validation failed',
    'DETERMINISTIC_VALIDATION_PENDING': 'Deterministic validation pending',
    'CLEARED_FOR_NEXT_VALIDATION_STAGE': 'Cleared for next validation stage',
    'REVIEW_CANCELLED': 'Independent review cancelled',
}


class IndependentReviewSection(Disclosure):
    def __init__(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        self.selector = QComboBox()
        self.detail = QLabel('No independent reviews loaded. Legacy specialist reviews do not confer clearance.')
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.selector)
        layout.addWidget(self.detail)
        layout.addStretch(1)
        self.records = []
        super().__init__('Independent AI review', body, expanded=False)
        self.selector.currentIndexChanged.connect(self._select)
        self.selector.hide()

    def render(self, records):
        self.records = records if isinstance(records, list) else []
        self.selector.blockSignals(True)
        self.selector.clear()
        for row in self.records:
            self.selector.addItem(str(row.get('artifact_id', 'Unknown artifact')))
        self.selector.blockSignals(False)
        self.selector.setVisible(bool(self.records))
        self._select(0 if self.records else -1)

    def _select(self, index):
        if not 0 <= index < len(self.records):
            self.detail.setText('No saved independent reviews available. Legacy specialist reviews do not confer clearance.')
            return
        s = self.records[index]
        primary, review = s.get('primary') or {}, s.get('review') or {}
        p, r = primary.get('output') or {}, review.get('output') or {}
        agreement = {'agree': 'AIs agree', 'minor': 'Minor concerns', 'material': 'Material disagreement'}.get(r.get('classification'), 'Independent review pending')
        concerns = s.get('open_objections') or r.get('objections') or []
        lines = [f"Primary AI result: {p.get('proposal', 'Pending')}",
            f"Independent review: {r.get('summary', 'Pending')}", f'Agreement status: {agreement}',
            'Reviewer concerns: ' + ('; '.join(str(x.get('objection', '')) for x in concerns) or 'None recorded'),
            'Deterministic validation: ' + str(s.get('deterministic', {}).get('checks', 'Pending')),
            'Final disposition: ' + LABELS.get(s.get('state'), 'Independent review pending'),
            'Offline review only. No data certificate, runner authorization or order authority.']
        content = '\n\n'.join(sanitize_display_text(line) for line in lines)
        if len(content) > 12000:
            content = content[:12000] + '\n[Preview truncated; exact outputs remain in the local audit.]'
        self.detail.setText(content)
