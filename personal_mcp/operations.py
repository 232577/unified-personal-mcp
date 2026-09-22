"""Persist dispatch before side effects; replay results, never unknown operations."""

import hashlib
import json
import sqlite3
import time
from concurrent.futures import Future
from contextlib import contextmanager


class OperationJournal:
    def __init__(self, path, *, clock=None, max_result_bytes=2 * 1024 * 1024,
                 retention_seconds=86400, max_records=100000, max_retained_bytes=64 * 1024 * 1024):
        self.path = path
        self.clock = clock or time.time
        self.max_result_bytes = max_result_bytes
        self.retention_seconds = retention_seconds
        self.max_records = max_records
        self.max_retained_bytes = max_retained_bytes
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS operations (
                owner TEXT, request_id TEXT, digest TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, PRIMARY KEY(owner, request_id))""")
            columns = {row[1] for row in db.execute('PRAGMA table_info(operations)')}
            for name, declaration in (('name', "TEXT NOT NULL DEFAULT ''"),
                                      ('updated', 'REAL NOT NULL DEFAULT 0')):
                if name not in columns:
                    db.execute(f'ALTER TABLE operations ADD COLUMN {name} {declaration}')
                    if name == 'updated':
                        # The old schema has no timestamps; give retained receipts one
                        # full grace period instead of evicting them on first lookup.
                        db.execute('UPDATE operations SET updated=?', (self.clock(),))
            db.execute("UPDATE operations SET state='unknown' WHERE state IN ('dispatched','running')")
            db.execute('CREATE INDEX IF NOT EXISTS operation_updated ON operations(updated)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def result_policy(self):
        return {'retention_seconds': self.retention_seconds, 'max_result_bytes': self.max_result_bytes,
                'max_retained_bytes': self.max_retained_bytes, 'max_records': self.max_records,
                'expired_receipts': 'Retained for deduplication; never replayed.'}

    def _prune(self, db):
        db.execute('UPDATE operations SET result=NULL WHERE result IS NOT NULL AND updated<?',
                   (self.clock() - self.retention_seconds,))
        rows = db.execute('SELECT owner,request_id,length(CAST(result AS BLOB)) FROM operations '
                          'WHERE result IS NOT NULL ORDER BY updated DESC').fetchall()
        retained = 0
        for owner, request_id, size in rows:
            retained += size
            if retained > self.max_retained_bytes:
                db.execute('UPDATE operations SET result=NULL WHERE owner=? AND request_id=?', (owner, request_id))

    def status(self, owner, request_id):
        with self._db() as db:
            self._prune(db)
            row = db.execute('SELECT name,state,result,updated FROM operations WHERE owner=? AND request_id=?',
                             (owner, request_id)).fetchone()
        if row is None:
            raise ValueError('OPERATION_NOT_FOUND')
        name, state, result, updated = row
        return {'ok': True, 'request_id': request_id, 'operation': name, 'state': state,
                'updated': updated, 'result': json.loads(result) if result is not None else None,
                'result_ref': request_id if state in {'completed', 'failed'} else None,
                'result_expired': state in {'completed', 'failed'} and result is None}

    def _unknown(self, owner, request_id):
        with self._db() as db:
            db.execute("UPDATE operations SET state='unknown',updated=? WHERE owner=? AND request_id=? "
                       "AND state IN ('dispatched','running')", (self.clock(), owner, request_id))

    def _complete(self, owner, request_id, result):
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        data = encoded.encode('utf-8')
        failed = isinstance(result, dict) and (result.get('isError') is True or result.get('ok') is False)
        if len(data) > self.max_result_bytes:
            result = {'isError': True, 'content': [{'type': 'text', 'text': 'RESULT_TRUNCATED'}],
                'structuredContent': {
                'ok': False, 'code': 'RESULT_TRUNCATED', 'execution_state': 'failed' if failed else 'completed',
                'result_truncated': True, 'serialized_bytes': len(data),
                'sha256': hashlib.sha256(data).hexdigest(),
                'preview': data[:max(0, min(1024, self.max_result_bytes // 12))].decode('utf-8', errors='ignore')}}
            encoded = json.dumps(result, ensure_ascii=False)
        with self._db() as db:
            db.execute("UPDATE operations SET state=?,result=?,updated=? WHERE owner=? AND request_id=? "
                       "AND state IN ('dispatched','running','unknown')",
                       ('failed' if failed else 'completed', encoded, self.clock(), owner, request_id))
            self._prune(db)
        return result

    def _finished(self, owner, request_id, future):
        try:
            self._complete(owner, request_id, future.result())
        except BaseException:
            # A cancelled Future or native error does not prove a side effect stopped.
            self._unknown(owner, request_id)

    def run(self, owner, request_id, name, arguments, execute, *, timeout=180):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("REQUEST_ID_REQUIRED")
        digest = hashlib.sha256(json.dumps([name, arguments], sort_keys=True, ensure_ascii=False,
                                           allow_nan=False).encode("utf-8")).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune(db)
            row = db.execute("SELECT digest,state,result FROM operations WHERE owner=? AND request_id=?",
                             (owner, request_id)).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError("REQUEST_CONFLICT")
                if row[1] in {'dispatched', 'running'}:
                    raise TimeoutError('OPERATION_RUNNING')
                if row[1] not in {"completed", "failed"}:
                    raise TimeoutError("OUTCOME_UNKNOWN")
                if row[2] is None:
                    raise ValueError('RESULT_EXPIRED')
                return json.loads(row[2])
            if db.execute('SELECT COUNT(*) FROM operations').fetchone()[0] >= self.max_records:
                raise ValueError('OPERATION_CAPACITY')
            db.execute("INSERT INTO operations (owner,request_id,digest,state,result,name,updated) "
                       "VALUES (?,?,?,'running',NULL,?,?)", (owner, request_id, digest, name, self.clock()))
        try:
            result = execute()
            if isinstance(result, Future):
                future = result
                future.add_done_callback(lambda done: self._finished(owner, request_id, done))
                try:
                    result = future.result(timeout=timeout)
                except TimeoutError:
                    if not future.done():
                        raise OperationPending('OPERATION_RUNNING') from None
                    raise
            return self._complete(owner, request_id, result)
        except OperationPending:
            raise
        except BaseException:
            self._unknown(owner, request_id)
            raise


class OperationPending(TimeoutError):
    """Only the caller's waiting period ended; the dispatched operation still runs."""
