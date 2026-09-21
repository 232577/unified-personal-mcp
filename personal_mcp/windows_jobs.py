"""Own a Windows process tree before its initial thread is allowed to run."""

import subprocess
import threading
import time
from contextvars import ContextVar

import psutil
import win32api
import win32job
import win32process

ACTIVE_JOB = ContextVar("personal_coding_job", default=None)


class OwnedJob:
    def __init__(self):
        self.lock = threading.RLock()
        self.handle = win32job.CreateJobObject(None, "")
        limits = win32job.QueryInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation, limits)

    def spawn(self, *args, **kwargs):
        with self.lock:
            if self.handle is None:
                raise RuntimeError("OWNED_JOB_CLOSED")
            options = dict(kwargs)
            flags = options.get("creationflags", 0)
            if flags & (subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS | 0x01000000):
                raise ValueError("OWNED_PROCESS_CANNOT_BREAK_AWAY")
            options["creationflags"] = flags | subprocess.CREATE_NO_WINDOW | 0x00000004
            process = subprocess.Popen(*args, **options)
            try:
                win32job.AssignProcessToJobObject(self.handle, int(process._handle))
                identity = psutil.Process(process.pid)
                process._coding_process_identity = identity
                threads = identity.threads()
                if len(threads) != 1:
                    raise RuntimeError("INITIAL_THREAD_IDENTITY_AMBIGUOUS")
                thread = win32api.OpenThread(0x0002, False, threads[0].id)
                try:
                    if win32process.ResumeThread(thread) != 1:
                        raise RuntimeError("INITIAL_THREAD_NOT_SUSPENDED")
                finally:
                    thread.Close()
                return process
            except BaseException:
                process.kill()
                process.wait(timeout=3)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream:
                        stream.close()
                raise

    def active_pids(self):
        with self.lock:
            if self.handle is None:
                return []
            return list(win32job.QueryInformationJobObject(self.handle, win32job.JobObjectBasicProcessIdList))

    def close(self):
        with self.lock:
            if self.handle is None:
                return
            win32job.TerminateJobObject(self.handle, 0)
            deadline = time.monotonic() + 3
            while self.active_pids() and time.monotonic() < deadline:
                time.sleep(0.01)
            if self.active_pids():
                raise RuntimeError("OWNED_PROCESS_CLEANUP_INCOMPLETE")
            self.handle.Close()
            self.handle = None
