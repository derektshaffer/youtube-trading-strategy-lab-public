"""Plain-English hover definitions for Explosive Stock Lab metrics."""

from __future__ import annotations

from functools import wraps
from typing import Any

import streamlit as st
from streamlit.delta_generator import DeltaGenerator


TERM_HELP: dict[str, str] = {
    "Explosion profile": (
        "Experimental 0–100 heuristic for how strongly the stock matches the app's "
        "explosive-mover profile. It is not a probability of profit or a buy signal."
    ),
    "Activation": (
        "Current stage of the explosive setup. ACTIVE or IGNITING means the ingredients "
        "are showing up now; EARLY WATCH is weaker/earlier; EXTENDED / CHASE RISK means "
        "the move may already be stretched."
    ),
    "Risk": (
        "Experimental 0–100 risk score. Higher values mean more warning signs or "
        "structural/execution risk. It is not a guarantee of loss or safety."
    ),
    "Day move": (
        "Percent price change for the current/latest session versus the previous close."
    ),
    "RVOL": (
        "Relative volume: current trading volume compared with normal volume for the "
        "same context. Above 1× means above normal; 2× is roughly twice normal."
    ),
    "Spread": (
        "Bid–ask spread as a percent of price. Smaller is usually easier/cheaper to "
        "enter and exit; large spreads increase slippage risk."
    ),
    "ATR %": (
        "Average True Range expressed as a percent of price. It measures typical price "
        "movement/volatility; higher values mean larger normal swings."
    ),
    "Volume acceleration": (
        "Compares very recent intraday volume pace with the preceding pace. Above 1× "
        "means volume is speeding up; 1.5× means about 50% faster."
    ),
    "10-day range": (
        "Percent distance from the lowest low to the highest high across the last 10 "
        "completed daily bars. Larger values mean a wider recent price range."
    ),
    "5d/20d compression": (
        "Average daily range over the last 5 completed days divided by the average "
        "daily range of the preceding 20 days. 0.63× means recent daily ranges are "
        "about 63% of that prior baseline. Below 1× = compression; above 1× = expansion. "
        "Compression alone is not bullish or bearish."
    ),
    "Largest recent 1-day gain": (
        "Biggest close-to-close percentage gain among roughly the last 60 completed "
        "daily returns. It shows whether the ticker has recently demonstrated large "
        "single-day upside."
    ),
    "20% runner days": (
        "Number of roughly the last 60 completed daily returns that gained at least 20%."
    ),
    "30% runner days": (
        "Number of roughly the last 60 completed daily returns that gained at least 30%."
    ),
    "Distance from 60d high": (
        "How far the latest close sits below the highest price of the last 60 completed "
        "daily bars. 0% means the stock is at its 60-day high; larger values are farther below it."
    ),
}


def term_help(label: Any) -> str | None:
    """Return hover help for a known Explosive Stock Lab label."""
    return TERM_HELP.get(str(label))


def _add_help(label: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    # Streamlit's metric positional order is delta, delta_color, help after label/value.
    # Do not overwrite an explicitly supplied positional or keyword help value.
    if len(args) >= 3 or "help" in kwargs:
        return kwargs
    help_text = term_help(label)
    if help_text:
        kwargs = dict(kwargs)
        kwargs["help"] = help_text
    return kwargs


def install_metric_help() -> None:
    """Add Explosive Stock Lab hover help without changing metric values or scoring."""
    if not getattr(DeltaGenerator.metric, "_explosive_help_wrapper", False):
        original_metric = DeltaGenerator.metric

        @wraps(original_metric)
        def metric_with_help(
            self: DeltaGenerator,
            label: Any,
            value: Any,
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            return original_metric(self, label, value, *args, **_add_help(label, args, kwargs))

        metric_with_help._explosive_help_wrapper = True  # type: ignore[attr-defined]
        DeltaGenerator.metric = metric_with_help  # type: ignore[method-assign]

    if not getattr(st.metric, "_explosive_help_wrapper", False):
        original_st_metric = st.metric

        @wraps(original_st_metric)
        def st_metric_with_help(label: Any, value: Any, *args: Any, **kwargs: Any) -> Any:
            return original_st_metric(label, value, *args, **_add_help(label, args, kwargs))

        st_metric_with_help._explosive_help_wrapper = True  # type: ignore[attr-defined]
        st.metric = st_metric_with_help  # type: ignore[assignment]
