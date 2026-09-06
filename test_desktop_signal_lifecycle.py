"""Real POSIX/Qt/sidecar lifecycle tests; no market or validation submissions."""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("uvicorn")
pytest.importorskip("fastapi")
fcntl = pytest.importorskip("fcntl")

ROOT = Path(__file__).resolve().parent

# The actual Qt signal bridge and SourceRuntime own a real backend. The tiny
# window avoids automatic UI data-loading requests and all external providers.
FRONTEND = r'''
import fcntl, json, os, pathlib, signal, sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget
from desktop.trading_intelligence.dev import SourceRuntime
from desktop.trading_intelligence.dev_signals import QtSignalShutdown
p = pathlib.Path(sys.argv[1])
app = QApplication([])
runtime = SourceRuntime(data_dir=p)
old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
calls = []
def cleanup():
    calls.append('quit')
    runtime.stop()
app.aboutToQuit.connect(cleanup)
with (p/'dev.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with QtSignalShutdown(app) as shutdown:
            assert all(signal.getsignal(s) == shutdown._signal for s in old)
            runtime.start()
            runtime.wait_until_ready()
            window = QWidget()
            window.show()
            def ready():
                (p/'ready.json').write_text(json.dumps({'frontend': os.getpid(), 'backend': runtime.process.pid, 'port': runtime.port}))
            QTimer.singleShot(0, ready)
            if sys.argv[2] == 'close':
                QTimer.singleShot(400, window.close)
            app.exec()
            code = shutdown.exit_code
        shutdown.close()  # cleanup itself is idempotent
        assert all(signal.getsignal(s) == old[s] for s in old)
    finally:
        runtime.stop()
        runtime.stop()
(p/'exited.json').write_text(json.dumps({'quit_calls': calls, 'code': code, 'handlers_restored': True}))
raise SystemExit(code)
'''

OWNER = r'''
import fcntl, os, pathlib, sys
from scripts.launch_desktop_dev import run_frontend
p=pathlib.Path(sys.argv[1])
with (p/'launcher.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    result=run_frontend([sys.executable, '-c', sys.argv[3], str(p), sys.argv[2]], environment=os.environ.copy())
raise SystemExit(result)
'''


def wait_for(predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    pytest.fail("Lifecycle deadline exceeded")


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def lock_available(path):
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    return True


def start_owner(path, mode="signal"):
    path.mkdir(exist_ok=True)
    (path / "ready.json").unlink(missing_ok=True)
    (path / "exited.json").unlink(missing_ok=True)
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    process = subprocess.Popen(
        [sys.executable, "-c", OWNER, str(path), mode, FRONTEND],
        cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    def ready():
        if process.poll() is not None:
            pytest.fail("Owner exited before ready: " + process.communicate()[1])
        try:
            return json.loads((path / "ready.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None
    return process, wait_for(ready)


def assert_stopped(process, identity, path, expected_code):
    _out, err = process.communicate(timeout=20)
    assert process.returncode == expected_code, err
    assert not alive(identity["frontend"])
    assert not alive(identity["backend"])
    assert lock_available(path / "launcher.lock")
    assert lock_available(path / "dev.lock")
    assert not (path / "local-service.json").exists()
    with socket.socket() as connection:
        assert connection.connect_ex(("127.0.0.1", identity["port"])) != 0
    evidence = json.loads((path / "exited.json").read_text())
    assert evidence == {"quit_calls": ["quit"], "code": expected_code, "handlers_restored": True}


@pytest.mark.parametrize("target", ["launcher", "frontend"])
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_real_signal_cleanup_lock_release_and_immediate_relaunch(tmp_path, target, signum):
    owner, identity = start_owner(tmp_path)
    try:
        assert not lock_available(tmp_path / "launcher.lock")
        assert not lock_available(tmp_path / "dev.lock")
        os.kill(owner.pid if target == "launcher" else identity["frontend"], signum)
        assert_stopped(owner, identity, tmp_path, 128 + signum)
        restarted, fresh = start_owner(tmp_path, "close")
        assert fresh["frontend"] != identity["frontend"]
        assert fresh["backend"] != identity["backend"]
        assert_stopped(restarted, fresh, tmp_path, 0)
    finally:
        if owner.poll() is None:
            owner.terminate()
            owner.wait(timeout=20)


def test_repeated_signal_uses_one_normal_cleanup(tmp_path):
    owner, identity = start_owner(tmp_path)
    try:
        os.kill(owner.pid, signal.SIGTERM)
        os.kill(owner.pid, signal.SIGTERM)
        os.kill(identity["frontend"], signal.SIGTERM)
        assert_stopped(owner, identity, tmp_path, 143)
    finally:
        if owner.poll() is None:
            owner.terminate()
            owner.wait(timeout=20)


def test_direct_backend_term_preserves_frontend_until_owner_cleanup(tmp_path):
    owner, identity = start_owner(tmp_path)
    try:
        os.kill(identity["backend"], signal.SIGTERM)
        wait_for(lambda: not (tmp_path / "local-service.json").exists())
        assert alive(identity["frontend"])
        assert owner.poll() is None
        assert not lock_available(tmp_path / "dev.lock")
        os.kill(owner.pid, signal.SIGTERM)
        assert_stopped(owner, identity, tmp_path, 143)
    finally:
        if owner.poll() is None:
            owner.terminate()
            owner.wait(timeout=20)


def test_signal_wakeup_fd_restored_and_idle_event_loop_wakes(tmp_path):
    script = r'''
import os, signal, socket
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from desktop.trading_intelligence.dev_signals import QtSignalShutdown
app=QApplication([])
reader, writer=socket.socketpair()
writer.setblocking(False)
old=signal.set_wakeup_fd(writer.fileno())
with QtSignalShutdown(app) as shutdown:
    QTimer.singleShot(0, lambda: os.kill(os.getpid(), signal.SIGTERM))
    app.exec()
assert shutdown.exit_code == 143
assert signal.set_wakeup_fd(old) == writer.fileno()
reader.close(); writer.close()
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
