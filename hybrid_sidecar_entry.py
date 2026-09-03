"""PyInstaller-friendly entrypoint for the hybrid local service."""

from __future__ import annotations

import os
from pathlib import Path
from typing import MutableMapping

from hybrid_runtime.server import main as server_main


def configure_tls_certificate_bundle(
    *,
    environ: MutableMapping[str, str] | None = None,
    certificate_bundle: str | Path | None = None,
) -> str:
    """Give frozen urllib clients an explicit maintained CA bundle."""

    environment = os.environ if environ is None else environ
    configured = str(environment.get("SSL_CERT_FILE") or "").strip()
    if configured:
        return configured
    if certificate_bundle is None:
        try:
            import certifi
        except ImportError:
            return ""
        certificate_bundle = certifi.where()
    candidate = Path(certificate_bundle).expanduser().resolve()
    if not candidate.is_file():
        return ""
    environment["SSL_CERT_FILE"] = str(candidate)
    return str(candidate)


def main(argv: list[str] | None = None) -> int:
    configure_tls_certificate_bundle()
    return server_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
