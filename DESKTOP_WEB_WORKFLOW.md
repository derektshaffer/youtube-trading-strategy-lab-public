# Web-style native workflow

Presentation-only adaptation of the existing Streamlit workspace, not a new frontend or engine.

## Web reference
The canonical trading_intelligence_app.py uses task-oriented persistent navigation, guided workflow steps, page titles with concise context and an Advanced / Research Details disclosure. trading_glass_theme.py defines the dark navy, muted blue-gray, green-action palette. The desktop adapts these concepts without copying decorative glass, gradients or paper/live execution.

## Scope
The existing native pages/controllers are composed by workflow_shell.py. The six primary destinations are Find Stocks, Analyze, Strategy, Validation, Results and Research. A persistent header retains exact ticker/strategy identity and only associates a saved verdict with its exact ticker/strategy tuple. Header navigation does not execute validation, matching or backtesting. Existing Find and Analyze actions still use their original callbacks.

Advanced validation controls are explicitly disclosed as starting cloud compute. The selected strategy explanation appears before optional broader research. Research is labeled separately from validation evidence. Results uses the existing read-only saved-validation controller, with library-wide evidence in a separate disclosure. Unpublished local mixed saved-results/backtest entry points are hidden and disconnected from this workflow, not published or expanded.

## Scroll contract
Ordinary wheel/trackpad gestures over a table or editable spin/combo control move the outer page. Reaching the page boundary does not fall through into the table. Hold Option to intentionally scroll inside a table, drag its scrollbar, or use keyboard arrows. Tables use capped heights and heading-aware widths. Long robustness details are selected-row explanations, not enormous horizontal table cells. A persistent visible page scrollbar supports direct manipulation.

## Evidence contract
Saved validation retains the canonical projection and exact identities. Verdict and strength appear before a compact summary of training, validation, holdout, walk-forward and stability evidence. Job provenance is progressively disclosed. Missing values remain missing, zero remains zero, and infrastructure timeout remains distinct from a failed strategy. Loading evidence is explicit and read-only. No backend, provider, threshold, strategy-rule or execution contract changes.

## Verification/publication gate
Run focused native/identity/scroll regressions, relevant broad tests and real macOS Accessibility walkthrough at laptop, large and narrow window sizes. Verify the completed SPY job d5e0aeae9c7c4556809fab89370b425f and timeout cc39e172544844d8b654135e3479298c without submitting new validation/backtests. Isolate only these presentation changes from the canonical dirty tree; require PR CI and activated native verification before claiming completion.
