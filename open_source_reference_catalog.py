"""Curated open-source implementation references for Trading Intelligence Lab.

These records are references, not trading evidence. A repository can help us
cross-check formulas, discover missing capabilities, or design tests without
implying that its strategy is profitable or safe.

Licensing posture is intentionally conservative:
- candidate_dependency: permissive license appears compatible, still review before use.
- reference_only: do not copy/import code into the app; independently implement concepts.
- methodology_reference: useful mainly for research design/falsification ideas.
"""

from __future__ import annotations

from typing import Any


OPEN_SOURCE_REFERENCE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "repository": "s-kust/anchored_vwaps",
        "category": "Anchored VWAP",
        "usefulness": "Multiple AVWAPs, anchor-date handling, significant high/low anchor ideas, chart verification.",
        "license": "GPL-3.0",
        "posture": "reference_only",
        "why": "Directly relevant to AVWAP anchor selection and multi-anchor research, but copyleft licensing and retrospective pivot logic require independent causal implementation.",
    },
    {
        "repository": "xgboosted/pandas-ta-classic",
        "category": "Indicators / patterns",
        "usefulness": "Large indicator and candlestick-pattern library; useful for independent formula cross-checks and vocabulary expansion.",
        "license": "MIT",
        "posture": "candidate_dependency",
        "why": "Permissive license and broad coverage make it a strong candidate for validation/reference after compatibility review.",
    },
    {
        "repository": "bukosabino/ta",
        "category": "Indicators",
        "usefulness": "Independent Pandas/Numpy technical-indicator implementation for cross-checking EMA, ATR, volume, trend, and volatility calculations.",
        "license": "MIT",
        "posture": "candidate_dependency",
        "why": "Useful as an independent oracle in tests so one implementation does not validate itself.",
    },
    {
        "repository": "pedrobraiti/volume-profile-trading",
        "category": "Volume profile / falsification",
        "usefulness": "POC, value-area, exhaustion, walk-forward, cost tests, permutation/ablation/random-entry falsification.",
        "license": "MIT",
        "posture": "methodology_reference",
        "why": "Especially useful for research design: it tries to disprove an apparent edge instead of only optimizing it.",
    },
    {
        "repository": "alpacahq/example-hftish",
        "category": "Order book / imbalance",
        "usefulness": "Streaming order-book imbalance example and event-driven microstructure workflow.",
        "license": "Unknown / review required",
        "posture": "reference_only",
        "why": "Useful for understanding live imbalance features, but the example is old and its historical data assumptions do not solve our point-in-time L2 dataset gap.",
    },
    {
        "repository": "CameronScarpati/lob-regime-scanner",
        "category": "Level 2 / regime detection",
        "usefulness": "Order-flow imbalance, VPIN, microstructure features, HMM regimes, and explicit causal-filtered versus retrospective-smoothed state separation.",
        "license": "MIT",
        "posture": "candidate_dependency",
        "why": "The causal-vs-smoothed distinction closely matches our retrospective-teacher architecture; still exploratory and not validated as a trading edge.",
    },
    {
        "repository": "taylorjmellon/market-regime-detection",
        "category": "Market regimes",
        "usefulness": "K-Means/HMM regime labeling using rolling return/volatility features and backtest evaluation.",
        "license": "Unknown / review required",
        "posture": "methodology_reference",
        "why": "Useful as a regime-research template, not as proof that a specific regime classifier predicts returns.",
    },
    {
        "repository": "QuantConnect/Lean",
        "category": "Backtesting architecture",
        "usefulness": "Mature event-driven engine, multi-asset data handling, portfolio/execution modeling, and large test surface.",
        "license": "Apache-2.0",
        "posture": "methodology_reference",
        "why": "Strong architecture/reference benchmark even though adopting Lean itself would be a much larger platform decision.",
    },
    {
        "repository": "edtechre/pybroker",
        "category": "ML / walk-forward",
        "usefulness": "Model training, walk-forward analysis, bootstrap metrics, optimization, Alpaca data, and multi-instrument research.",
        "license": "Apache-2.0 with Commons Clause",
        "posture": "reference_only",
        "why": "Highly relevant to the causal learner and walk-forward ML architecture, but Commons Clause warrants conservative integration posture.",
    },
    {
        "repository": "polakowo/vectorbt",
        "category": "Parameter research",
        "usefulness": "Fast vectorized parameter sweeps and large-scale strategy exploration.",
        "license": "Apache-2.0 with Commons Clause",
        "posture": "reference_only",
        "why": "Excellent performance/design reference for broad search, but license terms mean we should not casually embed or redistribute it.",
    },
    {
        "repository": "kernc/backtesting.py",
        "category": "Backtesting",
        "usefulness": "Readable strategy/backtest framework useful for comparing trade semantics, indicators, and optimization behavior.",
        "license": "AGPL-3.0",
        "posture": "reference_only",
        "why": "Useful as an independent behavioral reference, but AGPL licensing makes direct incorporation inappropriate without explicit licensing review.",
    },
    {
        "repository": "mementum/backtrader",
        "category": "Backtesting",
        "usefulness": "Long-established event-driven Python backtesting framework with indicators, orders, analyzers, and broker simulation.",
        "license": "GPL-3.0",
        "posture": "reference_only",
        "why": "Broad design reference, but copyleft licensing argues for independent implementation rather than code reuse.",
    },
    {
        "repository": "sohandillikar/SupportResistance",
        "category": "Support / resistance",
        "usefulness": "Smoothing, local extrema, and regression-line approach for support/resistance visualization.",
        "license": "No license found",
        "posture": "reference_only",
        "why": "Conceptually useful, but full-series smoothing/extrema can become retrospective. We should use it to generate teacher labels or comparisons, not causal signals without redesign.",
    },
)


def reference_rows() -> list[dict[str, Any]]:
    return [dict(item) for item in OPEN_SOURCE_REFERENCE_CATALOG]


def reference_categories() -> list[str]:
    return sorted({str(item.get("category") or "") for item in OPEN_SOURCE_REFERENCE_CATALOG})
