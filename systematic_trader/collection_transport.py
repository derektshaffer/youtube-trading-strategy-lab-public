"""Fixed market-only transports; exact frames enter the service before parsing."""
import json
import time
from uuid import uuid4

from .events import ContractError,canonical_json
from .service import Config,connect_live,CaptureFailure,CHANNELS,AUTO_CHANNELS
from .tradier import TradierConfig,connect_market,create_session


def stream(service,sid,source,secrets,symbols,stop,*,connect=None,session_factory=create_session,clock=time.time_ns):
    if source not in {'alpaca_sip','alpaca_iex','tradier'}:raise ContractError('market_source_unknown')
    if not secrets or any(not isinstance(s,str) or not s for s in secrets):raise ContractError('market_credentials_unavailable')
    connection_id=uuid4().hex
    service.gap(sid,source,'connection_coverage_unknown_no_resume_cursor',clock())
    from .iex_collection import IEXConfig,connect_iex
    config=IEXConfig(tuple(symbols)) if source=='alpaca_iex' else Config(tuple(symbols)) if source=='alpaca_sip' else TradierConfig(tuple(symbols))
    connector=connect or (connect_iex if source=='alpaca_iex' else connect_live if source=='alpaca_sip' else connect_market)
    session_id=session_factory(secrets[0]) if source=='tradier' else None
    hidden=tuple(secrets)+( (session_id,) if session_id else ())
    last_market=time.monotonic();last_health=0
    with connector(config) as socket:
        def receive(timeout=1):
            nonlocal last_market,last_health
            raw=socket.recv(timeout=timeout)
            raw=raw.encode() if isinstance(raw,str) else raw
            if not isinstance(raw,bytes) or len(raw)>config.max_frame_bytes:raise CaptureFailure('invalid_market_frame')
            altered=False
            for secret in hidden:
                if secret.encode() in raw:raw=raw.replace(secret.encode(),b'[REDACTED]');altered=True
            service.ingest(sid,source,raw,received_ns=clock(),connection_id=connection_id,provenance={'transport':'fixed_market_websocket','feed':config.feed,'credential_reflection_redacted':altered})
            if altered:raise CaptureFailure('credential_reflection_redacted')
            from .providers import adapter_for
            messages=adapter_for('alpaca' if source in {'alpaca_sip','alpaca_iex'} else 'tradier',config.feed).decode(raw)
            if any(m.get('T')=='error' or 'error' in m for m in messages):raise CaptureFailure('market_provider_rejected')
            if any((m.get('S') or m.get('symbol')) not in (None,*symbols) for m in messages):
                raise CaptureFailure('unexpected_market_symbol')
            if any(m.get('S') in symbols or m.get('symbol') in symbols for m in messages):
                last_market=time.monotonic()
                if last_market-last_health>=30:
                    service.health(sid,source,'Healthy','receiving_unqualified_market_frames',clock());last_health=last_market
            return messages
        if source in {'alpaca_sip','alpaca_iex'}:
            if receive(5)!=[{'T':'success','msg':'connected'}]:raise CaptureFailure('market_handshake_invalid')
            socket.send(canonical_json({'action':'auth','key':secrets[0],'secret':secrets[1]}))
            if receive(5)!=[{'T':'success','msg':'authenticated'}]:raise CaptureFailure('market_auth_invalid')
            socket.send(canonical_json(config.subscription()))
            ack=receive(5)
            required=CHANNELS+AUTO_CHANNELS if source=='alpaca_sip' else ('trades','quotes','corrections','cancelErrors')
            if len(ack)!=1 or ack[0].get('T')!='subscription' or any(set(ack[0].get(k,[]))!=set(symbols) for k in required):
                raise CaptureFailure('incomplete_subscription')
            service.health(sid,source,'Degraded','subscription_verified_waiting_for_market_events',clock())
        else:
            socket.send(canonical_json(config.subscription(session_id)))
        silent=False
        while not stop.is_set():
            try:receive()
            except TimeoutError:pass
            if time.monotonic()-last_market>=30 and not silent:
                service.gap(sid,source,'market_stream_silent',clock());silent=True
                service.health(sid,source,'Degraded','market_stream_silent',clock())


def reconnecting_stream(service,sid,source,secrets,symbols,stop,*,runner=stream,clock=time.time_ns,persistent=False):
    from websockets.exceptions import ConnectionClosed,InvalidHandshake
    while not stop.is_set():
        for attempt in range(6):
            try:
                runner(service,sid,source,secrets,symbols,stop)
                return
            except (ConnectionClosed,InvalidHandshake,ConnectionError,TimeoutError) as exc:
                # Authentication, entitlement, throttling and malformed handshakes
                # require intervention. Only a server-side 5xx may rejoin retries.
                if isinstance(exc,InvalidHandshake):
                    status_code=getattr(getattr(exc,'response',None),'status_code',None)
                    if not isinstance(status_code,int) or not 500<=status_code<600:
                        service.gap(sid,source,'provider_rejection_or_protocol_failure',clock())
                        service.health(sid,source,'Source unavailable','provider_rejection_or_protocol_failure_no_retry',clock())
                        return
                service.gap(sid,source,'transport_disconnect',clock())
                service.health(sid,source,'Degraded','transport_disconnect',clock())
                if stop.is_set():return
                if attempt==5:break
                if stop.wait(min(60,2**attempt)):return
            except CaptureFailure:
                service.gap(sid,source,'provider_rejection_or_protocol_failure',clock())
                service.health(sid,source,'Source unavailable','provider_rejection_or_protocol_failure_no_retry',clock())
                return
            # Storage/contract errors deliberately escape and stop the entire service.
        service.health(sid,source,'Source unavailable','reconnect_budget_exhausted',clock())
        if not persistent:return
        service.gap(sid,source,'reconnect_cooldown_no_continuity_claim',clock())
        if stop.wait(300):return
