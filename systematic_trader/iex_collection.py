"""IEX partial market data only; no fallback, account endpoint or plan change."""
from dataclasses import dataclass,asdict
import logging
from .service import Config,CaptureFailure

ENDPOINT='wss://stream.data.alpaca.markets/v2/iex'

@dataclass(frozen=True)
class IEXConfig(Config):
    feed: str='iex'
    def __post_init__(self):
        if self.feed!='iex':raise CaptureFailure('iex_feed_mismatch')
        Config(**{**asdict(self),"feed":"sip"})
    def subscription(self):
        return {'action':'subscribe','trades':list(self.symbols),'quotes':list(self.symbols)}


def connect_iex(config):
    from websockets.sync.client import connect
    logger=logging.Logger('systematic_trader.iex',level=logging.CRITICAL+1);logger.addHandler(logging.NullHandler())
    return connect(ENDPOINT,open_timeout=10,close_timeout=5,ping_interval=10,ping_timeout=10,
        max_size=config.max_frame_bytes,max_queue=16,compression=None,logger=logger)
