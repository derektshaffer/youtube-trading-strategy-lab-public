"""Append-only review/call receipts in the existing HybridStore SQLite database."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from hybrid_runtime.contracts import JobRequest, ExecutionTarget, canonical_json
from .contracts import ReviewError, VERSION, canonical, digest, freeze, fresh, now

JOB_TYPE = 'research.independent_review'
# An internal API capability, not an OS security boundary or an execution certificate.
_WORKFLOW_WRITE = object()
SCHEMA = '''
CREATE TABLE IF NOT EXISTS ai_review_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, artifact_id TEXT NOT NULL,
 previous_hash TEXT NOT NULL, body TEXT NOT NULL, event_hash TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS ai_review_artifact ON ai_review_events(artifact_id,id);
CREATE TABLE IF NOT EXISTS ai_review_calls (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, request_hash TEXT NOT NULL,
 created_at TEXT NOT NULL, request TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_review_receipts (
 id TEXT PRIMARY KEY REFERENCES ai_review_calls(id), body TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS ai_review_events_no_update BEFORE UPDATE ON ai_review_events
 BEGIN SELECT RAISE(ABORT,'append_only_review'); END;
CREATE TRIGGER IF NOT EXISTS ai_review_events_no_delete BEFORE DELETE ON ai_review_events
 BEGIN SELECT RAISE(ABORT,'append_only_review'); END;
CREATE TRIGGER IF NOT EXISTS ai_review_calls_no_update BEFORE UPDATE ON ai_review_calls
 BEGIN SELECT RAISE(ABORT,'append_only_calls'); END;
CREATE TRIGGER IF NOT EXISTS ai_review_calls_no_delete BEFORE DELETE ON ai_review_calls
 BEGIN SELECT RAISE(ABORT,'append_only_calls'); END;
CREATE TRIGGER IF NOT EXISTS ai_review_receipts_no_update BEFORE UPDATE ON ai_review_receipts
 BEGIN SELECT RAISE(ABORT,'append_only_receipts'); END;
CREATE TRIGGER IF NOT EXISTS ai_review_receipts_no_delete BEFORE DELETE ON ai_review_receipts
 BEGIN SELECT RAISE(ABORT,'append_only_receipts'); END;
'''


def replay(connection, artifact_id):
    previous = '0' * 64
    records = []
    for row in connection.execute('SELECT * FROM ai_review_events WHERE artifact_id=? ORDER BY id', (artifact_id,)):
        body = json.loads(row['body'])
        if (row['previous_hash'] != previous or body['artifact_id'] != artifact_id
                or digest([previous, body]) != row['event_hash']):
            raise ReviewError('audit_chain_invalid')
        previous = row['event_hash']
        records.append({**body, 'event_hash': previous})
    return records


class ReviewStorage:
    def __init__(self, store, clock=now):
        self.store, self.clock = store, clock
        with store._transaction(immediate=True) as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _transaction(self, **unused):
        conn = self.store._connect()
        try:
            conn.execute('PRAGMA synchronous=FULL')
            conn.execute('PRAGMA fullfsync=ON')
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def history(self, artifact_id):
        with self.store._reader() as conn:
            return replay(conn, artifact_id)

    def latest(self, artifact_id):
        rows = self.history(artifact_id)
        return rows[-1] if rows else None

    def job(self, artifact_id):
        job, _ = self.store.create_or_get_job(JobRequest(JOB_TYPE,
            {'artifact_id': artifact_id}, idempotency_key='ai-review:' + digest(artifact_id),
            requested_target='local', engine_version=VERSION),
            execution_target=ExecutionTarget.LOCAL, route_reason='offline frozen-packet review')
        return job.id

    def append(self, artifact_id, expected_head, kind, snapshot, *, authority=None):
        """Compare-and-swap snapshot and its job projection in one durable transaction."""
        if authority is not _WORKFLOW_WRITE:
            raise ReviewError('review_gate_write_required')
        snapshot = freeze(snapshot)
        job_id = self.job(artifact_id)
        with self._transaction() as conn:
            conn.execute('PRAGMA fullfsync=ON')
            records = replay(conn, artifact_id)
            head = records[-1]['event_hash'] if records else '0' * 64
            if head != expected_head:
                raise ReviewError('review_changed_concurrently')
            job = conn.execute('SELECT cancel_requested,status FROM jobs WHERE id=?', (job_id,)).fetchone()
            if job['cancel_requested'] or job['status'] == 'cancelled':
                raise ReviewError('review_job_cancelled')
            body = dict(version=VERSION, artifact_id=artifact_id, kind=kind, job_id=job_id,
                        created_at=self.clock(), snapshot=snapshot)
            event_hash = digest([head, body])
            conn.execute('INSERT INTO ai_review_events(artifact_id,previous_hash,body,event_hash) VALUES(?,?,?,?)',
                         (artifact_id, head, canonical(body), event_hash))
            # Completion is review bookkeeping only. It never dispatches a runner.
            status = 'complete' if kind == 'advanced' else 'retry_wait'
            conn.execute('UPDATE jobs SET status=?,stage=?,result_json=?,updated_at=?,completed_at=? WHERE id=?',
                (status, snapshot['state'], canonical_json(snapshot), body['created_at'],
                 body['created_at'] if status == 'complete' else None, job_id))
            from hybrid_runtime.contracts import JobStatus
            self.store._append_event(conn, job_id=job_id, status=JobStatus(status), stage=snapshot['state'],
                progress=0, message='Independent review: ' + kind, created_at=body['created_at'])
        return {**body, 'event_hash': event_hash}

    def reserve_call(self, request_hash, route, request):
        identifier = uuid4().hex
        stamp = self.clock()
        with self._transaction() as conn:
            pending = conn.execute('SELECT count(*) FROM ai_review_calls c LEFT JOIN ai_review_receipts r ON c.id=r.id WHERE r.id IS NULL').fetchone()[0]
            if pending:
                raise ReviewError('provider_call_in_flight_or_interrupted')
            count = conn.execute('SELECT count(*) FROM ai_review_calls WHERE provider=? AND substr(created_at,1,10)=?',
                                 (route['provider'], stamp[:10])).fetchone()[0]
            if count >= route['max_calls']:
                raise ReviewError('daily_call_budget_exhausted')
            conn.execute('INSERT INTO ai_review_calls VALUES(?,?,?,?,?)',
                         (identifier, route['provider'], request_hash, stamp, canonical(request)))
        return identifier

    def finish_call(self, identifier, response):
        with self._transaction() as conn:
            body = {'response': response, 'received_at': self.clock(), 'status': 'received'}
            conn.execute('INSERT INTO ai_review_receipts VALUES(?,?)', (identifier, canonical(body)))

    def fail_call(self, identifier):
        with self._transaction() as conn:
            conn.execute('INSERT OR IGNORE INTO ai_review_receipts VALUES(?,?)',
                (identifier, canonical({'received_at': self.clock(), 'status': 'unavailable'})))

    def cached_call(self, request_hash, require=False):
        with self.store._reader() as conn:
            rows = conn.execute('SELECT c.*,r.body FROM ai_review_calls c JOIN ai_review_receipts r ON c.id=r.id WHERE c.request_hash=? ORDER BY c.created_at DESC,c.rowid DESC', (request_hash,)).fetchall()
        for row in rows:
            body = json.loads(row['body'])
            request = json.loads(row['request'])
            if digest(request) != request_hash:
                raise ReviewError('call_request_hash_invalid')
            if body['status'] != 'received' or not fresh(body['received_at'], self.clock()):
                continue
            response = body['response']
            route = request['route']
            if not isinstance(response, dict) or set(response) != {'text', 'model_version', 'usage'}:
                raise ReviewError('provider_response_schema_invalid')
            if response['model_version'] != route['model_version']:
                raise ReviewError('provider_model_version_mismatch')
            usage = response['usage']
            if usage is not None and (not isinstance(usage, dict) or set(usage) != {'input_tokens', 'output_tokens'}
                    or any(type(v) is not int or v < 0 for v in usage.values())
                    or usage['input_tokens'] > route['max_input_tokens']
                    or usage['output_tokens'] > route['max_output_tokens']):
                raise ReviewError('provider_usage_invalid')
            if not isinstance(response['text'], str) or len(response['text'].encode()) > route['max_output_tokens'] * 16:
                raise ReviewError('response_budget_exceeded')
            try:
                output = json.loads(response['text'])
            except (ValueError, TypeError):
                raise ReviewError('provider_response_not_json') from None
            return dict(output=output, exact_response=response, request=request, request_hash=request_hash,
                        received_at=body['received_at'], receipt_hash=digest(body), call_id=row['id'])
        if require:
            raise ReviewError('provider_receipt_unavailable')
        return None

    def verify_receipt(self, receipt):
        """Bind decisions to persisted receipts, never caller-supplied consensus flags."""
        with self.store._reader() as conn:
            row = conn.execute('SELECT c.*,r.body FROM ai_review_calls c JOIN ai_review_receipts r ON c.id=r.id WHERE c.id=?',
                               (receipt.get('call_id'),)).fetchone()
        if row is None:
            raise ReviewError('persisted_provider_receipt_required')
        request, body = json.loads(row['request']), json.loads(row['body'])
        if (body['status'] != 'received' or request != receipt['request']
                or digest(request) != receipt['request_hash'] or row['request_hash'] != receipt['request_hash']
                or digest(body) != receipt['receipt_hash'] or body['response'] != receipt['exact_response']
                or body['received_at'] != receipt['received_at']
                or json.loads(body['response']['text']) != receipt['output']):
            raise ReviewError('persisted_provider_receipt_mismatch')


def read_summary(path, limit=30):
    """Read-only desktop projection; never initialize or migrate a database."""
    if not Path(path).exists():
        return []
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='ai_review_events'").fetchone():
            return []
        ids = conn.execute('SELECT artifact_id,max(id) AS recent FROM ai_review_events GROUP BY artifact_id ORDER BY recent DESC LIMIT ?', (max(1, min(limit, 100)),)).fetchall()
        result = []
        for row in ids:
            latest = replay(conn, row['artifact_id'])[-1]
            snapshot = latest['snapshot']
            from .gate import disposition
            snapshot['state'] = disposition(snapshot, now())
            job = conn.execute('SELECT status FROM jobs WHERE id=?', (latest['job_id'],)).fetchone()
            if job and job['status'] == 'cancelled':
                snapshot['state'] = 'REVIEW_CANCELLED'
            result.append({'artifact_id': row['artifact_id'], 'when': latest['created_at'], **snapshot})
        return result
    finally:
        conn.close()
