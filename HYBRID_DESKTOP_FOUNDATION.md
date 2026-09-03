# Hybrid desktop foundation

The Trading Intelligence desktop migration is additive. It does not replace or
modify the working Streamlit UI.

## Primary workflow parity

The production desktop keeps the same user-facing directions as the web app:

- **Find & Test a Strategy:** choose one stock, then search for strategies that fit it.
- **Find Stocks:** choose one strategy or all faithful strategy families, then scan
  Momentum Universe, Top Gainers, Most Active, or a Custom Watchlist for current
  rule matches.
- **Home:** review strategy candidates and strict-validation status. This is the
  clearer user-facing name for the internal Profit First queue.
- **Open Momentum Scanner:** launch the independently maintained Momentum Scanner /
  Stock Analyzer without merging its code, rankings, or validation state into the Lab.

Market Discovery matches are research-only and keep validated and research-only
strategies visibly distinct. The separate Scanner launcher stores only a web URL or
local macOS app/launcher path.

Run the core tests:

```bash
python -m pytest -q test_hybrid_runtime.py test_hybrid_runtime_hardening.py test_desktop_build_workflow.py
```

Run the optional local service:

```bash
python -m pip install -r requirements-desktop.txt
python -m hybrid_runtime.server
```

The service binds to `127.0.0.1`, creates a random per-launch bearer token in a
mode-0600 file, stores durable jobs in SQLite, and runs only jobs routed to the
local target. Heavy jobs remain queued for a later cloud adapter.

Apple Silicon framework candidates are built by the `Desktop Framework Spikes`
GitHub Actions workflow. It packages and launches both Tauri and PySide6 against
the same service contracts before either framework is selected.

See `docs/HYBRID_DESKTOP_ARCHITECTURE.md`, `desktop/README.md`, and
`desktop/FRAMEWORK_SCORECARD.md`.
