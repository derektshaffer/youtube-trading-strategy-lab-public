"""Read-only import of bounded existing Alpaca exports, never a provider client."""
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import tempfile

from ..data_acquisition import verified_pages
from ..events import ContractError, timestamp_ns, canonical_json, digest
from ..research_split import protect_holdout
from .contracts import Tick, dataset_from_dict
from .replay import file_hash


def export_alpaca(quotes_dir, trades_dir, output, dataset_document):
    """Import without guessing lot sizes, quote eligibility, status or receipt time.

Disk ordering is deterministic for diagnostics. Equal timestamps retain unknown
ordering and will fail execution replay rather than invent cross-stream sequence.
The manifest remains BLOCKED until a separately audited evidence intake exists.
"""
    dataset = dataset_from_dict(dataset_document)  # Authorize before raw reads.
    if dataset.origin != "historical" or dataset.feed != "sip_nbbo":
        raise ContractError("short_horizon_alpaca_import_fidelity_invalid")
    if dataset.evidence:
        raise ContractError("short_horizon_alpaca_import_cannot_self_certify")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    refs = []
    with tempfile.TemporaryDirectory(prefix="short-horizon-", dir=output) as scratch:
        conn = sqlite3.connect(str(Path(scratch)/"sort.sqlite3"))
        try:
            conn.execute("CREATE TABLE ticks(at INTEGER, idx INTEGER PRIMARY KEY, body TEXT)")
            index = 0
            for directory, kind in ((quotes_dir, "quotes"), (trades_dir, "trades")):
                p = Path(directory); request = json.loads((p/"request.json").read_text())
                params = request["params"]
                protect_holdout(params["start"], params["end"])
                if (request["kind"] != kind or request["provider"] != "alpaca" or params.get("feed") != "sip"
                    or params.get("sort") != "asc" or params.get("asof") != "-"
                    or not set(dataset.symbols) <= set(params["symbols"].split(","))
                    or timestamp_ns(params["start"]) > dataset.start_ns
                    or timestamp_ns(params["end"])+1 < dataset.end_ns):
                    raise ContractError("short_horizon_export_scope_invalid")
                for page, meta in verified_pages(p):
                    refs.append(dict(kind=kind, sha256=meta["sha256"], retrieved_ns=meta["retrieved_ns"]))
                    batch = []
                    for symbol, rows in page[kind].items():
                        if symbol not in dataset.symbols:
                            continue
                        for row in rows:
                            at = timestamp_ns(row["t"])
                            received = row.get("original_application_receipt_ns")
                            if received is not None and dataset.clock != "actual_receipt":
                                raise ContractError("short_horizon_receipt_must_control_availability")
                            if received is None and dataset.clock == "actual_receipt":
                                raise ContractError("short_horizon_original_receipt_missing")
                            if not dataset.start_ns <= at < dataset.end_ns-dataset.source_delay_ns:
                                continue
                            index += 1
                            if index > 2_000_000:
                                raise ContractError("short_horizon_dataset_bound_exceeded")
                            base = dict(symbol=symbol, kind="quote" if kind == "quotes" else "trade", exchange_ns=at,
                                        received_ns=received, sequence=None, event_id=digest([kind,symbol,row]),
                                        source="alpaca:sip:"+meta["sha256"], eligible=False)
                            if kind == "quotes":
                                base.update(bid=str(row["bp"]), ask=str(row["ap"]), bid_size=row["bs"], ask_size=row["as"],
                                            venue=str(row.get("bx"))+"/"+str(row.get("ax")), size_unit="provider_round_lots")
                            else:
                                base.update(price=str(row["p"]), size=row["s"], venue=row.get("x"))
                            event = Tick(**base)
                            available=dataset.available(event)
                            if available < dataset.end_ns:
                                batch.append((available, index, canonical_json(asdict(event))))
                    conn.executemany("INSERT INTO ticks VALUES(?,?,?)", batch)
                conn.commit()
            with (output/"events.jsonl").open("x") as stream:
                for body, in conn.execute("SELECT body FROM ticks ORDER BY at,idx"):
                    stream.write(body+"\n")
        finally:
            conn.close()
    manifest = dict(dataset=dataset_document, events_sha256=file_hash(output/"events.jsonl"), source_pages=refs,
                    import_status="BLOCKED_unreviewed_conditions_lot_sizes_status_and_original_receipts",
                    execution_authority="none")
    (output/"dataset.json").write_text(json.dumps(manifest, indent=2)+"\n")
    return manifest
