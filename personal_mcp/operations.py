"""Persist dispatch before side effects; replay results, never unknown operations."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager


class OperationJournal:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS operations (
                owner TEXT, request_id TEXT, digest TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, PRIMARY KEY(owner, request_id))""")
            db.execute("UPDATE operations SET state='unknown' WHERE state='dispatched'")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def run(self, owner, request_id, name, arguments, execute):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("REQUEST_ID_REQUIRED")
        digest = hashlib.sha256(json.dumps([name, arguments], sort_keys=True, ensure_ascii=False,
                                           allow_nan=False).encode("utf-8")).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT digest,state,result FROM operations WHERE owner=? AND request_id=?",
                             (owner, request_id)).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError("REQUEST_CONFLICT")
                if row[1] != "completed":
                    raise TimeoutError("OUTCOME_UNKNOWN")
                return json.loads(row[2])
            db.execute("INSERT INTO operations VALUES (?,?,?,'dispatched',NULL)", (owner, request_id, digest))
        try:
            result = execute()
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > 2 * 1024 * 1024:
                raise TimeoutError("RESULT_TOO_LARGE_OUTCOME_UNKNOWN")
        except BaseException:
            with self._db() as db:
                db.execute("UPDATE operations SET state='unknown' WHERE owner=? AND request_id=?", (owner, request_id))
            raise
        with self._db() as db:
            db.execute("UPDATE operations SET state='completed',result=? WHERE owner=? AND request_id=?",
                       (encoded, owner, request_id))
        return result
