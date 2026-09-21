"""Durable task/request deduplication. Never stores raw arguments or task tokens."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class OperationStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS operations ('
                       'owner TEXT, request_id TEXT, fingerprint TEXT, state TEXT, '
                       'result TEXT, updated REAL, PRIMARY KEY(owner,request_id))')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def validate_id(request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request_id):
            raise ValueError('INVALID_REQUEST_ID')

    @staticmethod
    def _row(request_id, state, result):
        return {'request_id': request_id, 'state': state,
                'verification': 'unknown' if state == 'unknown' else 'not_requested',
                'result': json.loads(result) if result is not None else None}

    def reserve(self, owner, request_id, operation, arguments):
        self.validate_id(request_id)
        fingerprint = hashlib.sha256(json.dumps(
            [operation, arguments], sort_keys=True, separators=(',', ':'), allow_nan=False,
        ).encode('utf-8')).hexdigest()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT fingerprint,state,result FROM operations WHERE owner=? AND request_id=?',
                             (owner, request_id)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise ValueError('REQUEST_ID_CONFLICT')
                return self._row(request_id, old[1], old[2]), False
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?)',
                       (owner, request_id, fingerprint, 'queued', None, time.time()))
            return self._row(request_id, 'queued', None), True

    def dispatch(self, owner, request_id):
        with self.connection() as db:
            cursor = db.execute("UPDATE operations SET state='dispatched',updated=? "
                                "WHERE owner=? AND request_id=? AND state='queued'",
                                (time.time(), owner, request_id))
            if cursor.rowcount != 1:
                raise ValueError('OPERATION_NOT_QUEUED')

    def finish(self, owner, request_id, state, result):
        if state not in {'completed', 'failed', 'unknown'}:
            raise ValueError('INVALID_FINAL_STATE')
        encoded = json.dumps(result, allow_nan=False, ensure_ascii=False) if result is not None else None
        with self.connection() as db:
            cursor = db.execute("UPDATE operations SET state=?,result=?,updated=? WHERE owner=? "
                                "AND request_id=? AND state IN ('queued','dispatched')",
                                (state, encoded, time.time(), owner, request_id))
            if cursor.rowcount != 1:
                raise ValueError('OPERATION_ALREADY_FINAL')
        return self.get(owner, request_id)

    def get(self, owner, request_id):
        self.validate_id(request_id)
        with self.connection() as db:
            row = db.execute('SELECT state,result FROM operations WHERE owner=? AND request_id=?',
                             (owner, request_id)).fetchone()
        return self._row(request_id, *row) if row else None

    def delete_owner(self, owner):
        with self.connection() as db:
            cursor = db.execute('DELETE FROM operations WHERE owner=?', (owner,))
            deleted = cursor.rowcount
            db.execute('PRAGMA optimize')
        return deleted

    def stats(self):
        with self.connection() as db:
            rows = db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]
            owners = db.execute('SELECT COUNT(DISTINCT owner) FROM operations').fetchone()[0]
        size = self.path.stat().st_size if self.path.exists() else 0
        return {'rows': rows, 'owners': owners, 'bytes': size}

    def recover(self):
        # Startup only, before accepting requests. A lost response is never replayed.
        with self.connection() as db:
            db.execute("UPDATE operations SET state='unknown',updated=? WHERE state='dispatched'", (time.time(),))
            db.execute("UPDATE operations SET state='failed',updated=? WHERE state='queued'", (time.time(),))
