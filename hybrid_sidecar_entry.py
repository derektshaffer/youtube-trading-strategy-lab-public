"""PyInstaller-friendly entrypoint for the hybrid local service."""

from hybrid_runtime.server import main


if __name__ == "__main__":
    raise SystemExit(main())
