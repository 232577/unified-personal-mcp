"""Windows process primitives for proving and releasing hybrid ownership."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    create_time: float
    executable: Path

    def matches(self, other) -> bool:
        return (
            isinstance(other, ProcessIdentity)
            and int(self.pid) == int(other.pid)
            and float(self.create_time) == float(other.create_time)
            and Path(self.executable).resolve() == Path(other.executable).resolve()
        )


class HybridPlatform:
    """Small injectable OS boundary; it never discovers endpoints by scanning ports."""

    def __init__(self):
        self._owned = {}
        self._lock = threading.RLock()

    def owns_job(self, identity):
        with self._lock:
            return identity in self._owned

    @staticmethod
    def reserve_loopback_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(('127.0.0.1', 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def process_identity(pid: int) -> ProcessIdentity:
        process = psutil.Process(int(pid))
        executable = process.exe()
        if not executable:
            raise ProcessLookupError(pid)
        return ProcessIdentity(process.pid, process.create_time(), Path(executable).resolve())

    def descendants(self, pid: int) -> tuple[ProcessIdentity, ...]:
        process = psutil.Process(int(pid))
        result = []
        for child in process.children(recursive=True):
            try:
                result.append(self.process_identity(child.pid))
            except (psutil.Error, OSError, ProcessLookupError):
                continue
        return tuple(result)

    def launch(self, *, executable, args, cwd, env, stdout_path, stderr_path, owner, store) -> ProcessIdentity:
        stdout_path, stderr_path = Path(stdout_path), Path(stderr_path)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        with stdout_path.open('ab') as stdout, stderr_path.open('ab') as stderr:
            process = store.spawn_owned(owner,
                [str(executable), *list(args)],
                cwd=str(cwd) if cwd is not None else None,
                shell=False, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                close_fds=True, env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
        try:
            current = process._coding_process_identity
            identity = ProcessIdentity(process.pid, current.create_time(), Path(current.exe()).resolve())
            with self._lock:
                self._owned[identity] = (store, process)
            return identity
        except BaseException:
            store.stop_owned(process)
            raise

    @staticmethod
    def wait_ready(ready_file, timeout_seconds):
        path = Path(ready_file).resolve()
        deadline = time.monotonic() + float(timeout_seconds)
        while time.monotonic() <= deadline:
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 16384:
                try:
                    row = json.loads(path.read_text(encoding='utf-8-sig'))
                    if isinstance(row, dict):
                        return row
                except (ValueError, OSError):
                    pass
            time.sleep(0.05)
        raise TimeoutError('HYBRID_READY_TIMEOUT')

    @staticmethod
    def window_pid(hwnd: int) -> int:
        import win32process
        _, pid = win32process.GetWindowThreadProcessId(int(hwnd))
        return int(pid)

    @staticmethod
    def listener_pids(port: int) -> set[int]:
        pids = set()
        for connection in psutil.net_connections(kind='tcp'):
            try:
                if (connection.status == psutil.CONN_LISTEN and connection.pid
                        and connection.laddr and int(connection.laddr.port) == int(port)
                        and str(connection.laddr.ip) in {'127.0.0.1', '::1'}):
                    pids.add(int(connection.pid))
            except (AttributeError, ValueError):
                continue
        return pids

    def terminate_tree(self, identity: ProcessIdentity):
        with self._lock:
            owned = self._owned.get(identity)
            if owned is not None:
                store, process = owned
                pids = process._bf_owned_job.active_pids()
                store.stop_owned(process)
                del self._owned[identity]
                return {'terminated': pids, 'remaining': []}
        # All managed launches enter an owned Job before running. A caller may
        # never recover process ownership merely by presenting a PID.
        raise RuntimeError('HYBRID_PROCESS_NOT_OWNED')
