# Hybrid desktop foundation

The Trading Intelligence desktop migration is additive. It does not replace or
modify the working Streamlit UI.

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
