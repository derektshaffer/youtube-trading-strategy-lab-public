"""Compact read-only certificate provenance. No override/execute controls."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel
from .workflow_widgets import Disclosure


class CertificateSection(Disclosure):
    def __init__(self):
        label=QLabel()
        label.setTextFormat(Qt.TextFormat.PlainText);label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        super().__init__('Dataset certificate and runner',label,expanded=False)
        self.label=label
        self.render({})

    def render(self, status):
        s=status if isinstance(status,dict) else {}
        detail=str(s.get('detail','No trusted certificate status loaded.'))
        detail={'deterministic_admission_rejected':'Required historical evidence has not passed admission.'}.get(detail,detail)
        lines=['Dataset certification: '+str(s.get('dataset_certification','Pending')),
               'Experiment: '+str(s.get('experiment','Not frozen')),
               'Runner authorization: '+str(s.get('runner_authorization','Blocked')),
               detail]
        if s.get('certificate_id'):lines.append('Certificate: '+str(s['certificate_id']))
        if s.get('holdout'):lines.append('Recorded holdout state: '+str(s['holdout']))
        lines.append('No live or paper orders. AI agreement does not certify data.')
        self.label.setText('\n\n'.join(lines))
