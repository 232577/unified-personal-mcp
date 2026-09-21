"""Owned Windows Job + bounded private worker pipes. No global process mutation."""

from __future__ import annotations

import errno
import os
import queue
import subprocess
import threading
import time
import uuid

import win32api
import win32job

from ..browser.protocol import MAX_LINE, VERSION, decode, encode


class WorkerReplyError(RuntimeError):
    pass


class WorkerProcess:
    def __init__(self, python, script, directory, browsers_path, timeout=25):
        # Chromium/Node create additional temporary names and do not support
        # every Windows long-path configuration. Fail before owning resources.
        if os.name == 'nt' and len(str(directory.resolve())) > 200:
            raise ValueError('BROWSER_STATE_PATH_TOO_LONG: choose a shorter private data directory')
        self.timeout = timeout
        self.directory = directory
        self.job = win32job.CreateJobObject(None, '')
        limits = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, limits)
        self.process = None
        self.reader = None
        self.closed = False
        self.close_lock = threading.RLock()
        self.cleanup_result = None
        self.replies = queue.Queue(maxsize=2)
        self.log = (directory / 'worker.stderr.log').open('ab')
        home = directory / 'home'
        home.mkdir(exist_ok=True)
        # Do not pass MCP keys, proxy credentials or a personal browser profile.
        env = {k: v for k, v in os.environ.items() if k.upper() in {
            'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'COMPUTERNAME', 'PROCESSOR_ARCHITECTURE',
        }}
        env.update(HOME=str(home), USERPROFILE=str(home), TEMP=str(directory), TMP=str(directory),
                   LOCALAPPDATA=str(home), APPDATA=str(home), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
                   PLAYWRIGHT_BROWSERS_PATH=str(browsers_path))
        try:
            self.process = subprocess.Popen([str(python), '-B', str(script)], cwd=directory, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
            # The worker only reads the pipe until boot; no browser is spawned
            # before ownership has been assigned. Failure is fail-closed.
            handle = win32api.OpenProcess(0x0100 | 0x0001, False, self.process.pid)
            try:
                win32job.AssignProcessToJobObject(self.job, handle)
            finally:
                handle.Close()
            self.reader = threading.Thread(target=self._read, daemon=True, name='bf-browser-pipe')
            self.reader.start()
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(MAX_LINE + 1)
                if not line:
                    self.replies.put_nowait(EOFError('WORKER_EXITED'))
                    return
                self.replies.put_nowait(decode(line))
        except Exception as exc:
            try:
                self.replies.put_nowait(exc)
            except queue.Full:
                pass

    def call(self, operation, arguments):
        if self.closed or self.process.poll() is not None:
            raise EOFError('WORKER_NOT_RUNNING')
        request_id = uuid.uuid4().hex
        frame = encode({'version': VERSION, 'id': request_id, 'operation': operation, 'arguments': arguments})
        self.process.stdin.write(frame)
        self.process.stdin.flush()
        try:
            result = self.replies.get(timeout=self.timeout)
        except queue.Empty:
            self.close()
            raise TimeoutError('WORKER_RESPONSE_TIMEOUT') from None
        if isinstance(result, BaseException):
            self.close()
            raise EOFError('INVALID_WORKER_STREAM') from None
        if result.get('version') != VERSION or result.get('id') != request_id or type(result.get('ok')) is not bool:
            self.close()
            raise EOFError('INVALID_WORKER_REPLY')
        if not result['ok']:
            raise WorkerReplyError(result.get('error', 'BROWSER_OPERATION_FAILED'))
        if not isinstance(result.get('result'), dict):
            self.close()
            raise EOFError('INVALID_WORKER_RESULT')
        return result['result']

    def active_pids(self):
        if self.job is None:
            return []
        return list(win32job.QueryInformationJobObject(self.job, win32job.JobObjectBasicProcessIdList))

    def close(self):
        with self.close_lock:
            if self.cleanup_result is not None:
                return dict(self.cleanup_result)
            result = self._close_impl()
            if result['released']:
                self.cleanup_result = result
            return dict(result)

    def _close_impl(self):
        # Stop new calls immediately, but retain ownership until cleanup succeeds.
        self.closed = True
        remaining = []
        job_drained = self.job is None
        try:
            if self.job is not None:
                win32job.TerminateJobObject(self.job, 0)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    remaining = self.active_pids()
                    if not remaining:
                        break
                    time.sleep(.05)
                job_drained = not remaining
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.kill()
                self.process.wait(timeout=3)
        finally:
            if self.job is not None and job_drained:
                self.job.Close()
                self.job = None
            if self.reader is not None:
                self.reader.join(timeout=2)
            pipe_error = None
            try:
                if self.process is not None:
                    for stream in (self.process.stdin, self.process.stdout):
                        if stream is None:
                            continue
                        try:
                            stream.close()
                        except OSError as exc:
                            # A buffered writer flushes during close. Windows may
                            # report EINVAL/EPIPE after the peer has exited, even
                            # though close has successfully released the handle.
                            # Do not skip stdout/log cleanup or conceal a live peer.
                            harmless = (exc.errno in {errno.EINVAL, errno.EPIPE}
                                        and stream.closed
                                        and self.process.poll() is not None)
                            if not harmless:
                                pipe_error = exc
            finally:
                self.log.close()
            if pipe_error is not None:
                raise pipe_error
        released = not remaining and self.job is None and not (self.reader and self.reader.is_alive())
        return {'released': released, 'remaining': remaining, 'deadline_exceeded': not released}
