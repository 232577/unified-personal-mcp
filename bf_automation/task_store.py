"""Task-scoped ownership and opaque window bindings."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path


class TaskStore:
    def __init__(self, state_root: str | Path, *, allowed_root: str | Path,
                 allow_external_projects: bool = False):
        self.state_root = Path(state_root).resolve()
        self.allowed_root = Path(allowed_root).resolve()
        self.allow_external_projects = allow_external_projects
        self.tasks_root = self.state_root / "tasks"
        self.tasks_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._end_locks = {}
        self._ending = set()
        self._end_hooks = []
        self._owned_jobs = {}

    def add_end_hook(self, hook) -> None:
        """Register owned-resource cleanup before a task becomes inactive."""
        if hook not in self._end_hooks:
            self._end_hooks.append(hook)

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @classmethod
    def _task_key(cls, token: str) -> str:
        return cls._token_hash(token)[:16]

    @staticmethod
    def _read_json(path: Path, default):
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8-sig"))

    @staticmethod
    def _write_json(path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def _project_path(self, project_path: str | Path) -> Path:
        project = Path(project_path).resolve()
        if not self.allow_external_projects:
            try:
                project.relative_to(self.allowed_root)
            except ValueError as exc:
                raise ValueError(f"project path must stay inside allowed root {self.allowed_root}") from exc
        if not project.is_dir():
            raise ValueError(f"project path is not a directory: {project}")
        return project

    def begin(self, project_path: str | Path, *, context_id=None) -> dict:
        if context_id is not None:
            import re
            if not isinstance(context_id, str) or not re.fullmatch(r'[0-9a-f]{64}', context_id):
                raise ValueError('INVALID_CONTEXT_ID')
        project = self._project_path(project_path)
        token = "bf_" + secrets.token_hex(16)
        key = self._task_key(token)
        task_dir = self.tasks_root / key
        record = {
            "token_sha256": self._token_hash(token),
            "task_key": key,
            "project_path": str(project),
            "status": "active",
            "created": time.time(),
            "owned_pids": [],
            "owned_searches": [],
            "context_id": context_id,
        }
        with self._lock:
            self._write_json(task_dir / "task.json", record)
            self._write_json(task_dir / "windows.json", {})
        return {
            "status": "active",
            "bf_task_id": token,
            "task_key": key,
            "task_directory": str(task_dir),
            "project_path": str(project),
        }

    def require(self, token: str, *, allow_ending=False) -> tuple[Path, dict]:
        if not isinstance(token, str) or not token.startswith("bf_"):
            raise PermissionError("invalid bf_task_id")
        key = self._task_key(token)
        task_dir = self.tasks_root / key
        record = self._read_json(task_dir / "task.json", None)
        if not record or record.get("token_sha256") != self._token_hash(token):
            raise PermissionError("unknown bf_task_id")
        if record.get("status") != "active":
            raise PermissionError("bf_task_id is not active")
        if not allow_ending and key in self._ending:
            raise PermissionError("TASK_ENDING")
        return task_dir, record

    def status(self, token: str) -> dict:
        task_dir, record = self.require(token, allow_ending=True)
        return {
            "status": record["status"],
            "task_key": record["task_key"],
            "task_directory": str(task_dir),
            "project_path": record["project_path"],
            "owned_pids": list(record.get("owned_pids", [])),
            "owned_searches": list(record.get("owned_searches", [])),
        }

    def end(self, token: str) -> dict:
        with self._lock:
            _, record = self.require(token, allow_ending=True)
            end_lock = self._end_locks.setdefault(record["task_key"], threading.Lock())
        # Cleanup can wait for a resource whose startup needs the state lock.
        # Serialize this owner's cleanup separately and fence new admissions.
        with end_lock:
            with self._lock:
                task_dir, record = self.require(token, allow_ending=True)
                self._ending.add(record["task_key"])
            errors = []
            for hook in self._end_hooks:
                try:
                    hook(token)
                except Exception as exc:
                    errors.append(exc)
            with self._lock:
                for job in list(self._owned_jobs.get(record["task_key"], [])):
                    try:
                        job.close()
                        self._owned_jobs[record["task_key"]].remove(job)
                    except Exception as exc:
                        errors.append(exc)
                if errors:
                    raise RuntimeError("TASK_CLEANUP_INCOMPLETE") from errors[0]
                self._owned_jobs.pop(record["task_key"], None)
                db = self._claims()
                try:
                    with db:
                        db.execute("DELETE FROM window_claims WHERE owner = ?", (record["task_key"],))
                finally:
                    db.close()
                record["status"] = "ended"
                record["ended"] = time.time()
                self._write_json(task_dir / "task.json", record)
                self._ending.discard(record["task_key"])
        return {"status": "ended", "task_key": record["task_key"]}

    def spawn_owned(self, token, *args, **kwargs):
        from personal_mcp.windows_jobs import OwnedJob
        with self._lock:
            _, record = self.require(token)
            job = OwnedJob()
            self._owned_jobs.setdefault(record["task_key"], []).append(job)
            try:
                process = job.spawn(*args, **kwargs)
                process._bf_owned_job = job
                self.add_owned_pid(token, process.pid)
                return process
            except BaseException:
                job.close()
                self._owned_jobs[record["task_key"]].remove(job)
                raise

    def recover_context(self, context_id):
        """Startup-only recovery; never terminate a PID discovered in old state."""
        import psutil
        with self._lock:
            for path in self.tasks_root.glob('*/task.json'):
                record = self._read_json(path, {})
                if record.get('context_id') != context_id or record.get('status') == 'ended':
                    continue
                identities = record.get('owned_processes', {})
                for pid in record.get('owned_pids', []):
                    try:
                        process = psutil.Process(int(pid))
                        identity = identities.get(str(pid))
                        if identity is None or (process.create_time() == identity['created']
                                and Path(process.exe()).resolve() == Path(identity['exe']).resolve()):
                            raise RuntimeError('RECOVERY_PROCESS_STILL_ALIVE')
                    except psutil.NoSuchProcess:
                        continue
                record['status'], record['ended'] = 'ended', time.time()
                record['recovered'] = True
                self._write_json(path, record)
                db = self._claims()
                try:
                    with db:
                        db.execute('DELETE FROM window_claims WHERE owner=?', (record['task_key'],))
                finally:
                    db.close()

    def stop_owned(self, process):
        with self._lock:
            job = process._bf_owned_job
            job.close()
            process.wait(timeout=3)
            for jobs in self._owned_jobs.values():
                if job in jobs:
                    jobs.remove(job)

    def add_owned_pid(self, token: str, pid: int) -> None:
        with self._lock:
            task_dir, record = self.require(token)
            pids = {int(value) for value in record.get("owned_pids", [])}
            pids.add(int(pid))
            record["owned_pids"] = sorted(pids)
            import psutil
            try:
                process = psutil.Process(int(pid))
                identity = {"pid": process.pid, "created": process.create_time(), "exe": process.exe()}
                identities = record.setdefault("owned_processes", {})
                identities[str(pid)] = identity
            except psutil.NoSuchProcess:
                pass
            self._write_json(task_dir / "task.json", record)

    def bind_window(self, token: str, *, hwnd: int, pid: int, title: str) -> str:
        digest = hashlib.sha256(
            f"{token}\0{int(hwnd)}\0{int(pid)}\0{title}".encode("utf-8")
        ).hexdigest()[:24]
        window_id = f"bfw_{digest}"
        with self._lock:
            task_dir, _ = self.require(token)
            path = task_dir / "windows.json"
            windows = self._read_json(path, {})
            windows[window_id] = {
                "hwnd": int(hwnd),
                "pid": int(pid),
                "title": str(title),
                "bound_at": time.time(),
            }
            self._write_json(path, windows)
        return window_id

    def resolve_window(self, token: str, window_id: str) -> dict:
        task_dir, _ = self.require(token)
        windows = self._read_json(task_dir / "windows.json", {})
        record = windows.get(window_id)
        if record is None:
            raise PermissionError("window_id is not owned by this task")
        return dict(record)

    def _claims(self):
        """Persist actual-window ownership independently of transport sessions."""
        db = sqlite3.connect(self.state_root / "window-claims.sqlite3", timeout=5)
        db.execute(
            "CREATE TABLE IF NOT EXISTS window_claims ("
            "hwnd INTEGER NOT NULL, pid INTEGER NOT NULL, owner TEXT NOT NULL, "
            "claimed_at REAL NOT NULL, PRIMARY KEY(hwnd,pid))"
        )
        return db

    def claim_window(self, token: str, window_id: str) -> None:
        """Inventory is read-only; the first writer owns the actual window."""
        window = self.resolve_window(token, window_id)
        _, task = self.require(token)
        db = self._claims()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT owner FROM window_claims WHERE hwnd=? AND pid=?",
                    (window["hwnd"], window["pid"]),
                ).fetchone()
                if row and row[0] != task["task_key"]:
                    previous = self._read_json(self.tasks_root / row[0] / "task.json", {})
                    if previous.get("status") == "active":
                        raise PermissionError("window is write-owned by another task")
                db.execute(
                    "INSERT OR REPLACE INTO window_claims VALUES (?,?,?,?)",
                    (window["hwnd"], window["pid"], task["task_key"], time.time()),
                )
        finally:
            db.close()
