"""Comparable elapsed-time history chosen by calendar, never data quality."""
from .events import ContractError
from .features import MINUTE


def comparable_indices(days, calendar, *, before_day, elapsed, count=14):
    if days!=sorted(set(days)) or type(elapsed) is not int or elapsed<1 or type(count) is not int or count<1:
        raise ContractError('invalid_comparable_calendar_context')
    # A missing/invalid price profile is deliberately not an input to selection.
    eligible=[i for i,d in enumerate(days) if d<before_day and
              (calendar[d]['close']-calendar[d]['open'])//MINUTE>=elapsed]
    return eligible[-count:]
