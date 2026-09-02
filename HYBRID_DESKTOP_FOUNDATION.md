# Hybrid desktop foundation

This branch starts the Trading Intelligence desktop migration without replacing
or modifying the working Streamlit UI.

Run the core tests:

```bash
python -m pytest -q test_hybrid_runtime.py
```

Run the optional local service:

```bash
python -m pip install -r requirements-desktop.txt
python -m hybrid_runtime.server
```

The service binds to `127.0.0.1`, creates a random per-launch bearer token in a
mode-0600 file, stores durable jobs in SQLite, and runs only jobs routed to the
local target. Heavy jobs remain queued for a later cloud adapter.

See `docs/HYBRID_DESKTOP_ARCHITECTURE.md` and `desktop/README.md`.
