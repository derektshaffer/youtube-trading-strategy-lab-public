"""Causal, transparent trade/quote features. Future outcomes live in reports only."""
from collections import deque
from decimal import Decimal
from math import sqrt

from ..events import ContractError
from .contracts import NS, decimal

D = Decimal


class Features:
    def __init__(self, session, *, max_window_events=200_000):
        self.session = session
        self.trades = deque()
        self.limit = max_window_events
        self.first_pre = self.last_pre = self.pre_high = self.pre_low = None
        self.pre_volume = 0
        self.open_price = self.high = self.low = self.last = None
        self.volume = 0
        self.notional = D(0)
        self.last_source_ns = -1
        self.quote = None
        self.ofi = D(0)
        self.pullback_seen = False
        self.previous = None

    def observe(self, e, now):
        if e.kind == "quote" and e.eligible:
            if self.quote and e.exchange_ns < self.quote.exchange_ns:
                return
            if self.quote and e.size_unit == self.quote.size_unit == "shares":
                p = self.quote
                self.ofi += ((e.bid_size if decimal(e.bid) >= decimal(p.bid) else 0)
                             - (p.bid_size if decimal(e.bid) <= decimal(p.bid) else 0)
                             - (e.ask_size if decimal(e.ask) <= decimal(p.ask) else 0)
                             + (p.ask_size if decimal(e.ask) >= decimal(p.ask) else 0))
            self.quote = e
        if e.kind != "trade" or not e.eligible or e.exchange_ns < self.last_source_ns:
            return
        # Do not let late premarket prints enter the opening-price series.
        segment = self.session.segment(e.exchange_ns)
        if segment != self.session.segment(now):
            return
        self.last_source_ns = e.exchange_ns
        p = decimal(e.price)
        if segment == "premarket":
            self.first_pre = p if self.first_pre is None else self.first_pre
            self.last_pre = p
            self.pre_high = p if self.pre_high is None else max(p, self.pre_high)
            self.pre_low = p if self.pre_low is None else min(p, self.pre_low)
            self.pre_volume += e.size
        if segment != "regular":
            return
        self.previous = self.last
        self.open_price = p if self.open_price is None else self.open_price
        self.high = p if self.high is None else max(p, self.high)
        self.low = p if self.low is None else min(p, self.low)
        if self.high and p < self.high:
            self.pullback_seen = True
        self.last = p
        self.volume += e.size
        self.notional += p * e.size
        sign = 0
        q = self.quote
        if q and q.eligible and 0 <= now-q.exchange_ns <= NS and decimal(q.ask) > decimal(q.bid):
            sign = 1 if p >= decimal(q.ask) else -1 if p <= decimal(q.bid) else 0
        self.trades.append((now, p, e.size, sign))
        while self.trades and self.trades[0][0] < now-60*NS:
            self.trades.popleft()
        if len(self.trades) > self.limit:
            raise ContractError("short_horizon_feature_window_bound_exceeded")

    def snapshot(self, now):
        while self.trades and self.trades[0][0] < now-60*NS:
            self.trades.popleft()
        current = [t for t in self.trades if t[0] > now-10*NS]
        prior = [t for t in self.trades if now-20*NS < t[0] <= now-10*NS]
        qty = sum(t[2] for t in current)
        prev_qty = sum(t[2] for t in prior)
        buy = sum(t[2] for t in current if t[3] == 1)
        sell = sum(t[2] for t in current if t[3] == -1)
        velocity = None
        if len(current) >= 2 and current[-1][0] > current[0][0]:
            velocity = (current[-1][1]-current[0][1]) / (D(current[-1][0]-current[0][0])/NS)
        changes = [float(b[1]-a[1]) for a, b in zip(current, current[1:])]
        volatility = D(str(sqrt(sum(v*v for v in changes)))) if changes else None
        q = self.quote
        valid_q = q and q.eligible and 0 <= now-q.exchange_ns <= NS and decimal(q.ask) > decimal(q.bid) > 0
        spread = decimal(q.ask)-decimal(q.bid) if valid_q else None
        total = q.bid_size+q.ask_size if valid_q and q.size_unit == "shares" else 0
        imbalance = D(q.bid_size-q.ask_size)/total if total else None
        microprice = (decimal(q.ask)*q.bid_size+decimal(q.bid)*q.ask_size)/total if total else None
        return dict(as_of_ns=now, premarket_return=(self.last_pre/self.first_pre-1) if self.first_pre else None,
                    premarket_volume=self.pre_volume, premarket_high=self.pre_high, premarket_low=self.pre_low,
                    opening_return=(self.last/self.open_price-1) if self.open_price and self.last else None,
                    opening_high=self.high, opening_low=self.low, opening_volume=self.volume,
                    opening_range=self.high-self.low if self.high is not None else None,
                    vwap=self.notional/self.volume if self.volume else None,
                    spread=spread, microprice=microprice, top_of_book_imbalance=imbalance,
                    ofi_approximation=self.ofi, aggressive_flow=(D(buy-sell)/(buy+sell)) if buy+sell else None,
                    trade_intensity_10s=D(len(current))/10,
                    volume_acceleration=(D(qty)/prev_qty) if prev_qty else None,
                    realized_volatility_price=volatility, price_velocity=velocity,
                    return_10s=(current[-1][1]/current[0][1]-1) if len(current)>1 else None,
                    relative_volume=None, relative_volume_missing="requires_prior_session_same_time_baseline",
                    pullback_depth=(self.high-self.last)/self.high if self.high and self.last else None,
                    pullback_recovering=bool(self.pullback_seen and self.previous is not None and self.last > self.previous),
                    last=self.last)


def classify(snapshot, *, minimum_direction=D("0.001"), max_spread=D("0.05")):
    """Current regime estimate, not the eventual opening outcome label."""
    pre, opening, spread = (snapshot[k] for k in ("premarket_return", "opening_return", "spread"))
    if pre is None or opening is None or spread is None or spread > max_spread:
        return "NO-TRADE"
    if abs(opening) < minimum_direction:
        return "CHOP"
    if pre > minimum_direction:
        return "CONTINUATION" if opening >= minimum_direction else "REVERSAL"
    return "NO-TRADE"
