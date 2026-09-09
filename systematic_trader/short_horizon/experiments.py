"""A–E share one event replay and simulator. Transparent long-only baselines."""
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from decimal import Decimal

from ..events import ContractError, digest, integer
from .contracts import NS, decimal
from .execution import Simulator
from .features import Features, classify
from .replay import replay
from .diagnostics import WindowStats

D = Decimal
OBSERVATION_MINUTES = (0, 2, 5, 10, 15)
HOLD_SECONDS = (10, 30, 60, 120, 300)
LATENCY_MS = (50, 100, 250, 500, 1000)
OPEN_WINDOWS = ((0, 15), (5, 30), (10, 30), (10, 60), (30, 60), (120, 180))
# Relative to the declared close; correctly follows an explicitly supplied early close.
CLOSE_WINDOWS = ((-90, -60), (-60, -30), (-30, -15), (-15, 0))


@dataclass(frozen=True)
class Experiment:
    family: str = "B"
    style: str = "unconditional"
    observation_minutes: int = 0
    window_minutes: tuple[int, int] = (0, 60)
    window_anchor: str = "open"
    hold_seconds: int = 30
    target_mode: str = "cents"
    target: str = "0.02"
    qty: int = 100
    episode_seconds: int = 300
    decision_interval_ns: int = NS
    minimum_direction: str = "0.001"
    max_spread: str = "0.05"
    minimum_acceleration: str = "1"
    maximum_pullback: str = "0.01"
    version: str = "transparent-long-baselines-v1"

    def __post_init__(self):
        if self.family not in {"A", "B", "C", "D", "E"} or self.style not in {"unconditional", "breakout", "pullback"}:
            raise ContractError("short_horizon_experiment_invalid")
        if self.window_anchor not in {"open", "close"} or len(self.window_minutes) != 2 or self.window_minutes[0] >= self.window_minutes[1]:
            raise ContractError("short_horizon_window_invalid")
        for t in (self.observation_minutes, self.hold_seconds, self.qty, self.episode_seconds, self.decision_interval_ns):
            integer(t)
        if self.qty < 1 or self.hold_seconds < 1 or self.episode_seconds < self.hold_seconds or self.decision_interval_ns < 1:
            raise ContractError("short_horizon_horizon_or_episode_invalid")
        if self.target_mode not in {"cents", "percentage", "volatility", "spread"}:
            raise ContractError("short_horizon_target_mode_invalid")
        for k in ("target", "minimum_direction", "max_spread", "minimum_acceleration", "maximum_pullback"):
            if decimal(getattr(self, k)) <= 0:
                raise ContractError("short_horizon_threshold_invalid")

    def bounds(self, session):
        anchor = session.open_ns if self.window_anchor == "open" else session.close_ns
        start = max(session.open_ns + self.observation_minutes*60*NS, anchor+self.window_minutes[0]*60*NS)
        end = min(session.close_ns, anchor+self.window_minutes[1]*60*NS)
        return start, end


def target_distance(config, snapshot):
    scale = {"cents": D(1), "percentage": snapshot["last"],
             "volatility": snapshot["realized_volatility_price"], "spread": snapshot["spread"]}[config.target_mode]
    return decimal(config.target)*scale if scale is not None and scale > 0 else None


class Harness:
    def __init__(self, dataset, experiment, policy=None, costs=None):
        self.dataset, self.config = dataset, experiment
        self.sim = Simulator(dataset, policy, costs)
        self.features = {}
        self.active = {}
        self.next_episode = {}
        self.next_decision = {}
        self.episodes = []
        self.gross_pending = defaultdict(list)
        self.counts = Counter()
        self.no_trade_reasons = Counter()
        self.observations = []
        self.forensics = {}
        self.window_stats = {}
        self.stats_by_key = {}
        self.exposure_ns = 0
        for session in dataset.sessions:
            start, end = experiment.bounds(session)
            self.exposure_ns += max(0, min(end, dataset.end_ns)-max(start, dataset.start_ns)) * len(dataset.symbols)
            for symbol in dataset.symbols:
                key = (symbol, session.day)
                self.features[key] = Features(session)
                windows = [("open_"+str(a)+"_"+str(b),session.open_ns+a*60*NS,session.open_ns+b*60*NS) for a,b in OPEN_WINDOWS]
                windows += [("close_"+str(a)+"_"+str(b),session.close_ns+a*60*NS,session.close_ns+b*60*NS) for a,b in CLOSE_WINDOWS]
                for name,a,b in windows:
                    a,b=max(a,dataset.start_ns,session.open_ns),min(b,dataset.end_ns,session.close_ns)
                    if a < b:
                        stats = WindowStats(a,b)
                        self.window_stats[(symbol,session.day,name)] = stats
                        self.stats_by_key.setdefault(key,[]).append(stats)
                for minutes in OBSERVATION_MINUTES:
                    at = session.open_ns+minutes*60*NS
                    if dataset.start_ns <= at < dataset.end_ns:
                        self.sim.schedule(at, lambda k=key, m=minutes: self._observation(k, m))

    def _observation(self, key, minutes):
        f = self.features[key].snapshot(self.sim.now)
        complete = self.dataset.start_ns <= self.features[key].session.pre_ns and bool(self.dataset.evidence.get("premarket_complete"))
        self.observations.append(dict(symbol=key[0], day=key[1], minutes=minutes,
                                      at_ns=self.sim.now, regime=classify(f) if complete else "NO-TRADE",
                                      premarket_coverage_established=complete,
                                      premarket_return=str(f["premarket_return"]) if f["premarket_return"] is not None else None,
                                      opening_return=str(f["opening_return"]) if f["opening_return"] is not None else None,
                                      role="as_of_observation_not_future_label"))

    def observe(self, e, now):
        self.counts[e.kind] += 1
        s = next(s for s in self.dataset.sessions if s.pre_ns <= now < s.end_ns)
        key = (e.symbol, s.day)
        features = self.features[key]
        features.observe(e, now)
        for stats in self.stats_by_key.get(key,[]):
            stats.observe(e,now)
        if e.kind == "trade":
            record = self.forensics.setdefault(e.symbol, dict(trades=0, volume=0, first_trade=None, last_trade=None,
                min_trade=None, max_trade=None, at_or_below_024_ns=None, later_at_or_above_041_ns=None))
            p = decimal(e.price)
            record["trades"] += 1; record["volume"] += e.size
            record["first_trade"] = record["first_trade"] or e.price
            record["last_trade"] = e.price
            record["min_trade"] = str(min(p, D(record["min_trade"]))) if record["min_trade"] is not None else e.price
            record["max_trade"] = str(max(p, D(record["max_trade"]))) if record["max_trade"] is not None else e.price
            if e.symbol == "RIV.RT":
                if p <= D("0.24") and record["at_or_below_024_ns"] is None:
                    record["at_or_below_024_ns"] = now
                if p >= D("0.41") and record["at_or_below_024_ns"] is not None and now > record["at_or_below_024_ns"]:
                    record["later_at_or_above_041_ns"] = record["later_at_or_above_041_ns"] or now
        active = self.active.get(key)
        self.gross_pending[key] = [ep for ep in self.gross_pending[key] if now <= ep["created_ns"]+self.config.hold_seconds*NS]
        if e.kind == "trade" and e.eligible:
            for ep in self.gross_pending[key]:
                if now > ep["created_ns"] and decimal(e.price) >= D(ep["signal_price"])+D(ep["target"]):
                    ep["gross_target_hit"] = True
        if active:
            if e.kind == "quote":
                self.sim.schedule(now+1, lambda a=active: self._exit_if_target(a))
        if e.kind != "trade" or not e.eligible or s.segment(now) != "regular":
            return
        start, end = self.config.bounds(s)
        last_entry = min(end, s.close_ns, self.dataset.end_ns)-self.config.hold_seconds*NS-2*self.sim.policy.latency_ns-2
        if not start <= now < last_entry or now < self.next_episode.get(key, 0) or key in self.active:
            return
        if now < self.next_decision.get(key, 0):
            return
        self.next_decision[key] = now + self.config.decision_interval_ns
        if self.config.family == "A" and e.symbol != "RIV.RT":
            return
        if self.config.style in {"breakout", "pullback"} and (self.dataset.start_ns > s.pre_ns or not self.dataset.evidence.get("premarket_complete")):
            self.no_trade_reasons["premarket_coverage_not_established"] += 1
            return
        f = features.snapshot(now)
        regime = classify(f, minimum_direction=decimal(self.config.minimum_direction), max_spread=decimal(self.config.max_spread))
        reason = self._signal_reason(f, regime)
        if reason:
            self.no_trade_reasons[reason] += 1
            return
        target = target_distance(self.config, f)
        if target is None:
            self.no_trade_reasons["target_feature_missing"] += 1
            return
        self.next_episode[key] = now + self.config.episode_seconds*NS
        entry = self.sim.submit(e.symbol, "buy", self.config.qty)
        ep = dict(id=len(self.episodes), symbol=e.symbol, day=s.day, weekday=s.weekday,
                  regime=s.regime if s.regime_known_ns is not None and s.regime_known_ns <= now else "unknown",
                  opening_regime=regime, created_ns=now, signal_price=e.price,
                  feature_hash=digest({k: str(v) if isinstance(v, D) else v for k, v in f.items()}),
                  target=str(target), entry=entry, exits=[], gross_target_hit=False,
                  target_bid_seen=False, exit_due=False, pending_exit=False,
                  status="ENTRY_PENDING", key=key)
        self.episodes.append(ep); self.active[key] = ep
        self.gross_pending[key].append(ep)
        self.sim.schedule(entry.arrival_ns+1, lambda: self._entered(ep))

    def _signal_reason(self, f, regime):
        if f["spread"] is None or f["spread"] > decimal(self.config.max_spread):
            return "spread_missing_or_above_limit"
        if self.config.style == "unconditional":
            return None
        if regime != "CONTINUATION":
            return "opening_regime_" + regime
        if f["volume_acceleration"] is None or f["volume_acceleration"] < decimal(self.config.minimum_acceleration):
            return "flow_acceleration_missing_or_weak"
        if f["aggressive_flow"] is None or f["aggressive_flow"] <= 0 or f["price_velocity"] is None or f["price_velocity"] <= 0:
            return "positive_flow_not_established"
        if self.config.style == "breakout" and f["last"] < f["opening_high"]:
            return "not_at_opening_high"
        if self.config.style == "pullback" and (not f["pullback_recovering"] or f["pullback_depth"] > decimal(self.config.maximum_pullback) or f["last"] < f["vwap"]):
            return "controlled_pullback_recovery_missing"
        return None

    def _entered(self, ep):
        entry = ep["entry"]
        if not entry.filled:
            ep["status"] = "NO_FILL"
            self.active.pop(ep["key"], None)
            return
        ep["status"] = "OPEN"
        ep["entry_price"] = str(sum(D(f["price"])*f["qty"] for f in entry.fills)/entry.filled)
        self.sim.schedule(entry.arrival_ns+self.config.hold_seconds*NS, lambda: self._time_exit(ep))

    def _time_exit(self, ep):
        ep["exit_due"] = True
        self._exit_if_target(ep)

    def _exit_if_target(self, ep):
        if ep["status"] != "OPEN" or ep["pending_exit"]:
            return
        q = self.sim.context(ep["symbol"])
        if q is not None and q["bid"] >= D(ep["entry_price"])+D(ep["target"]):
            ep["target_bid_seen"] = True
            ep["exit_due"] = True
        if not ep["exit_due"]:
            return
        remaining = ep["entry"].filled-sum(o.filled for o in ep["exits"])
        if remaining:
            order = self.sim.submit(ep["symbol"], "sell", remaining)
            ep["exits"].append(order); ep["pending_exit"] = True
            self.sim.schedule(order.arrival_ns+1, lambda: self._exited(ep))

    def _exited(self, ep):
        ep["pending_exit"] = False
        if sum(o.filled for o in ep["exits"]) == ep["entry"].filled:
            ep["status"] = "CLOSED"
            self.active.pop(ep["key"], None)

    def run(self, events):
        self.event_count = replay(events, self.sim, self.observe)
        for ep in self.episodes:
            if ep["status"] in {"OPEN", "ENTRY_PENDING"}:
                ep["status"] = "UNRESOLVED_AT_COVERAGE_END"
        from .results import report
        return report(self)


def experiment_grid(family):
    """Predeclared comparisons; no parameter selection from observed performance."""
    if family in {"A", "B", "E"}:
        targets = [("cents", t) for t in ("0.01", "0.02", "0.03", "0.05")]
        if family != "A":
            targets += [("percentage", "0.001"), ("volatility", "1"), ("spread", "2")]
        return [Experiment(family=family, target_mode=mode, target=t) for mode, t in targets]
    if family == "C":
        momentum = [Experiment(family="C", style=style, observation_minutes=o, window_minutes=w, hold_seconds=h)
                for style in ("breakout", "pullback") for o in OBSERVATION_MINUTES
                for w in OPEN_WINDOWS for h in HOLD_SECONDS if max(o, w[0])*60+h < w[1]*60]
        return momentum + [Experiment(family="C", style="unconditional", observation_minutes=o, hold_seconds=h)
                           for o in OBSERVATION_MINUTES for h in HOLD_SECONDS]
    if family == "D":
        return [Experiment(family="D", window_anchor="close", window_minutes=w) for w in CLOSE_WINDOWS]
    raise ContractError("short_horizon_unknown_experiment_family")
