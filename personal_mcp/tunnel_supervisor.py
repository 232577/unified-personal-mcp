"""Own one tunnel generation and recover without blocking service work."""

import random
import threading
import time

from .tunnel import TunnelRunner


_ACTION_REQUIRED = frozenset({
    'TUNNEL_KEY_UNAVAILABLE', 'TUNNEL_KEY_INVALID', 'TUNNEL_CLIENT_HASH_MISMATCH',
    'TUNNEL_CLIENT_UNAVAILABLE', 'TUNNEL_ALREADY_IN_USE', 'TUNNEL_OWNERSHIP_UNCERTAIN',
    'INSTANCE_ALREADY_RUNNING', 'OWNED_PROCESS_CLEANUP_INCOMPLETE',
    'TUNNEL_CLEANUP_INCOMPLETE',
})


def _error_code(exc):
    code = str(exc)
    if code in _ACTION_REQUIRED:
        return code
    if isinstance(exc, FileNotFoundError):
        return 'TUNNEL_CLIENT_UNAVAILABLE'
    return 'TUNNEL_START_FAILED'


class TunnelSupervisor:
    def __init__(self, config, binary, backend_key, *, poll_interval=1,
                 retry_base=1, clock=time.time, jitter=random.random):
        self.config, self.binary, self.backend_key = config, binary, backend_key
        self._poll_interval, self._retry_base = poll_interval, retry_base
        self._clock, self._jitter = clock, jitter
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._runner = self._attempt = self.worker = None
        self._blocked = False
        self._refresh_credentials = False
        self._starts = self._failures = 0
        self._healthy_since = None
        self._state = {'status': 'disabled', 'last_success': None, 'error_code': None,
                       'recovery_attempts': 0, 'next_retry': None}

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def start(self):
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError('TUNNEL_SUPERVISOR_STOPPED')
            if self.worker is None:
                self._state['status'] = 'recovering'
                self.worker = threading.Thread(target=self._run, daemon=True, name='tunnel-supervisor')
                self.worker.start()
        self._wake.set()
        return self.snapshot()

    def retry(self):
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError('TUNNEL_SUPERVISOR_STOPPED')
            self._blocked = False
            self._refresh_credentials = True
            self._state['next_retry'] = None
        return self.start()

    def _update(self, **fields):
        with self._lock:
            if not self._stop.is_set():
                self._state.update(fields)

    def _run(self):
        while not self._stop.is_set():
            with self._lock:
                due = self._state['next_retry']
                if (not self._stop.is_set() and (not self._blocked or self._refresh_credentials)
                        and (self._attempt is None or not self._attempt.is_alive())
                        and (due is None or self._clock() >= due)):
                    self._attempt = threading.Thread(target=self._observe, daemon=True,
                                                     name='tunnel-recovery')
                    self._attempt.start()
            self._wake.wait(self._poll_interval)
            self._wake.clear()

    def _dispose(self):
        if self._runner is not None:
            try:
                self._runner.close()
            except Exception:
                raise RuntimeError('TUNNEL_CLEANUP_INCOMPLETE') from None
            self._runner = None

    def _failed(self, code):
        self._healthy_since = None
        self._failures += 1
        delay = min(30, self._retry_base * 2 ** min(self._failures - 1, 5))
        delay = min(30, delay * (1 + 0.1 * self._jitter()))
        with self._lock:
            if self._stop.is_set():
                return
            self._blocked = code in _ACTION_REQUIRED
            self._state.update(status='degraded' if self._blocked else 'recovering',
                               error_code=code,
                               next_retry=None if self._blocked else self._clock() + delay)

    def _observe(self):
        try:
            if self._stop.is_set():
                return
            with self._lock:
                refresh = self._refresh_credentials
                self._refresh_credentials = False
            if refresh and self._runner is not None and self._runner.credentials_changed():
                self._dispose()
            if self._runner is None:
                self._update(status='recovering', next_retry=None,
                             recovery_attempts=self._starts)
                self._starts += 1
                self._runner = TunnelRunner(self.config, self.binary, self.backend_key)
                self._runner.start()
            if self._stop.is_set():
                return
            if not self._runner.is_alive():
                self._dispose()
                self._failed('TUNNEL_PROCESS_EXITED')
            elif self._runner.ready():
                now = self._clock()
                if self._healthy_since is None:
                    self._healthy_since = now
                if now - self._healthy_since >= 60:
                    self._failures = 0
                self._update(status='healthy', last_success=now, error_code=None, next_retry=None)
            else:
                self._healthy_since = None
                self._update(status='degraded', error_code='TUNNEL_NOT_READY', next_retry=None)
        except Exception as exc:
            code = _error_code(exc)
            try:
                # A live child may be reconnecting itself. Never replace it on a probe error.
                if self._runner is not None and self._runner.is_alive():
                    if code in _ACTION_REQUIRED:
                        self._failed(code)
                    else:
                        self._update(status='degraded', error_code='TUNNEL_NOT_READY', next_retry=None)
                    return
                self._dispose()
            except Exception:
                code = 'TUNNEL_CLEANUP_INCOMPLETE'
            self._failed(code)
        finally:
            if self._stop.is_set():
                try:
                    self._dispose()
                except Exception:
                    with self._lock:
                        self._state['error_code'] = 'TUNNEL_CLEANUP_INCOMPLETE'

    def close(self):
        with self._lock:
            self._stop.set()
            self._state.update(status='stopped', next_retry=None)
        self._wake.set()
        if self.worker is not None:
            self.worker.join(timeout=2)
        attempt = self._attempt
        if attempt is not None:
            attempt.join(timeout=5)
            if attempt.is_alive():
                raise RuntimeError('TUNNEL_SHUTDOWN_INCOMPLETE')
        self._dispose()
