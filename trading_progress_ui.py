"""Reusable progress + learned ETA helpers for long-running Trading Lab actions.

UI convention for long jobs:
- clicked button becomes a disabled "...ing" state immediately;
- show an explicitly estimated percentage when backend work is not uniformly weighted;
- estimate remaining time from completed runs, never from a fake countdown;
- keep the visible activity panel compact and focused on the newest actions;
- retain the full activity history in an optional expander.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, field
from typing import Any


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


@dataclass
class AutonomousResearchTimingRecorder:
    """Collect a compact timing profile for future learned ETA estimates."""

    samples: list[dict[str, Any]] = field(default_factory=list)
    _last_fraction: float = -1.0

    def record(self, fraction: float, elapsed_seconds: float, message: str = "") -> None:
        fraction = max(0.0, min(1.0, float(fraction)))
        elapsed_seconds = max(0.0, float(elapsed_seconds))

        # Save only meaningful forward milestones; repeated log lines at the same
        # percentage do not improve future ETA estimates.
        if self.samples and fraction <= self._last_fraction + 0.004:
            return
        self.samples.append(
            {
                "fraction": round(fraction, 4),
                "elapsed_seconds": round(elapsed_seconds, 2),
                "message": str(message or "")[:180],
            }
        )
        self._last_fraction = fraction

    def finish(
        self,
        total_seconds: float,
        *,
        deep_strategies_attempted: int | None = None,
        universe_sample_size: int | None = None,
    ) -> dict[str, Any]:
        total_seconds = max(0.1, float(total_seconds))
        if not self.samples or self.samples[-1].get("fraction") != 1.0:
            self.samples.append(
                {
                    "fraction": 1.0,
                    "elapsed_seconds": round(total_seconds, 2),
                    "message": "Research complete",
                }
            )
        return {
            "version": 1,
            "total_seconds": round(total_seconds, 2),
            "deep_strategies_attempted": deep_strategies_attempted,
            "universe_sample_size": universe_sample_size,
            "samples": self.samples[-80:],
        }


def autonomous_timing_profiles(
    research_runs: list[dict[str, Any]] | None,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for run in research_runs or []:
        if not isinstance(run, dict) or run.get("kind") != "autonomous_research":
            continue
        profile = run.get("timing_profile")
        if not isinstance(profile, dict):
            continue
        total = profile.get("total_seconds")
        samples = profile.get("samples")
        try:
            total_value = float(total)
        except (TypeError, ValueError):
            continue
        if total_value <= 0 or not isinstance(samples, list) or len(samples) < 2:
            continue
        profiles.append(profile)
        if len(profiles) >= max(1, int(limit)):
            break
    return profiles


def _elapsed_at_fraction(profile: dict[str, Any], fraction: float) -> float | None:
    fraction = max(0.0, min(1.0, float(fraction)))
    points: list[tuple[float, float]] = []
    for sample in profile.get("samples") or []:
        if not isinstance(sample, dict):
            continue
        try:
            point_fraction = float(sample.get("fraction"))
            point_elapsed = float(sample.get("elapsed_seconds"))
        except (TypeError, ValueError):
            continue
        if point_fraction < 0 or point_fraction > 1 or point_elapsed < 0:
            continue
        points.append((point_fraction, point_elapsed))

    try:
        total = float(profile.get("total_seconds"))
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None

    points.append((1.0, total))
    points = sorted(set(points))
    if not points:
        return None
    if fraction <= points[0][0]:
        first_fraction, first_elapsed = points[0]
        if first_fraction <= 0:
            return first_elapsed
        return first_elapsed * (fraction / first_fraction)

    for (left_f, left_t), (right_f, right_t) in zip(points, points[1:]):
        if left_f <= fraction <= right_f:
            if right_f <= left_f:
                return right_t
            weight = (fraction - left_f) / (right_f - left_f)
            return left_t + (right_t - left_t) * weight
    return total


@dataclass
class AutonomousResearchEtaEstimator:
    """Estimate remaining duration from actual completed Autopilot timing profiles."""

    profiles: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_research_runs(
        cls,
        research_runs: list[dict[str, Any]] | None,
    ) -> "AutonomousResearchEtaEstimator":
        return cls(autonomous_timing_profiles(research_runs))

    @property
    def sample_count(self) -> int:
        return len(self.profiles)

    def estimate_range(
        self,
        fraction: float,
        *,
        current_elapsed_seconds: float | None = None,
    ) -> tuple[float, float] | None:
        fraction = max(0.0, min(0.999, float(fraction)))
        # The earliest setup messages are too coarse to support a useful ETA.
        if fraction < 0.08 or not self.profiles:
            return None

        estimates: list[float] = []
        for profile in self.profiles:
            elapsed_at_fraction = _elapsed_at_fraction(profile, fraction)
            if elapsed_at_fraction is None:
                continue
            try:
                total = float(profile.get("total_seconds"))
            except (TypeError, ValueError):
                continue
            remaining = max(0.0, total - elapsed_at_fraction)

            # Adapt historical timing to today's observed speed, but clamp heavily so
            # one slow/fast API response cannot swing the ETA wildly.
            if (
                current_elapsed_seconds is not None
                and current_elapsed_seconds >= 8.0
                and elapsed_at_fraction >= 5.0
            ):
                speed_ratio = float(current_elapsed_seconds) / elapsed_at_fraction
                speed_ratio = max(0.65, min(1.55, speed_ratio))
                remaining *= speed_ratio

            estimates.append(remaining)

        if not estimates:
            return None

        estimates.sort()
        center = statistics.median(estimates)
        if len(estimates) == 1:
            low = center * 0.65
            high = center * 1.50
        elif len(estimates) <= 3:
            low = min(estimates) * 0.85
            high = max(estimates) * 1.20
        else:
            q1_index = max(0, math.floor((len(estimates) - 1) * 0.25))
            q3_index = min(len(estimates) - 1, math.ceil((len(estimates) - 1) * 0.75))
            low = estimates[q1_index] * 0.90
            high = estimates[q3_index] * 1.12

        low = max(0.0, low)
        high = max(low, high)
        return low, high


def _coarse_duration(seconds: float, *, round_up: bool) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return "<1 min"
    minutes = seconds / 60.0
    if minutes < 60:
        value = math.ceil(minutes) if round_up else max(1, math.floor(minutes))
        return f"{value} min"
    hours = minutes / 60.0
    # Keep long ETAs intentionally coarse.
    value = math.ceil(hours * 2) / 2 if round_up else max(0.5, math.floor(hours * 2) / 2)
    if float(value).is_integer():
        return f"{int(value)} hr"
    return f"{value:.1f} hr"


def format_eta_range(eta_range: tuple[float, float] | None) -> str | None:
    if eta_range is None:
        return None
    low, high = eta_range
    if high < 60:
        return "less than 1 min"
    low_text = _coarse_duration(low, round_up=False)
    high_text = _coarse_duration(high, round_up=True)
    if low_text == high_text:
        return f"about {high_text}"
    return f"about {low_text}–{high_text}"


SESSION_TIMING_STATE_KEY = "_trading_lab_long_task_timings"


def session_task_profiles(
    state: Any,
    task_key: str,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Read recent completed timing profiles for one UI task from Streamlit session state."""
    try:
        all_profiles = state.get(SESSION_TIMING_STATE_KEY) or {}
    except AttributeError:
        return []
    if not isinstance(all_profiles, dict):
        return []
    values = all_profiles.get(str(task_key)) or []
    if not isinstance(values, list):
        return []
    return [item for item in values[: max(1, int(limit))] if isinstance(item, dict)]


def save_session_task_profile(
    state: Any,
    task_key: str,
    profile: dict[str, Any],
    *,
    limit: int = 12,
) -> None:
    """Keep lightweight learned ETA history without touching trading/research records."""
    try:
        all_profiles = dict(state.get(SESSION_TIMING_STATE_KEY) or {})
    except AttributeError:
        return
    current = [
        item
        for item in all_profiles.get(str(task_key)) or []
        if isinstance(item, dict)
    ]
    all_profiles[str(task_key)] = [dict(profile), *current][: max(1, int(limit))]
    state[SESSION_TIMING_STATE_KEY] = all_profiles


@dataclass
class LongTaskMonitor:
    """Generic percentage + learned-ETA tracker for non-Autopilot long actions."""

    task_key: str
    profiles: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=lambda: __import__("time").monotonic())
    recorder: AutonomousResearchTimingRecorder = field(
        default_factory=AutonomousResearchTimingRecorder
    )

    def __post_init__(self) -> None:
        self.eta_estimator = AutonomousResearchEtaEstimator(list(self.profiles))

    def text(
        self,
        fraction: float,
        message: str = "",
    ) -> str:
        import time

        fraction = max(0.0, min(0.999, float(fraction)))
        elapsed = time.monotonic() - self.started_at
        self.recorder.record(fraction, elapsed, message)
        eta = self.eta_estimator.estimate_range(
            fraction,
            current_elapsed_seconds=elapsed,
        )
        eta_text = format_eta_range(eta)
        progress_text = f"Estimated progress: {int(round(fraction * 100))}%"
        if eta_text:
            progress_text += f" · Estimated time remaining: {eta_text}"
        else:
            progress_text += " · Estimating time remaining…"
        if message:
            progress_text += f" · {message}"
        return progress_text

    def finish(self, state: Any | None = None) -> dict[str, Any]:
        import time

        profile = self.recorder.finish(time.monotonic() - self.started_at)
        if state is not None:
            save_session_task_profile(state, self.task_key, profile)
        return profile
