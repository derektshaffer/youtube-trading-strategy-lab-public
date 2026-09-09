"""Compact evidence-standard labels; no locked observations or override controls."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel,QWidget,QVBoxLayout
from .workflow_widgets import Disclosure


class EvidenceTracksSection(QWidget):
    def __init__(self):
        super().__init__();layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.status=QLabel();self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.detail=QLabel();self.detail.setWordWrap(True);self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(Disclosure('Evidence collection details',self.detail,expanded=False));self.render({})

    def render(self,value):
        value=value if isinstance(value,dict) else {};p=value.get('preliminary',{});f=value.get('prospective',{})
        state='Prospective evidence collecting' if f.get('collecting') else str(f.get('state','Prospective status not loaded'))
        certificate=value.get('certificate',{})
        certificate=certificate if isinstance(certificate,dict) else {}
        if certificate.get('dataset_certification')=='Certified' and certificate.get('experiment')=='Experiment frozen':
            certification='Certified fixture experiment — not historical evidence' if certificate.get('trust_domain')=='synthetic-fixture' else 'Certified experiment — '+str(certificate.get('runner_authorization','Blocked'))
        else:certification='Certification blocked or pending'
        self.status.setText('Preliminary research only — not certified evidence of profitability\n'+state+'\n'+certification)
        service=value.get('collection_service',{})
        service_lines=[]
        campaign=value.get('campaign',{})
        if isinstance(campaign,dict):
            service_lines += ['Bounded research: '+str(c.get('hypothesis_id',''))+' — '+str(c.get('classification','')) for c in campaign.get('candidates',[])]
        service_lines += ['Target momentum research: '+str(c.get('id','')).replace('-',' ').capitalize()+' — '+str(c.get('classification','')) for c in value.get('target_campaign',{}).get('candidates',[])]
        if isinstance(service,dict) and service:
            service_lines+=['Collector: '+str(service.get('state','Unknown'))+'; session: '+str(service.get('session') or 'not registered').split('|')[0],
                'Monitored: '+', '.join(service.get('symbols',[])),
                'Session evidence: '+str(service.get('completeness','Unknown'))+'; '+str(service.get('seal','Unsealed')),
                'Detected gaps: '+str(service.get('detected_gaps',0))+'; certification not yet evaluated']
            labels={'luld':'LULD','market':'Market data','alpaca_iex':'Alpaca IEX','alpaca_sip':'Alpaca SIP','alpaca_assets':'Alpaca assets',
                'alpaca_actions':'Alpaca actions','alpaca_calendar':'Alpaca calendar','nasdaq_directory':'Nasdaq directory','tradier':'Tradier'}
            reasons={'snapshot_received_not_complete_interval':'Snapshot received; interval coverage unverified',
                'existing_SIP_certification_BLOCKED_no_new_stream_probe':'SIP entitlement remains blocked',
                'existing_SIP_entitlement_BLOCKED':'SIP entitlement remains blocked',
                'existing_Lab_Dev_tradier_credentials_unavailable':'Missing Lab Dev Tradier token',
                'existing_lab_tradier_credentials_unavailable':'Missing Lab Dev Tradier token'}
            from datetime import datetime,timezone
            stamp=service.get('last_event_time_ns')
            last=datetime.fromtimestamp(stamp/10**9,timezone.utc).isoformat() if stamp else 'None yet'
            service_lines += ['Phase: '+str(service.get('phase','Awaiting session')),
                'Raw receipts: '+str(service.get('raw_receipts',0))+'; last receipt (UTC): '+last,
                'Market events: '+str(service.get('market_events',0)),
                'Not certifiable; collection only',
                'Alpaca SIP: '+service.get('source_matrix',{}).get('alpaca_sip','Unknown')+'; Tradier: '+service.get('source_matrix',{}).get('tradier','Unknown')]
            service_lines += [field+': '+row['classification'] for field,row in service.get('source_matrix',{}).get('fields',{}).items()]
            service_lines += [symbol+': '+', '.join(sorted(set(checks.values()))) for symbol,checks in service.get('completeness_by_symbol',{}).items()]
            service_lines += [labels.get(key,key.replace('_',' ').capitalize())+': '+', '.join(values) for key,values in service.get('coverage',{}).items()]
            service_lines += [labels.get(key,key.replace('_',' ').capitalize())+': '+v.get('state','Unknown')+' ('+reasons.get(v.get('reason',''),v.get('reason','').replace('_',' '))+')' for key,v in service.get('sources',{}).items()]
            self.status.setText(self.status.text()+'\nCollector: '+str(service.get('state','Unknown')))
        self.detail.setText('\n'.join([
            'Preliminary historical research runs: '+str(p.get('runs',0)),
            'Prospective sessions complete: '+str(f.get('complete',0))+'; incomplete: '+str(f.get('incomplete',0)),
            'Collection-only and locked observations are not exposed to research.',
            'Completeness is not certification. See the separate certificate status.',
            'No paper or live orders. No automatic promotion.',*service_lines]))
