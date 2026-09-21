"""One asyncio thread and one in-process MCP connection for the BF subsystem."""

import asyncio
import threading
import time
from concurrent.futures import Future, wait


class BFExecutor:
    def __init__(self, server):
        self.server = server
        self.ready = Future()
        self.lock = threading.Lock()
        self.pending = {}
        self.closed = False
        self.failure = None
        self.thread = threading.Thread(target=self._thread_main, daemon=True, name="personal-bf")
        self.thread.start()
        try:
            self.ready.result(timeout=20)
        except BaseException:
            if getattr(self, "loop", None) and not self.loop.is_closed():
                self.loop.call_soon_threadsafe(self.stop.set)
            self.thread.join(timeout=5)
            raise

    def _thread_main(self):
        try:
            asyncio.run(self._main())
        except BaseException as exc:
            self.failure = exc
            if not self.ready.done():
                self.ready.set_exception(exc)

    async def _main(self):
        from fastmcp import Client

        self.loop, self.stop = asyncio.get_running_loop(), asyncio.Event()
        async with Client(self.server, init_timeout=15, timeout=300) as client:
            self.client = client
            self.ready.set_result(True)
            await self.stop.wait()

    async def _call(self, name, arguments):
        result = await self.client.call_tool_mcp(name, arguments)
        return result.model_dump(by_alias=True, mode="json", exclude_none=True) if hasattr(result, "model_dump") else dict(result)

    def call(self, name, arguments, *, timeout=180):
        owner = arguments.get("bf_task_id", "")
        with self.lock:
            if self.closed or not self.thread.is_alive():
                raise RuntimeError("BF_EXECUTOR_CLOSED")
            future = asyncio.run_coroutine_threadsafe(self._call(name, arguments), self.loop)
            self.pending.setdefault(owner, set()).add(future)

        def finished(done):
            with self.lock:
                self.pending[owner].discard(done)

        future.add_done_callback(finished)
        # Do not cancel or replay a dispatched native operation on timeout.
        return future.result(timeout=timeout)

    def drain(self, owner, *, timeout=5):
        with self.lock:
            pending = set(self.pending.get(owner, ()))
        _, running = wait(pending, timeout=timeout)
        if running:
            raise TimeoutError("BF_OPERATIONS_STILL_RUNNING")

    def close(self):
        with self.lock:
            self.closed = True
            owners = list(self.pending)
        deadline = time.monotonic() + 10
        for owner in owners:
            self.drain(owner, timeout=max(0, deadline - time.monotonic()))
        if self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.stop.set)
            self.thread.join(timeout=5)
        if self.thread.is_alive() or self.failure:
            raise RuntimeError("BF_SHUTDOWN_INCOMPLETE") from self.failure
