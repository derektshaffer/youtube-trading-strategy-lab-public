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

## Run the spikes

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
