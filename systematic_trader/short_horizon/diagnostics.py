"""Observed opening/closing/weekday descriptions, separate from executable P&L."""
from dataclasses import dataclass
from decimal import Decimal
from math import log, sqrt

from .contracts import NS, decimal


@dataclass
class WindowStats:
    start_ns: int
    end_ns: int
    trades: int = 0
    eligible_trades: int = 0
    quote_updates: int = 0
    valid_quotes: int = 0
    volume: int = 0
    first_price: Decimal | None = None
    last_price: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    spread_sum: Decimal = Decimal(0)
    squared_log_returns: float = 0

    def observe(self, event, now):
        if not self.start_ns <= now < self.end_ns:
            return
        if event.kind == "quote":
            self.quote_updates += 1
            if event.eligible and decimal(event.ask) > decimal(event.bid) > 0:
                self.valid_quotes += 1
                self.spread_sum += decimal(event.ask)-decimal(event.bid)
        elif event.kind == "trade":
            self.trades += 1
            if not event.eligible:
                return
            p = decimal(event.price)
            self.eligible_trades += 1; self.volume += event.size
            if self.last_price is not None:
                self.squared_log_returns += log(float(p/self.last_price))**2
            self.first_price = p if self.first_price is None else self.first_price
            self.last_price = p
            self.high = p if self.high is None else max(p,self.high)
            self.low = p if self.low is None else min(p,self.low)

    def result(self):
        seconds = Decimal(max(0,self.end_ns-self.start_ns))/NS
        ret = self.last_price/self.first_price-1 if self.first_price else None
        return dict(start_ns=self.start_ns,end_ns=self.end_ns,trade_count=self.trades,
            eligible_trades=self.eligible_trades,quote_updates=self.quote_updates,volume=self.volume,
            trade_frequency_per_second=str(Decimal(self.eligible_trades)/seconds) if seconds else None,
            mean_observed_quote_spread=str(self.spread_sum/self.valid_quotes) if self.valid_quotes else None,
            spread_weighting="quote_update_weighted_not_time_weighted",
            gross_first_to_last_return=str(ret) if ret is not None else None,
            realized_trade_return_volatility=sqrt(self.squared_log_returns) if self.eligible_trades > 1 else None,
            high=str(self.high) if self.high is not None else None,low=str(self.low) if self.low is not None else None,
            large_upside_event=ret >= Decimal("0.05") if ret is not None else None,
            large_downside_event=ret <= Decimal("-0.05") if ret is not None else None,
            large_move_threshold="5_percent_first_to_last_not_executable",relative_volume=None,
            relative_volume_reason="prior_session_same_time_baseline_not_supplied",
            role="descriptive_tape_diagnostic_no_profitability_claim")
