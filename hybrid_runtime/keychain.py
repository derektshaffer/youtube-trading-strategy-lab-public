"""Minimal macOS Keychain adapter for desktop-only credentials."""

from __future__ import annotations

import platform
import subprocess
from typing import Sequence


class KeychainError(RuntimeError):
    pass


class KeychainUnavailable(KeychainError):
    pass


class MacOSKeychain:
    def __init__(self, service: str = "Trading Intelligence Lab") -> None:
        self.service = str(service or "Trading Intelligence Lab").strip()
        if not self.service:
            raise ValueError("Keychain service name is required")

    @staticmethod
    def _available() -> bool:
        return platform.system() == "Darwin"

    def _run(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if not self._available():
            raise KeychainUnavailable("macOS Keychain is only available on macOS")
        try:
            return subprocess.run(
                ["security", *arguments],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise KeychainUnavailable("The macOS security command is unavailable") from exc
        except subprocess.CalledProcessError as exc:
            # Never include stderr because the security CLI can echo sensitive input.
            raise KeychainError(
                f"Keychain command failed with exit code {exc.returncode}"
            ) from exc

    def set_secret(self, account: str, value: str) -> None:
        clean_account = str(account or "").strip()
        if not clean_account or not str(value):
            raise ValueError("Both account and non-empty secret are required")
        self._run(
            [
                "add-generic-password",
                "-U",
                "-a",
                clean_account,
                "-s",
                self.service,
                "-w",
                str(value),
            ]
        )

    def get_secret(self, account: str) -> str:
        clean_account = str(account or "").strip()
        if not clean_account:
            raise ValueError("account is required")
        completed = self._run(
            [
                "find-generic-password",
                "-a",
                clean_account,
                "-s",
                self.service,
                "-w",
            ]
        )
        return completed.stdout.rstrip("\n")

    def delete_secret(self, account: str) -> None:
        clean_account = str(account or "").strip()
        if not clean_account:
            raise ValueError("account is required")
        self._run(
            [
                "delete-generic-password",
                "-a",
                clean_account,
                "-s",
                self.service,
            ]
        )
