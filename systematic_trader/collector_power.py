"""Temporary macOS sleep assertions owned by one collector process."""
import os
import subprocess

from .events import ContractError


class CollectorAwake:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.process = None

    def __enter__(self):
        if self.enabled:
            self.process = subprocess.Popen(
                ['/usr/bin/caffeinate', '-i', '-s', '-w', str(os.getpid())],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True)
            self.check()
        return self

    def check(self):
        if self.process is not None and self.process.poll() is not None:
            raise ContractError('collector_sleep_assertion_lost')

    def __exit__(self, *_):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
