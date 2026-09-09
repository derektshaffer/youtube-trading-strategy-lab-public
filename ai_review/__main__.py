"""Offline fixture demonstration and read-only audit replay. Never reads market data."""
import argparse
import json
from pathlib import Path
import sqlite3

from hybrid_runtime.storage import HybridStore
from .contracts import digest
from .fixtures import adapters, packet, source, validator, review_response
from .gate import ReviewGate
from .storage import ReviewStorage, replay


def fixture_demo(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    store = HybridStore(directory / 'hybrid.sqlite3')
    storage = ReviewStorage(store)
    gate = ReviewGate(storage, *adapters(lambda r: review_response(r, 'material')), validator)
    p = packet(artifact_id='fixture-resolution')
    gate.submit(p)
    material = gate.run(p['artifact_id'])
    # Reopen before resolution to exercise durable disagreement.
    reopened = ReviewStorage(HybridStore(store.path))
    assert reopened.latest(p['artifact_id']) == material
    objection = material['snapshot']['open_objections'][0]
    p['evidence'].append(source('new-receipt', 'Invented receipt demonstrates fixture timestamp order.'))
    p['resolutions'] = [dict(objection_id=objection['id'], evidence_id='new-receipt', sha256=p['evidence'][-1]['sha256'])]
    gate = ReviewGate(reopened, *adapters(), validator)
    gate.submit(p)
    gate.run(p['artifact_id'])
    gate.advance(p['artifact_id'], packet_hash=digest(p), checkpoint=p['checkpoint'], target='next_validation_stage')
    missing = packet(artifact_id='fixture-reviewer-unavailable')
    blocked = ReviewGate(reopened, adapters()[0], None, validator)
    blocked.submit(missing); blocked.run(missing['artifact_id'])
    def failed(p):
        r = validator(p); r['checks']['admission'] = 'failed'; return r
    rejected = ReviewGate(reopened, *adapters(), failed)
    p = packet(artifact_id='fixture-deterministic-rejection')
    rejected.submit(p); rejected.run(p['artifact_id'])
    result = replay_demo(directory)
    (directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def replay_demo(directory):
    path = Path(directory) / 'hybrid.sqlite3'
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        ids = [r[0] for r in conn.execute('SELECT DISTINCT artifact_id FROM ai_review_events ORDER BY artifact_id')]
        artifacts = {}
        for identifier in ids:
            rows = replay(conn, identifier)
            artifacts[identifier] = dict(events=len(rows), head=rows[-1]['event_hash'],
                replay_hash=digest(rows), disposition=rows[-1]['snapshot']['state'])
        return dict(version='independent-review-fixture-v1', fixture_only=True,
            execution_authority='none', artifacts=artifacts,
            audit_replay_hash=digest(artifacts), integrity=conn.execute('PRAGMA integrity_check').fetchone()[0])
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['fixture-demo', 'replay'])
    parser.add_argument('directory')
    args = parser.parse_args()
    result = fixture_demo(args.directory) if args.operation == 'fixture-demo' else replay_demo(args.directory)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
