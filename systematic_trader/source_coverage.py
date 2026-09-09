"""Operational claims, separate from immutable legacy configuration contracts."""
from .prospective_adapters import SOURCES


def matrix(*,iex_subscription=False,iex_market_events=0,tradier_configured=False,iex_event_types=None):
    # An authenticated subscription is access evidence, not a market observation.
    types=iex_event_types or {}
    def observed(*kinds):
        return 'Live available' if iex_subscription and any(types.get(k,0)>0 for k in kinds) else 'Not currently sourced'
    live=observed('market.trade','market.quote','market.trade_correction','market.trade_cancel')
    rows={
      'security identity':('Snapshot/reference only','Alpaca assets UUID','No effective interval or continuous listing proof'),
      'listing/class state':('Snapshot/reference only','Alpaca assets; Nasdaq directory','Observed current state only'),
      'corporate actions':('Snapshot/reference only','Alpaca announcements','Effective-date completeness and revision coverage unverified'),
      'round-lot quantity':('Snapshot/reference only','Nasdaq directory','No stable-ID historical interval; quote lot units do not establish quantity'),
      'positive trading status':('Entitlement blocked','Alpaca SIP status adapter','IEX adapter does not assert positive status'),
      'trading actions':('Not currently sourced','None','No complete administrative stream'),
      'halts/resumptions':('Entitlement blocked','Alpaca SIP status adapter','IEX prices cannot establish halt absence'),
      'LULD bands/transitions':('Entitlement blocked','Alpaca SIP LULD adapter','IEX has no admitted LULD evidence'),
      'quotes':(observed('market.quote'),'Alpaca IEX','Single venue, not consolidated; no market events means unverified capture'),
      'trades':(observed('market.trade'),'Alpaca IEX','Single venue, not consolidated; historical SIP bars are historical only'),
      'corrections/cancellations':(observed('market.trade_correction','market.trade_cancel'),'Alpaca IEX automatic subscribed channels','Adapter and subscription support; actual amendments must be observed; no complete coverage claim'),
      'timestamps':(live,'Alpaca IEX provider timestamp + local receipt nanoseconds','Serialized precision does not prove clock accuracy; current reference is receipt-time only'),
      'sequence/gap semantics':('Not currently sourced','Local arrival order + disconnect audit','No provider contiguous sequence or replay cursor; unknown loss remains unresolved'),
    }
    return dict(fields={k:dict(classification=v[0],source=v[1],limitation=v[2]) for k,v in rows.items()},
        alpaca_sip='Entitlement blocked',tradier='Not currently sourced' if tradier_configured else 'Credential blocked',fixture='Fixture only',
        historical='Historical only: owned exports do not supply future entering state',
        iex_subscription_verified=iex_subscription,iex_actual_market_events=iex_market_events,iex_observed_event_types=types,
        adapter_version=SOURCES['alpaca_iex']['version'],certified=False,orders_enabled=False)
