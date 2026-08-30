# Trading Intelligence Lab v1.0 — Definition of Done

## Status

Trading Intelligence Lab v1.0 is considered production-ready for **research and paper/shadow use** when the final CI and cloud-worker checks pass.

It is a research decision-support system. It is **not** a guarantee of profitability and does not need to discover a profitable strategy before the application itself can be considered complete.

## Core v1 capabilities

- Durable knowledge/research library with private cloud persistence.
- Source ingestion from curated research material.
- Grounded web research and specialist review.
- Stable hypothesis/concept memory to avoid endlessly rediscovering failed ideas.
- Machine-rule translation/readiness checks that fail closed for unsupported rules.
- Historical optimization with chronological training/validation/holdout separation.
- Leakage-free autonomous validation using a strict pre-test discovery cutoff.
- Walk-forward validation that fails closed if unavailable.
- Cross-stock generalization testing that excludes the optimized anchor stock.
- Conservative same-bar behavior and realistic slippage/cost modeling.
- Stock-specific Strategy Finder with checkpoints and cloud execution.
- Predictive ML research/shadow scoring with stock-learning route comparison.
- Continuous cloud research queue with retry/recovery, aging, bounded follow-up branching, and durable state.
- System Health page with configuration, persistence, workflow, and cloud smoke-test checks.
- Regression and repository-wide CI coverage.

## What counts as a v1 blocker

Reopen v1 development only for a demonstrated issue that can materially break or mislead the system, such as:

- look-ahead/data leakage;
- incorrect historical execution or rule evaluation;
- corrupted/lost durable state;
- jobs that can permanently deadlock or silently disappear;
- a page/workflow that cannot complete its stated core function;
- validated status being granted without required evidence;
- predictive ML affecting live execution when it is marked research/shadow-only;
- a critical/high-severity security or persistence defect.

## What does NOT block v1

These are normal research outcomes or future improvements, not reasons to keep rebuilding v1:

- a strategy fails validation;
- no currently tested strategy is profitable;
- a ticker has no clear ML learning route;
- a hypothesis needs more research;
- adding another indicator, data source, model family, or strategy concept;
- improving queue throughput after it is already draining correctly;
- increasing validation parallelism beyond the conservative v1 default;
- richer source provenance, analytics dashboards, or lifetime statistics;
- changing visual styling that does not block workflow completion.

## v1 operating model

The normal loop after release is:

**Learn → Consolidate → Test → Validate → Apply → Observe outcomes → Research again**

A failed validation is a successful execution of this loop when the evidence does not support the hypothesis.

## Post-v1 policy

New features belong in v1.x/v2 work and should be driven by actual use, measured bottlenecks, or new evidence. Do not reopen core architecture simply because research produces a negative result.
