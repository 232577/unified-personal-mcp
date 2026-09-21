"""Exclusive lease for operations that depend on the real foreground desktop."""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager, contextmanager


class DesktopLease:
    def __init__(self):
        self._condition = threading.Condition()
        self._owner: str | None = None
        self._acquired_at: float | None = None

    @contextmanager
    def hold(self, token: str, *, timeout: float = 30.0):
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._owner is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("desktop lease is owned by another task")
                self._condition.wait(min(remaining, 0.25))
            self._owner = token
            self._acquired_at = time.monotonic()
        try:
            yield
        finally:
            with self._condition:
                if self._owner == token:
                    self._owner = None
                    self._acquired_at = None
                    self._condition.notify_all()

    @asynccontextmanager
    async def hold_async(self, token: str, *, timeout: float = 30.0):
        """Wait cooperatively; never block the MCP event loop on a Condition.

        A token identifies a workflow, not an individual request. Even requests
        carrying the same token serialize their global keyboard/mouse use.
        """
        deadline = time.monotonic() + timeout
        while True:
            with self._condition:
                if self._owner is None:
                    self._owner = token
                    self._acquired_at = time.monotonic()
                    break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("desktop lease is owned by another request")
            await asyncio.sleep(min(remaining, 0.01))
        try:
            yield
        finally:
            with self._condition:
                self._owner = None
                self._acquired_at = None
                self._condition.notify_all()

    def status(self, token: str | None = None) -> dict:
        with self._condition:
            if self._owner is None:
                state = "available"
                age = 0.0
            elif token is not None and self._owner == token:
                state = "owned_by_task"
                age = max(0.0, time.monotonic() - (self._acquired_at or time.monotonic()))
            else:
                state = "owned_by_other_task"
                age = max(0.0, time.monotonic() - (self._acquired_at or time.monotonic()))
            return {"desktop_lease": state, "lease_age_seconds": round(age, 3)}
