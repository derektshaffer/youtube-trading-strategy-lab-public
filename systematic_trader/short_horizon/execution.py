"""One offline L1 simulator: delayed marketable IOC/GTC limits, no queue fills.

The book is an observed NBBO proxy, not the unobserved matching engine. All fills
are conditional simulations. Equal-time market updates cannot affect arrivals.
"""
from dataclasses import dataclass, field, asdict
from collections import defaultdict
from decimal import Decimal
from heapq import heappush, heappop

from ..events import ContractError, integer
from .contracts import NS, decimal

D = Decimal
MARKOUT_NS = tuple(int(D(s) * NS) for s in ("0.1", "0.5", "1", "5", "30", "60"))


@dataclass(frozen=True)
class Costs:
    name: str = "zero_commission_configurable_fees"
    commission_per_share: str = "0"
    minimum_per_order: str = "0"
    fee_per_share: str = "0"
    sell_fee_per_share: str = "0"
    sell_fee_notional_rate: str = "0"
    slippage_per_share: str = "0.001"

    def __post_init__(self):
        for k, v in asdict(self).items():
            if k != "name":
                decimal(v)

    def commission(self, qty):
        return max(decimal(self.minimum_per_order), qty * decimal(self.commission_per_share)) if qty else D(0)

    def fees(self, side, qty, price):
        return qty * decimal(self.fee_per_share) + (qty * decimal(self.sell_fee_per_share) + qty * price * decimal(self.sell_fee_notional_rate) if side == "sell" else D(0))


@dataclass(frozen=True)
class ExecutionPolicy:
    latency_ns: int = 100_000_000
    cancel_latency_ns: int = 100_000_000
    max_quote_age_ns: int = NS
    displayed_fraction: str = "1"
    spread_multiplier: str = "1"

    def __post_init__(self):
        for t in (self.latency_ns, self.cancel_latency_ns, self.max_quote_age_ns):
            integer(t)
        if self.latency_ns < 1 or self.cancel_latency_ns < 1 or self.max_quote_age_ns < 1:
            raise ContractError("short_horizon_positive_latency_and_age_required")
        if not 0 < decimal(self.displayed_fraction) <= 1 or decimal(self.spread_multiplier) < 1:
            raise ContractError("short_horizon_execution_policy_invalid")


@dataclass
class Order:
    id: int
    symbol: str
    side: str
    qty: int
    created_ns: int
    arrival_ns: int
    limit: D | None
    tif: str
    state: str = "CREATED"
    filled: int = 0
    fills: list = field(default_factory=list)
    lifecycle: list = field(default_factory=list)
    reason: str | None = None

    def transition(self, now, state, reason=None):
        self.state = state
        self.reason = reason
        self.lifecycle.append(dict(at_ns=now, state=state, reason=reason))


class Simulator:
    def __init__(self, dataset, policy=None, costs=None):
        self.dataset = dataset
        self.policy = policy or ExecutionPolicy()
        self.costs = costs or Costs()
        self.now = dataset.start_ns
        self.quotes = {}
        self.status = {}
        self.bad = set()
        self.orders = []
        self.resting = defaultdict(dict)
        self.fills = []
        self.tasks = []
        self.serial = 0

    def schedule(self, at, callback):
        if type(at) is not int or at < self.now:
            raise ContractError("short_horizon_timer_regression")
        self.serial += 1
        heappush(self.tasks, (at, self.serial, callback))

    def advance(self, now):
        if now < self.now or now > self.dataset.end_ns:
            raise ContractError("short_horizon_clock_regression_or_outside_coverage")
        while self.tasks and self.tasks[0][0] <= now:
            at, _, callback = heappop(self.tasks)
            if at >= self.dataset.end_ns:
                break
            self.now = at
            callback()
        self.now = now

    def context(self, symbol):
        q = self.quotes.get(symbol)
        session = next((s for s in self.dataset.sessions if s.open_ns <= self.now < s.close_ns), None)
        status = self.status.get(symbol)
        if symbol in self.bad or session is None or status is None or status[0] != "trading" or q is None:
            return None
        e, available, remaining = q
        if status[1] < session.pre_ns or e.exchange_ns < session.open_ns:
            return None
        if not available < self.now or not 0 <= self.now - e.exchange_ns <= self.policy.max_quote_age_ns:
            return None
        if not e.eligible or e.size_unit != "shares":
            return None
        bid, ask = decimal(e.bid), decimal(e.ask)
        if bid <= 0 or ask <= bid or not e.bid_size or not e.ask_size:
            return None
        mid = (bid + ask) / 2
        half = (ask - bid) / 2 * decimal(self.policy.spread_multiplier)
        if mid - half <= 0:
            return None
        return dict(bid=mid-half, ask=mid+half, mid=mid, remaining=remaining, event=e)

    def observe(self, e, available):
        # Caller advances timers first; strategies observe only after this method.
        if available != self.now:
            raise ContractError("short_horizon_market_clock_mismatch")
        if e.kind == "gap":
            self.bad.add(e.symbol)  # Irreversible within this bounded replay.
        elif e.kind == "status":
            self.status[e.symbol] = (e.status, e.exchange_ns)
            if e.status != "trading":
                self.quotes.pop(e.symbol, None)
        elif e.kind == "quote":
            previous = self.quotes.get(e.symbol)
            if previous and e.exchange_ns < previous[0].exchange_ns:
                return  # A late source quote cannot regress a newer book.
            quantities = [int(e.bid_size * decimal(self.policy.displayed_fraction)),
                          int(e.ask_size * decimal(self.policy.displayed_fraction))]
            if previous:
                old, _, remaining = previous
                # Conservative depletion: same-price quote updates do not prove
                # replenishment of quantity already consumed by simulated orders.
                for i, name in enumerate(("bid", "ask")):
                    if getattr(e, name) == getattr(old, name):
                        quantities[i] = min(quantities[i], remaining[i])
            self.quotes[e.symbol] = (e, available, quantities)
            # GTC limits may execute only after a quote becomes strictly available.
            for order in list(self.resting.get(e.symbol, {}).values()):
                if order.state in {"ACTIVE", "PARTIAL"}:
                    self.schedule(available + 1, lambda o=order: self._attempt(o))
        elif e.kind == "trade" and e.eligible:
            previous = self.quotes.get(e.symbol)
            if previous and e.exchange_ns >= previous[0].exchange_ns:
                quote, _, remaining = previous
                # Conservatively consume displayed liquidity hit by later prints;
                # do not infer replenishment or deeper levels from a trade.
                if decimal(e.price) >= decimal(quote.ask):
                    remaining[1] = max(0, remaining[1]-e.size)
                elif decimal(e.price) <= decimal(quote.bid):
                    remaining[0] = max(0, remaining[0]-e.size)

    def submit(self, symbol, side, qty, *, limit=None, tif="IOC"):
        integer(qty)
        if qty < 1 or side not in {"buy", "sell"} or symbol not in self.dataset.symbols or tif not in {"IOC", "GTC"}:
            raise ContractError("short_horizon_simulated_order_invalid")
        price = decimal(limit) if limit is not None else None
        if (price is not None and price <= 0) or (tif == "GTC" and price is None):
            raise ContractError("short_horizon_gtc_requires_limit")
        o = Order(len(self.orders), symbol, side, qty, self.now,
                  self.now + self.policy.latency_ns, price, tif)
        o.transition(self.now, "CREATED")
        o.transition(self.now, "SENT")
        self.orders.append(o)
        self.schedule(o.arrival_ns, lambda: self._arrive(o))
        return o

    def _arrive(self, order):
        if order.state == "CANCELED":
            return
        order.transition(self.now, "ACTIVE")
        if self.dataset.blockers():
            order.transition(self.now, "REJECTED", "dataset_admission_blocked")
            return
        if order.tif == "GTC":
            self.resting[order.symbol][order.id] = order
        self._attempt(order)

    def _attempt(self, o):
        if o.state not in {"ACTIVE", "PARTIAL"}:
            return
        q = self.context(o.symbol)
        reason = "quote_or_status_unavailable"
        if q is not None:
            side = 1 if o.side == "buy" else -1
            index = 1 if side == 1 else 0
            touch = q["ask" if side == 1 else "bid"]
            price = touch + side * decimal(self.costs.slippage_per_share)
            crosses = o.limit is None or (price <= o.limit if side == 1 else price >= o.limit)
            reason = "not_marketable_no_passive_fill"
            if crosses and price > 0:
                qty = min(o.qty - o.filled, q["remaining"][index])
                reason = "insufficient_observed_top_of_book"
                if qty:
                    commission = self.costs.commission(o.filled + qty) - self.costs.commission(o.filled)
                    fill = dict(order_id=o.id, symbol=o.symbol, side=o.side, at_ns=self.now,
                                qty=qty, price=str(price), midpoint=str(q["mid"]),
                                spread_cost=str(qty * abs(touch-q["mid"])),
                                slippage_cost=str(qty * decimal(self.costs.slippage_per_share)),
                                commission=str(commission), fees=str(self.costs.fees(o.side, qty, price)),
                                quote_id=q["event"].event_id, markouts={}, execution_authority="none")
                    q["remaining"][index] -= qty
                    o.filled += qty
                    o.fills.append(fill)
                    self.fills.append(fill)
                    o.transition(self.now, "FILLED" if o.filled == o.qty else "PARTIAL")
                    for horizon in MARKOUT_NS:
                        fill["markouts"][str(horizon)] = None
                        self.schedule(self.now + horizon, lambda f=fill, h=horizon: self._markout(f, h))
        if o.tif == "IOC" and o.state != "FILLED":
            o.transition(self.now, "CANCELED", reason)
        if o.state in {"FILLED", "CANCELED", "REJECTED"}:
            self.resting[o.symbol].pop(o.id, None)

    def cancel(self, order):
        if not any(order is item for item in self.orders):
            raise ContractError("short_horizon_unknown_order")
        order.lifecycle.append(dict(at_ns=self.now, state="CANCEL_REQUESTED", reason=None))
        def effective():
            if order.state not in {"FILLED", "CANCELED", "REJECTED"}:
                order.transition(self.now, "CANCELED", "cancel_effective")
                self.resting[order.symbol].pop(order.id, None)
        self.schedule(self.now + self.policy.cancel_latency_ns, effective)

    def _markout(self, fill, horizon):
        q = self.context(fill["symbol"])
        if q is not None:
            side = 1 if fill["side"] == "buy" else -1
            fill["markouts"][str(horizon)] = str(side * (q["mid"] - D(fill["midpoint"])))


def break_even_midpoint_move(qty, entry_spread, exit_spread, costs, entry_price):
    """Round-trip cost per share; not a win-rate or profitability claim."""
    integer(qty)
    if qty < 1:
        raise ContractError("short_horizon_break_even_quantity")
    friction = ((decimal(entry_spread) + decimal(exit_spread)) / 2
                + 2 * decimal(costs.slippage_per_share)
                + 2 * costs.commission(qty) / qty
                + (costs.fees("buy", qty, decimal(entry_price))
                   + costs.fees("sell", qty, decimal(entry_price))) / qty)
    # Sell notional fee grows with the required exit price.
    rate = decimal(costs.sell_fee_notional_rate)
    if rate >= 1:
        raise ContractError("short_horizon_fee_rate_invalid")
    return friction / (1-rate)
