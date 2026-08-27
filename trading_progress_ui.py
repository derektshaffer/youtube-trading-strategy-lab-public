"""Reusable progress estimation helpers for long-running Trading Lab actions.

UI convention for long jobs:
- clicked button becomes a disabled "...ing" state immediately;
- show an explicitly estimated percentage when backend work is not uniformly weighted;
- keep the visible activity panel compact and focused on the newest actions;
- retain the full activity history in an optional expander.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


def format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m"
    if minutes:
        return f"{minutes:d}m {seconds:02d}s"
    return f"{seconds:d}s"


@dataclass
class AutonomousResearchProgressEstimator:
    """Estimate Autopilot completion from its existing structured stage messages.

    The work inside optimization/API calls is not uniform, so this is deliberately
    labeled an estimate. Progress never moves backward.
    """

    fraction: float = 0.0
    deep_index: int = 0
    deep_total: int = 0

    @staticmethod
    def _ratio(message: str, pattern: str) -> tuple[int, int] | None:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if not match:
            return None
        current = max(0, int(match.group(1)))
        total = max(1, int(match.group(2)))
        return current, total

    def update(self, message: str) -> float:
        text = str(message or "").strip()
        lower = text.casefold()
        candidate = self.fraction

        if "collapsed" in lower and "root strategy" in lower:
            candidate = max(candidate, 0.01)
        elif "loading current movers" in lower:
            candidate = max(candidate, 0.02)
        elif "loading alpaca active + inactive" in lower:
            candidate = max(candidate, 0.05)
        elif lower.startswith("screening ") and "daily history" in lower:
            candidate = max(candidate, 0.08)
        else:
            batch = self._ratio(text, r"Historical\s+1Day\s+batch\s+(\d+)\s+of\s+(\d+)")
            if batch:
                current, total = batch
                candidate = max(candidate, 0.08 + 0.27 * min(1.0, current / total))
            elif "selecting historical event windows" in lower:
                candidate = max(candidate, 0.38)
            else:
                intraday = self._ratio(
                    text,
                    r"Point-in-time\s+intraday\s+window\s+(\d+)\s*/\s*(\d+)",
                )
                if intraday:
                    current, total = intraday
                    candidate = max(candidate, 0.38 + 0.17 * min(1.0, current / total))
                elif "loading point-in-time catalyst news" in lower:
                    candidate = max(candidate, 0.56)
                else:
                    catalyst = self._ratio(
                        text,
                        r"Historical\s+catalyst\s+window\s+(\d+)\s*/\s*(\d+)",
                    )
                    if catalyst:
                        current, total = catalyst
                        candidate = max(candidate, 0.56 + 0.09 * min(1.0, current / total))
                    else:
                        deep = self._ratio(text, r"Deep\s+research\s+(\d+)\s*/\s*(\d+)")
                        if deep:
                            self.deep_index, self.deep_total = deep
                            span = 0.34 / self.deep_total
                            candidate = max(
                                candidate,
                                0.65 + span * (self.deep_index - 1) + span * 0.10,
                            )
                        elif self.deep_index and self.deep_total:
                            span = 0.34 / self.deep_total
                            base = 0.65 + span * (self.deep_index - 1)
                            if "rolling walk-forward" in lower:
                                candidate = max(candidate, base + span * 0.50)
                            elif "testing frozen" in lower:
                                candidate = max(candidate, base + span * 0.82)

        # Leave room for saving/merging the final report.
        self.fraction = min(0.99, max(self.fraction, candidate))
        return self.fraction

    @property
    def percent(self) -> int:
        return int(round(self.fraction * 100))
