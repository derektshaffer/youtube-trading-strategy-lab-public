"""Run the authenticated loopback service and its bounded local worker."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import threading

from .api import create_app
from .security import assert_loopback_host, generate_service_token, write_private_token_file
from .service import HybridService
from .storage import HybridStore
from .worker import LocalWorker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trading Intelligence local sidecar")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--data-dir",
        default=os.environ.get(
            "TRADING_INTELLIGENCE_DESKTOP_DATA_DIR",
            str(Path.home() / "Library" / "Application Support" / "Trading Intelligence Lab"),
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    host = assert_loopback_host(args.host)
    if not 1 <= int(args.port) <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    data_dir = Path(args.data_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    environment_token = os.environ.pop("TRADING_INTELLIGENCE_LOCAL_TOKEN", "")
    token = str(environment_token or generate_service_token()).strip()
    if len(token) < 32:
        raise SystemExit("The local service token must contain at least 32 characters")
    token_path = write_private_token_file(data_dir / "local-service.token", token)

    store = HybridStore(data_dir / "hybrid.sqlite3")
    recovered_jobs = store.requeue_stale_jobs(stale_after_seconds=180)
    if recovered_jobs:
        print(f"Recovered {recovered_jobs} stale local job(s).", flush=True)
    service = HybridService(store)
    worker = LocalWorker(service, worker_id=f"{socket.gethostname()}:{os.getpid()}")
    stop_event = threading.Event()
    thread = threading.Thread(
        target=worker.run_forever,
        args=(stop_event,),
        name="trading-intelligence-local-worker",
        daemon=True,
    )
    thread.start()

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "uvicorn is not installed. Install requirements-desktop.txt."
        ) from exc

    print(f"Trading Intelligence local service token: {token_path}", flush=True)
    app = create_app(service, expected_token=token)
    try:
        uvicorn.run(app, host=host, port=int(args.port), log_level="warning")
    finally:
        stop_event.set()
        thread.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
