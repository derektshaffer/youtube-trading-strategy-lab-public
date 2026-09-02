"""Native macOS Keychain adapter for desktop-only credentials."""

from __future__ import annotations

import platform
from typing import Any


class KeychainError(RuntimeError):
    pass


class KeychainUnavailable(KeychainError):
    pass


class MacOSKeychain:
    """Store credentials through keyring's native macOS Security API backend."""

    def __init__(self, service: str = "Trading Intelligence Lab") -> None:
        self.service = str(service or "Trading Intelligence Lab").strip()
        if not self.service:
            raise ValueError("Keychain service name is required")

    @staticmethod
    def _available() -> bool:
        return platform.system() == "Darwin"

    def _backend(self) -> Any:
        if not self._available():
            raise KeychainUnavailable("macOS Keychain is only available on macOS")
        try:
            from keyring.backends.macOS import Keyring
        except (ImportError, RuntimeError) as exc:
            raise KeychainUnavailable(
                "The native macOS keyring backend is unavailable; install requirements-desktop.txt"
            ) from exc
        try:
            return Keyring()
        except Exception as exc:
            raise KeychainUnavailable("The native macOS Keychain could not be initialized") from exc

    @staticmethod
    def _account(value: str) -> str:
        account = str(value or "").strip()
        if not account:
            raise ValueError("account is required")
        return account

    def set_secret(self, account: str, value: str) -> None:
        clean_account = self._account(account)
        secret = str(value or "")
        if not secret:
            raise ValueError("A non-empty secret is required")
        try:
            self._backend().set_password(self.service, clean_account, secret)
        except KeychainError:
            raise
        except Exception as exc:
            # Do not echo backend details: some implementations include command
            # arguments or values in exception messages.
            raise KeychainError("The credential could not be stored in macOS Keychain") from exc

    def get_secret(self, account: str) -> str:
        clean_account = self._account(account)
        try:
            value = self._backend().get_password(self.service, clean_account)
        except KeychainError:
            raise
        except Exception as exc:
            raise KeychainError("The credential could not be read from macOS Keychain") from exc
        if value is None:
            raise KeychainError("No matching credential exists in macOS Keychain")
        return str(value)

    def delete_secret(self, account: str) -> None:
        clean_account = self._account(account)
        try:
            self._backend().delete_password(self.service, clean_account)
        except KeychainError:
            raise
        except Exception as exc:
            raise KeychainError("The credential could not be deleted from macOS Keychain") from exc
