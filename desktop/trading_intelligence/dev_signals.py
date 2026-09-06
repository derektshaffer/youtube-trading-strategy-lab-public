"""Event-driven POSIX signal shutdown for the source-only Qt application."""

from __future__ import annotations

import signal
import socket

from PySide6.QtCore import QSocketNotifier


class QtSignalShutdown:
    """Wake Qt even while Python is idle; never raise through a Qt callback."""

    def __init__(self, application) -> None:
        self.application = application
        self.exit_code = 0
        self._previous = {}
        self._previous_fd = None
        self._reader = self._writer = self._notifier = None
        self._quitting = False

    def _signal(self, signum: int, _frame: object) -> None:
        if not self.exit_code:
            self.exit_code = 128 + signum

    def _ready(self, *_args) -> None:
        while True:
            try:
                if not self._reader.recv(4096):
                    break
            except BlockingIOError:
                break
        if self.exit_code and not self._quitting:
            self._quitting = True
            self.application.quit()

    def __enter__(self):
        try:
            self._reader, self._writer = socket.socketpair()
            self._reader.setblocking(False)
            self._writer.setblocking(False)
            self._previous_fd = signal.set_wakeup_fd(self._writer.fileno())
            for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                self._previous[signum] = signal.signal(signum, self._signal)
            self._notifier = QSocketNotifier(
                self._reader.fileno(), QSocketNotifier.Type.Read, self.application
            )
            self._notifier.activated.connect(self._ready)
            return self
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier.deleteLater()
            self._notifier = None
        for signum, handler in self._previous.items():
            signal.signal(signum, handler)
        self._previous.clear()
        if self._previous_fd is not None:
            signal.set_wakeup_fd(self._previous_fd)
            self._previous_fd = None
        for channel in (self._reader, self._writer):
            if channel is not None:
                channel.close()
        self._reader = self._writer = None

    def __exit__(self, *_exc) -> None:
        self.close()
