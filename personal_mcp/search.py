"""Bounded progressive rg sessions, adapted from DesktopCommanderMCP 0.2.50.

See third_party/DesktopCommanderMCP for MIT attribution. Absolute cursors,
ownership and limits replace the original global singleton and tail paging.
"""

import json
import os
import secrets
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .windows_jobs import OwnedJob


@dataclass
class Search:
    search_id: str
    owner: str
    project: Path
    process: object
    state: str = "running"
    reason: str | None = None
    results: list = field(default_factory=list)
    size: int = 0
    lock: object = field(default_factory=threading.RLock)
    thread: object = None
    job: object = None
    timer: object = None


class SearchManager:
    def __init__(self, *, rg=None, max_records=10000, max_bytes=8 * 1024 * 1024, max_seconds=60,
                 max_sessions=4):
        self.rg = rg or shutil.which("rg")
        self.max_records, self.max_bytes = max_records, max_bytes
        self.sessions = {}
        self.lock = threading.RLock()
        self.closed = False
        if type(max_sessions) is not int or not 1 <= max_sessions <= 64:
            raise ValueError("INVALID_SEARCH_SESSION_LIMIT")
        self.max_sessions, self.max_per_owner = max_sessions, 2
        if not 0 < max_seconds <= 300:
            raise ValueError("INVALID_SEARCH_TIMEOUT")
        self.max_seconds = max_seconds

    def start(self, owner, project, pattern, *, regex=False, env=None):
        if not isinstance(pattern, str) or not 1 <= len(pattern) <= 1024 or "\x00" in pattern:
            raise ValueError("INVALID_PATTERN")
        if type(regex) is not bool:
            raise ValueError("INVALID_REGEX_FLAG")
        project = Path(project).resolve(strict=True)
        if not project.is_dir():
            raise ValueError("INVALID_SEARCH_ROOT")
        if not self.rg:
            raise FileNotFoundError("RIPGREP_NOT_INSTALLED")
        command = [str(self.rg), "--json", "--line-buffered", "--no-config", "--max-columns", "16000"]
        if not regex:
            command.append("--fixed-strings")
        command.extend(["--", pattern, "."])
        with self.lock:
            if self.closed:
                raise RuntimeError("SEARCH_MANAGER_CLOSED")
            if (len(self.sessions) >= self.max_sessions
                    or sum(s.owner == owner for s in self.sessions.values()) >= self.max_per_owner):
                raise ValueError("SEARCH_SESSION_LIMIT")
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            job = OwnedJob()
            try:
                process = job.spawn(command, cwd=project, env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
            except BaseException:
                job.close()
                raise
            sid = "search_" + secrets.token_urlsafe(24)
            session = Search(sid, owner, project, process, job=job)
            self.sessions[sid] = session
            session.timer = threading.Timer(self.max_seconds, self._timeout, args=(session,))
            session.timer.daemon = True
            session.thread = threading.Thread(target=self._collect, args=(session,), daemon=True,
                                              name="personal-search")
            session.timer.start()
            session.thread.start()
        return {"search_id": sid, "state": "running"}

    def _timeout(self, session):
        with session.lock:
            if session.state == "running":
                session.state, session.reason = "partial", "TIME_LIMIT"
                session.job.close()

    def _collect(self, session):
        failed = False
        try:
            while line := session.process.stdout.readline(65537):
                with session.lock:
                    if session.state != "running":
                        break
                    if len(line) > 65536:
                        session.state, session.reason = "partial", "RESULT_LINE_LIMIT"
                        break
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeError):
                        failed = True
                        continue
                    if event.get("type") != "match":
                        continue
                    data = event["data"]
                    path = (session.project / data["path"]["text"]).resolve(strict=True)
                    if not path.is_relative_to(session.project):
                        raise ValueError("RESULT_PATH_OUTSIDE_PROJECT")
                    text = data["lines"].get("text")
                    if text is None:
                        session.state, session.reason = "partial", "NON_UTF8_RESULT"
                        break
                    record = {"path": path.relative_to(session.project).as_posix(),
                              "line": data["line_number"], "text": text.rstrip("\r\n")}
                    size = len(json.dumps(record, ensure_ascii=False).encode("utf-8"))
                    if (len(session.results) >= self.max_records or session.size + size > self.max_bytes
                            or size > 60000):
                        session.state, session.reason = "partial", "RESULT_LIMIT"
                        break
                    session.results.append(record)
                    session.size += size
        except Exception:
            failed = True
        finally:
            session.timer.cancel()
            with session.lock:
                if session.state != "running" and session.process.poll() is None:
                    session.process.kill()
            try:
                code = session.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                session.process.kill()
                code = session.process.wait(timeout=5)
                failed = True
            session.process.stdout.close()
            session.job.close()
            with session.lock:
                if session.state == "running":
                    session.state = "failed" if failed or code not in (0, 1) else "completed"
                    if session.state == "failed":
                        session.reason = "RIPGREP_FAILED"

    def occupancy(self, owner=None):
        with self.lock:
            sessions = list(self.sessions.values())
            return {"used": len(sessions), "running": sum(s.state == "running" for s in sessions),
                    "limit": self.max_sessions, "owner_used": sum(s.owner == owner for s in sessions),
                    "owner_limit": self.max_per_owner}

    def idle_blockers(self, owner):
        with self.lock:
            sessions = [s for s in self.sessions.values() if s.owner == owner]
        for session in sessions:
            if session.process.poll() is None or (session.thread is not None and session.thread.is_alive()):
                return ["running_search"]
        return []

    def _owned(self, owner, search_id):
        with self.lock:
            session = self.sessions.get(search_id)
            if session is None or session.owner != owner:
                raise PermissionError("SEARCH_NOT_OWNED")
            return session

    def read(self, owner, search_id, *, cursor=0, limit=100):
        session = self._owned(owner, search_id)
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("INVALID_CURSOR_OR_LIMIT")
        with session.lock:
            if cursor > len(session.results):
                raise ValueError("INVALID_CURSOR")
            results, size = [], 0
            for record in session.results[cursor:cursor + limit]:
                length = len(json.dumps(record, ensure_ascii=False).encode("utf-8"))
                if size + length > 60000:
                    break
                results.append(dict(record))
                size += length
            following = cursor + len(results)
            return {"search_id": search_id, "state": session.state, "reason": session.reason,
                    "results": results, "next_cursor": following,
                    "has_more": following < len(session.results) or session.state == "running"}

    def stop(self, owner, search_id):
        session = self._owned(owner, search_id)
        self._stop(session)
        return self.read(owner, search_id)

    def _stop(self, session):
        with session.lock:
            if session.state == "running":
                session.state = "stopped"
                session.job.close()
        session.thread.join(timeout=6)
        if session.thread.is_alive():
            raise RuntimeError("SEARCH_CLEANUP_INCOMPLETE")
        session.job.close()

    def release(self, owner, search_id):
        return self._release(self._owned(owner, search_id))

    def _release(self, session):
        # Process termination and collector joins can be slow. Keep capacity
        # reserved until they finish, without blocking another owner's reads.
        self._stop(session)
        with self.lock:
            if self.sessions.get(session.search_id) is session:
                del self.sessions[session.search_id]
        return {"search_id": session.search_id, "state": "released"}

    def end_owner(self, owner):
        with self.lock:
            sessions = [s for s in self.sessions.values() if s.owner == owner]
        for session in sessions:
            self._release(session)

    def close(self):
        with self.lock:
            self.closed = True
            sessions = list(self.sessions.values())
        for session in sessions:
            self._release(session)
