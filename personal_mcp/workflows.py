"""Durable workflow ownership and project leases."""

import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AuthPrincipal:
    key: str


class WorkflowError(RuntimeError):
    def __init__(self, code, details=None):
        self.code = code
        self.details = details or {}
        super().__init__(code)


class WorkflowRegistry:
    def __init__(self, config, factory, clock=None, recovery=None):
        self.config, self.factory = config, factory
        self.recovery = recovery
        self.clock = clock or time.time
        self.lock = threading.RLock()
        self.resources, self.workflow_locks = {}, {}
        self.inflight = {}
        self.idle_timeout = getattr(config, "workflow_idle_seconds", 1800)
        self.closed = False
        config.data_root.mkdir(parents=True, exist_ok=True)
        self.path = config.data_root / "workflows.sqlite3"
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS workflows (
                token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL,
                request_id TEXT NOT NULL, digest TEXT NOT NULL, project TEXT NOT NULL,
                access TEXT NOT NULL, state TEXT NOT NULL, expires REAL NOT NULL,
                created REAL NOT NULL, error TEXT, UNIQUE(owner, request_id))""")
            if "last_activity" not in {row[1] for row in db.execute("PRAGMA table_info(workflows)")}:
                db.execute("ALTER TABLE workflows ADD COLUMN last_activity REAL")
            # Existing sessions receive a full idle grace period after migration.
            db.execute("UPDATE workflows SET last_activity=? WHERE last_activity IS NULL", (self.clock(),))
            db.execute("UPDATE workflows SET state='CLEANUP_BLOCKED', error='HOST_RESTARTED' "
                       "WHERE state IN ('STARTING','ACTIVE','CLOSING')")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _hash(token):
        if not isinstance(token, str) or not token.startswith("wf_") or len(token) > 128:
            raise WorkflowError("WORKFLOW_DENIED")
        return hashlib.sha256(token.encode()).hexdigest()

    def _row(self, owner, token):
        key = self._hash(token)
        with self._db() as db:
            row = db.execute("SELECT * FROM workflows WHERE token_hash=? AND owner=?",
                             (key, owner.key)).fetchone()
        if row is None:
            raise WorkflowError("WORKFLOW_DENIED")
        return dict(row)

    def _state(self, key, state, error=None):
        with self._db() as db:
            db.execute("UPDATE workflows SET state=?, error=? WHERE token_hash=?", (state, error, key))

    def _touch(self, key):
        with self._db() as db:
            db.execute("UPDATE workflows SET last_activity=? WHERE token_hash=?", (self.clock(), key))

    def _public(self, row):
        result = {k: row[k] for k in ("state", "project", "access", "expires")}
        result.update(workflow_ref="wfr_" + row["token_hash"], created=row["created"],
                      last_activity=row["last_activity"],
                      idle_seconds=max(0, self.clock() - row["last_activity"]))
        result["ok"] = row["state"] != "CLEANUP_BLOCKED"
        if row.get("error"):
            result["code"] = row["error"]
        return result

    def list(self, owner):
        if not isinstance(owner, AuthPrincipal) or not owner.key:
            raise WorkflowError("OWNER_REQUIRED")
        with self.lock, self._db() as db:
            rows = db.execute("SELECT * FROM workflows WHERE owner=? AND state NOT IN ('ENDED','EXPIRED') "
                              "ORDER BY created, token_hash", (owner.key,)).fetchall()
            return {"ok": True, "workflows": [self._public(dict(row)) for row in rows]}

    def _reference_row(self, owner, workflow_ref):
        if (not isinstance(owner, AuthPrincipal) or not owner.key
                or not isinstance(workflow_ref, str) or not re.fullmatch(r"wfr_[0-9a-f]{64}", workflow_ref)):
            raise WorkflowError("WORKFLOW_DENIED")
        with self._db() as db:
            row = db.execute("SELECT * FROM workflows WHERE token_hash=? AND owner=?",
                             (workflow_ref[4:], owner.key)).fetchone()
        if row is None:
            raise WorkflowError("WORKFLOW_DENIED")
        return dict(row)

    def release_idle(self, owner, workflow_ref):
        with self.lock:
            row = self._reference_row(owner, workflow_ref)
            key = row["token_hash"]
            gate = self.workflow_locks.setdefault(key, threading.RLock())
        if not gate.acquire(blocking=False):
            raise WorkflowError("WORKFLOW_NOT_IDLE", {"reason": "REQUEST_IN_PROGRESS"})
        try:
            with self.lock:
                row = self._reference_row(owner, workflow_ref)
                if row["state"] in {"ENDED", "EXPIRED"}:
                    return self._public(row)
                if self.closed or row["state"] != "ACTIVE":
                    raise WorkflowError("WORKFLOW_NOT_IDLE", {"reason": "WORKFLOW_STATE"})
                if self.inflight.get(key, 0):
                    raise WorkflowError("WORKFLOW_NOT_IDLE", {"reason": "REQUEST_IN_PROGRESS"})
                if self.clock() - row["last_activity"] < self.idle_timeout:
                    raise WorkflowError("WORKFLOW_NOT_IDLE", {"reason": "RECENT_ACTIVITY"})
                resource = self.resources.get(key)
            try:
                blockers = resource.idle_blockers()
            except Exception:
                blockers = None
            if not isinstance(blockers, list):
                raise WorkflowError("WORKFLOW_NOT_IDLE", {"reason": "RESOURCE_STATE_UNKNOWN"})
            if blockers:
                raise WorkflowError("WORKFLOW_NOT_IDLE", {"reason": "ACTIVE_RESOURCES", "blockers": blockers})
            # The gate spans observation and cleanup, so a new tool cannot enter
            # after the resource check. Cleanup never holds the registry lock.
            self._end(row)
            with self.lock:
                return self._public(self._reference_row(owner, workflow_ref))
        finally:
            gate.release()

    def begin(self, owner, project, request_id, access="write", ttl=120):
        if not isinstance(owner, AuthPrincipal) or not owner.key:
            raise WorkflowError("OWNER_REQUIRED")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise WorkflowError("INVALID_REQUEST_ID")
        if access not in {"read", "write"} or type(ttl) is not int or not 1 <= ttl <= 300:
            raise WorkflowError("INVALID_ARGUMENT")
        project = self.config.project(project)
        digest = hashlib.sha256(json.dumps([str(project).casefold(), access, ttl]).encode()).hexdigest()
        self.expire()
        with self.lock, self._db() as db:
            if self.closed:
                raise WorkflowError("HOST_CLOSING")
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT digest FROM workflows WHERE owner=? AND request_id=?",
                               (owner.key, request_id)).fetchone()
            if prior:
                return {"ok": False, "code": "BEGIN_ALREADY_ACCEPTED" if prior[0] == digest else "BEGIN_CONFLICT"}
            now = self.clock()
            db.execute("UPDATE workflows SET state='EXPIRED' WHERE state='RESERVED' AND expires<=?", (now,))
            conflicts = []
            for row in db.execute("SELECT * FROM workflows WHERE state NOT IN ('ENDED','EXPIRED')"):
                other = Path(row["project"])
                if (access == "write" or row["access"] == "write") and (
                        project.is_relative_to(other) or other.is_relative_to(project)):
                    conflicts.append(dict(row))
            if conflicts:
                blockers = []
                for row in conflicts:
                    if row["owner"] == owner.key:
                        other = Path(row["project"])
                        relation = "same" if project == other else "ancestor" if project.is_relative_to(other) else "descendant"
                        blockers.append({**self._public(row), "relation": relation})
                raise WorkflowError("PROJECT_BUSY", {"blockers": blockers,
                    "guidance": "Use a concrete project directory, not its shared parent. Keep the target project "
                    "when calling an external build tool by absolute path. Reuse your known workflow token, "
                    "or list and release_idle an idle workflow after its grace period; active resources are retained. "
                    "Use separate working copies for simultaneous changes to the same project."})
            token = "wf_" + secrets.token_urlsafe(32)
            key = self._hash(token)
            db.execute("INSERT INTO workflows (token_hash,owner,request_id,digest,project,access,state,expires,created,error,last_activity) "
                       "VALUES (?,?,?,?,?,?,?,?,?,NULL,?)",
                       (key, owner.key, request_id, digest, str(project), access, "RESERVED", now + ttl, now, now))
            self.workflow_locks[key] = threading.RLock()
        return {"ok": True, "workflow_id": token, "state": "RESERVED", "expires": now + ttl,
                "instructions": "Activate this workflow before use. This token is returned only once."}

    def expire(self):
        with self.lock, self._db() as db:
            db.execute("UPDATE workflows SET state='EXPIRED' WHERE state='RESERVED' AND expires<=?",
                       (self.clock(),))
            rows = [dict(row) for row in db.execute("SELECT token_hash,owner FROM workflows "
                    "WHERE state='ACTIVE' AND last_activity<=?", (self.clock() - self.idle_timeout,))]
        for row in rows:
            try:
                self.release_idle(AuthPrincipal(row["owner"]), "wfr_" + row["token_hash"])
            except WorkflowError:
                pass

    def status(self, owner, token):
        self.expire()
        with self.lock:
            return self._public(self._row(owner, token))

    def activate(self, owner, token):
        self.expire()
        with self.lock:
            row = self._row(owner, token)
            key = row["token_hash"]
            gate = self.workflow_locks.setdefault(key, threading.RLock())
        # Renewal and idle reclamation must use the same gate; otherwise an
        # activation could return ACTIVE while a prior idle probe closes it.
        with gate:
            with self.lock:
                row = self._row(owner, token)
                if self.closed or row["state"] not in {"RESERVED", "ACTIVE"}:
                    raise WorkflowError("WORKFLOW_INACTIVE")
                if row["state"] == "RESERVED":
                    self._state(key, "STARTING")
                    try:
                        self.resources[key] = self.factory(row)
                    except Exception:
                        self._state(key, "CLEANUP_BLOCKED", "STARTUP_FAILED")
                        raise WorkflowError("STARTUP_FAILED") from None
                    self._state(key, "ACTIVE")
                self._touch(key)
                return self._public(self._row(owner, token))

    @contextmanager
    def use(self, owner, token, *, write=False):
        with self.lock:
            row = self._row(owner, token)
            key = row["token_hash"]
            gate = self.workflow_locks.setdefault(key, threading.RLock())
        with gate:
            with self.lock:
                row = self._row(owner, token)
                if self.closed or row["state"] != "ACTIVE" or key not in self.resources:
                    raise WorkflowError("WORKFLOW_INACTIVE")
                if write and row["access"] != "write":
                    raise WorkflowError("READ_ONLY_WORKFLOW")
                resource = self.resources[key]
                self._touch(key)
                self.inflight[key] = self.inflight.get(key, 0) + 1
            try:
                yield resource
            finally:
                with self.lock:
                    self.inflight[key] -= 1
                    if not self.inflight[key]:
                        del self.inflight[key]
                    self._touch(key)

    def _end(self, row):
        key = row["token_hash"]
        with self.lock:
            with self._db() as db:
                row = dict(db.execute("SELECT * FROM workflows WHERE token_hash=?", (key,)).fetchone())
            if row["state"] in {"ENDED", "EXPIRED"}:
                return
            prior = row["state"]
            self._state(key, "CLOSING")
            gate = self.workflow_locks.setdefault(key, threading.RLock())
        with gate:
            with self._db() as db:
                if db.execute("SELECT state FROM workflows WHERE token_hash=?", (key,)).fetchone()[0] == "ENDED":
                    return
            try:
                resource = self.resources.get(key)
                if resource:
                    resource.close()
                elif prior != "RESERVED":
                    if self.recovery is None:
                        raise WorkflowError("RECOVERY_REQUIRED")
                    self.recovery(row)
            except Exception:
                self._state(key, "CLEANUP_BLOCKED", "CLEANUP_FAILED")
            else:
                with self.lock:
                    self.resources.pop(key, None)
                    self._state(key, "ENDED")

    def end(self, owner, token):
        with self.lock:
            row = self._row(owner, token)
        self._end(row)
        with self.lock:
            return self._public(self._row(owner, token))

    def close(self):
        with self.lock:
            self.closed = True
            with self._db() as db:
                rows = [dict(r) for r in db.execute("SELECT * FROM workflows")
                        if r["token_hash"] in self.resources]
        for row in rows:
            self._end(row)
        if self.resources:
            raise WorkflowError("CLEANUP_FAILED")

    def recover(self):
        with self.lock:
            with self._db() as db:
                rows = [dict(row) for row in db.execute("SELECT * FROM workflows WHERE state='CLEANUP_BLOCKED'")]
        for row in rows:
            self._end(row)
        self.expire()
        with self.lock, self._db() as db:
            blocked = db.execute("SELECT COUNT(*) FROM workflows WHERE state='CLEANUP_BLOCKED'").fetchone()[0]
        return {"recovered": len(rows) - blocked, "blocked": blocked}
