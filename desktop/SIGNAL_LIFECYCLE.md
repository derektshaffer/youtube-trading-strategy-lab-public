# Canonical development process shutdown

The Python development launcher owns `launcher.lock` and waits for its frontend.
The frontend owns `dev.lock` and the backend `Popen` handle. The backend starts
in a separate session; it is not intended to outlive the frontend.

Send SIGTERM to the Python launcher for whole-app shutdown. It forwards the
first termination request and retains its lock until the frontend is reaped.
SIGTERM, SIGINT, and SIGHUP sent directly to the frontend request the same Qt
quit path as native window close. A nonblocking socket wakeup and Qt socket
notifier bridge signals into the event loop without polling or raising through
Qt callbacks. Handlers and any previous wakeup descriptor are restored afterward.

`aboutToQuit` invokes the existing runtime cleanup. The development `finally`
remains an idempotent fallback. Backend termination waits up to the existing
five-second grace period before its existing forced-termination fallback.
The backend runs worker/PID cleanup during ASGI lifespan shutdown, before
Uvicorn can re-raise the terminating signal at the OS level.

Direct backend SIGTERM stops only the backend. The frontend intentionally stays
open, retaining its lock; there is no implicit new backend or job submission.
The frontend reaps its stopped child on runtime cleanup. A terminated child may
remain a zombie until then, but cannot serve requests or hold its old port.

Empty lock files may persist: the OS advisory lock, not file existence, indicates
ownership. `dev-launch.json` is historical provenance, not a liveness lock.
`local-service.json` is removed during normal backend shutdown. No credentials,
saved results, or queued-job files are deleted by this lifecycle repair.

Focused certification: `python -m pytest -q test_desktop_signal_lifecycle.py
test_desktop_dev_launcher.py test_desktop_qt_startup.py`. The lifecycle tests use
real Qt processes and local sidecars in temporary data directories, without
provider research, validation, backtest, or brokerage submissions.
