"""Explicit commands: preflight, capture, import-reference, verify, replay, status."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

from .events import ContractError, canonical_json
from .ledger import Ledger, Recorder, strict_json
from .service import (CANONICAL_ROOT, CaptureFailure, CaptureService, Config,
                      assert_canonical_source, credentials_from_environment)

DEFAULT_DATA = CANONICAL_ROOT / ".systematic-trader" / "capture"


def get_credentials(source):
    if source == "environment":
        return credentials_from_environment()
    services = {"lab-keychain": "Trading Intelligence Lab", "lab-dev-keychain": "Trading Intelligence Lab Dev"}
    if source not in services:
        raise CaptureFailure("unknown_credential_source")
    # Reuse the audited existing native adapter, without importing any GUI,
    # provider cache, application server or scanner module.
    from hybrid_runtime.keychain import KeychainError, MacOSKeychain
    from hybrid_runtime.desktop_settings import ALPACA_API_KEY_ACCOUNT, ALPACA_SECRET_KEY_ACCOUNT
    try:
        keychain = MacOSKeychain()
        # Explicit namespace; never inherit the launcher's environment or mix a
        # Dev key with a packaged-app secret through per-item fallback.
        keychain.service = services[source]
        keychain._fallback_service = None
        pair = (keychain.get_secret(ALPACA_API_KEY_ACCOUNT).strip(),
                keychain.get_secret(ALPACA_SECRET_KEY_ACCOUNT).strip())
        if not all(pair):
            raise CaptureFailure("keychain_credentials_missing")
        return pair
    except KeychainError:
        raise CaptureFailure("keychain_credentials_unavailable") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Systematic Trader: market-data capture only; no order authority.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    sub = parser.add_subparsers(dest="command", required=True)
    research = sub.add_parser("research-check",help="Offline fixture engineering verification; no credentials or orders")
    research.add_argument("--output-dir",type=Path,required=True)
    history = sub.add_parser("import-history", help="Preserve a bounded unadjusted historical bundle; no network")
    history.add_argument("--file", type=Path, required=True)
    history_view = sub.add_parser("history-view", help="Explicit research-clock projection of an archived dataset")
    history_view.add_argument("--dataset-id", required=True)
    history_view.add_argument("--through-seq", type=int, required=True)
    history_view.add_argument("--as-of", required=True)
    history_view.add_argument("--rank-session", help="Emit causal mover watchlist using archived membership and prior-session profiles")
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--config", type=Path)
    preflight.add_argument("--credentials", choices=["environment", "lab-keychain", "lab-dev-keychain"], default="environment")
    capture = sub.add_parser("capture")
    capture.add_argument("--config", type=Path, required=True)
    capture.add_argument("--credentials", choices=["environment", "lab-keychain", "lab-dev-keychain"], default="environment")
    capture.add_argument("--duration-seconds", type=float, default=0)
    tradier = sub.add_parser("capture-tradier")
    tradier.add_argument("--config", type=Path, required=True)
    tradier.add_argument("--credentials", choices=["environment", "lab-keychain", "lab-dev-keychain"], default="environment")
    tradier.add_argument("--duration-seconds", type=float, default=60)
    reference = sub.add_parser("import-reference")
    reference.add_argument("--file", type=Path, required=True)
    for name in ("verify", "status"):
        sub.add_parser(name)
    replay = sub.add_parser("replay")
    replay.add_argument("--through-seq", type=int, required=True)
    replay.add_argument("--received-through-ns", type=int)
    args = parser.parse_args(argv)
    try:
        assert_canonical_source()
        if args.command in {"import-history", "history-view"}:
            from .historical import HistoricalImporter, historical_view, MAX_BYTES
            from .events import timestamp_ns
            if args.data_dir.resolve() == DEFAULT_DATA.resolve():
                raise ContractError("historical_commands_require_explicit_data_directory")
            if args.command == "import-history":
                os.umask(0o077)
                with args.file.open("rb") as handle:
                    raw = handle.read(MAX_BYTES + 1)
                with Ledger(args.data_dir) as ledger:
                    importer = HistoricalImporter(ledger)
                    importer.recover()
                    raw_id = importer.ingest(raw)
                    rejected = ledger.connection.execute("SELECT 1 FROM events WHERE raw_id=? AND event_type='recorder.reject'", (raw_id,)).fetchone()
                    print(canonical_json(dict(raw_id=raw_id, rejected=bool(rejected), **ledger.verify())))
                    return 2 if rejected else 0
            with Ledger(args.data_dir, read_only=True) as ledger:
                ledger.verify()
                view = historical_view(ledger, through_seq=args.through_seq, as_of_ns=timestamp_ns(args.as_of), dataset_id=args.dataset_id)
                if args.rank_session:
                    from .momentum import historical_scan
                    print(canonical_json(historical_scan(view, session_id=args.rank_session)))
                else:
                    print(canonical_json(view["manifest"]))
                return 0
        if args.command == "research-check":
            from .research_check import run
            os.umask(0o077)
            print(canonical_json(run(args.output_dir)))
            return 0
        if args.command == "preflight":
            config = Config.load(args.config).summary() if args.config else None
            try:
                credentials = get_credentials(args.credentials)
                available = bool(all(credentials))
                del credentials
            except CaptureFailure:
                available = False
            print(canonical_json({"source_root": str(CANONICAL_ROOT), "data_dir": str(args.data_dir.resolve()),
                                  "config": config, "credentials_available": available,
                                  "credential_source": args.credentials, "execution_authority": "none",
                                  "live_started": False, "entitlement_verified": False}))
            return 0 if config and available else 2
        if args.command in {"verify", "status", "replay"}:
            with Ledger(args.data_dir, read_only=True) as ledger:
                if args.command == "verify":
                    print(canonical_json(ledger.verify()))
                elif args.command == "replay":
                    for event in ledger.replay(through_seq=args.through_seq, received_through_ns=args.received_through_ns):
                        print(canonical_json(event))
                else:
                    latest = ledger.connection.execute("SELECT event_json FROM events ORDER BY seq DESC LIMIT 1").fetchone()
                    event = json.loads(latest[0]) if latest else None
                    counts = dict(ledger.connection.execute("SELECT event_type,COUNT(*) FROM events GROUP BY event_type"))
                    age = (time.time_ns() - event["received_ns"]) / 1e9 if event else None
                    print(canonical_json({"watermark": ledger.watermark(), "counts": counts,
                                          "last_record_age_seconds": age, "last_event": event,
                                          "execution_authority": "none", "production_eligible": False,
                                          "pending_receipts": len(ledger.pending()),
                                          "capture_health": "unqualified"}))
            return 0
        os.umask(0o077)
        if args.command == "import-reference":
            raw = args.file.read_bytes()
            rows = strict_json(raw)
            if not isinstance(rows, list) or not rows:
                raise ContractError("invalid_reference_file")
            prepared = []
            for item in rows:
                if not isinstance(item, dict) or item.get("event_type") not in {
                    "reference.instrument", "reference.calendar", "reference.corporate_action"}:
                    raise ContractError("invalid_reference_event_type")
                payload = dict(item["payload"])
                payload["source_sha256"] = hashlib.sha256(raw).hexdigest()
                Recorder.validate_reference(item["event_type"], payload)
                prepared.append((item["event_type"], payload))
            with Ledger(args.data_dir) as ledger:
                recorder = Recorder(ledger, origin="import")
                recorder.recover()
                for kind, payload in prepared:
                    recorder.internal(kind, payload)
                print(canonical_json({"imported": len(prepared), "known_since": "local_receipt_only",
                                      "watermark": ledger.watermark()}))
            return 0
        if args.command == "capture-tradier":
            from .tradier import TradierCapture, TradierConfig, credentials as tradier_credentials
            config = TradierConfig.load(args.config)
            token = tradier_credentials(args.credentials)
            directory = CANONICAL_ROOT / ".systematic-trader/tradier-capture" if args.data_dir == DEFAULT_DATA else args.data_dir
            stop = threading.Event()
            for name in (signal.SIGINT, signal.SIGTERM):
                signal.signal(name, lambda *_: stop.set())
            with Ledger(directory, min_free_bytes=config.min_free_bytes) as ledger:
                recorder = Recorder(ledger, provider="tradier", feed="tradier_consolidated")
                TradierCapture(recorder, config, token, stop=stop, duration_seconds=args.duration_seconds).run()
            return 0
        config = Config.load(args.config)
        credentials = get_credentials(args.credentials)
        stop = threading.Event()
        for name in (signal.SIGINT, signal.SIGTERM):
            signal.signal(name, lambda *_: stop.set())
        with Ledger(args.data_dir, min_free_bytes=config.min_free_bytes) as ledger:
            service = CaptureService(Recorder(ledger), config, credentials, stop=stop,
                                     duration_seconds=args.duration_seconds)
            service.run()
        return 0
    except (Exception,) as exc:
        from .ledger import LedgerError
        reason = str(exc) if isinstance(exc, (ContractError, CaptureFailure, LedgerError)) else "capture_command_failed"
        print(canonical_json({"error": reason, "execution_authority": "none", "live_ready": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
