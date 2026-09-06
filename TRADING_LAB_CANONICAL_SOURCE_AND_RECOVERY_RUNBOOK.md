# Trading Intelligence Lab — Canonical Source Lock & Functional Recovery Runbook

## CANONICAL SOURCE

- Canonical source tree for all future Trading Intelligence Lab edits:
  - `/Users/Derek_1/Documents/Codex/2026-09-03/referenced-chatgpt-conversation-this-is-an/work/trading-lab-dev`

How to enforce:
- launches now fail fast if started outside this path unless
  `TRADING_INTELLIGENCE_CANONICAL_SOURCE_ROOT` is explicitly set to the checked
  out path.
- every launch prints source hash, branch, commit, git top-level, and clean/dirty
  status before runtime startup.

## DUPLICATE / STALE COPIES TO AVOID

- `/Users/Derek_1/Documents/GitHub/youtube-trading-strategy-lab-public/Untitled/youtube-trading-strategy-lab-public`
  (different branch state, missing the Trading Lab Dev launcher/runtime files)
- `/Users/Derek_1/Documents/New project/Trading Lab`
  (non-primary local tree)
- `/Users/Derek_1/Documents/Codex/2026-09-03/referenced-chatgpt-conversation-this-is-an/outputs` wrappers

## SOURCE IDENTITY PRE-FLIGHT (required before any work session)

1. From canonical checkout, run:
   - `python3 scripts/launch_desktop_dev.py --bootstrap-only`
2. Confirm output:
   - `canonical source` equals the canonical tree above
   - `branch`/`commit` are expected for that session
   - `manifest sha256` is printed
   - `git status: clean` is preferred

If the command exits non-zero or prints "blocked", stop and move execution to the
canonical launcher path before continuing.

## WORKING END-TO-END CHECKLIST (target workflow)

1. Find stocks matching strategy rules in the stock finder section.
2. Analyze a selected stock from search results and capture strategy/technical context.
3. Match strategies to that stock and review matched strategy family details.
4. Run strategy test/validation paths (optimization, holdout, walk-forward, final validation).
5. Review confidence and validation narrative for explainability before any practice actions.
6. Start/review research queue tasks separately; do not let research status contaminate
   deterministic validation outputs.
7. Track runs/jobs in the dedicated search/strategy/job monitor and verify status transitions.
