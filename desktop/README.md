# Desktop framework spikes

These spikes call the same authenticated loopback API. They are not production
applications and do not replace Streamlit yet.

The framework decision is made from measured results, not preference:

1. cold-start time,
2. chart interaction smoothness,
3. packaged Apple Silicon size,
4. Python sidecar reliability,
5. crash/restart recovery,
6. macOS signing/notarization path,
7. amount of duplicated UI code,
8. accessibility and native keyboard behavior.

`pyside6_spike/app.py` reuses Python directly. `tauri_spike/` exercises a Tauri
2 webview and an external Python sidecar. Both submit `system.health` through
`hybrid_runtime` and display the same route reason and durable job progress.

## Automated Apple Silicon builds

`.github/workflows/desktop-framework-spikes.yml` runs both candidates on the
standard `macos-14` M1 runner. It builds and ad-hoc signs each `.app`, verifies
arm64 binaries and signatures, launches a real authenticated app-to-sidecar
health job, records timing/size metadata, and uploads both zipped app bundles.

The workflow deliberately does not use production API keys, brokerage access,
research data, Apple certificates, or the current Streamlit deployment.

## Run the spikes locally

PySide6:

```bash
python -m pip install -r requirements-desktop-pyside.txt
python desktop/pyside6_spike/app.py
```

Tauri:

```bash
python -m pip install -r requirements-desktop.txt
python scripts/build_desktop_sidecar.py
cd desktop/tauri_spike
npm install
npm run dev
```

See `FRAMEWORK_SCORECARD.md` for the selection gates. A hosted build can measure
packaging and startup reliability, but the final choice still needs a narrow
chart-interaction comparison on the user's Apple Silicon Mac.
