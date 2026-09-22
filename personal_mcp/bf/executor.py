"""Generation-bound BF connections; dispatched native operations are never replayed."""

import asyncio
import threading
import time
from concurrent.futures import Future, wait
from copy import deepcopy
from dataclasses import dataclass, field


@dataclass
class _Generation:
    number: int
    ready: Future = field(default_factory=Future)
    thread: threading.Thread | None = None
    loop: object = None
    stop: object = None
    client: object = None
    accepting: bool = False
    failure: BaseException | None = None
    requests: dict = field(default_factory=dict)
    uncertain_owners: set = field(default_factory=set)


class BFExecutor:
    def __init__(self, server):
        self.server = server
        # This lock and pending inventory are also consumed by WorkflowResources.
        self.lock = threading.Lock()
        self.pending = {}
        self.closed = False
        self.failure = None
        self._health = {"status": "recovering", "last_success": None, "error_code": None,
                        "recovery_attempts": 0, "next_retry": None}
        self._generation = None
        with self.lock:
            generation = self._start_locked()
        try:
            generation.ready.result(timeout=20)
        except BaseException:
            with self.lock:
                self.closed = True
            self._stop(generation)
            generation.thread.join(timeout=5)
            raise

    def _start_locked(self):
        previous = self._generation
        if previous is not None:
            # Only a conclusively exited thread may relinquish the shared server.
            assert not previous.thread.is_alive() and not previous.requests
            self._health["recovery_attempts"] += 1
        generation = _Generation(previous.number + 1 if previous else 1)
        generation.thread = threading.Thread(target=self._thread_main, args=(generation,),
            daemon=True, name=f"personal-bf-{generation.number}")
        self._generation = generation
        self.ready, self.thread = generation.ready, generation.thread
        self._health.update(status="recovering", error_code=None, next_retry=None)
        generation.thread.start()
        return generation

    def _thread_main(self, generation):
        try:
            asyncio.run(self._lifetime(generation))
        except BaseException as exc:
            generation.failure = exc
        finally:
            error = RuntimeError("BF_CONNECTION_LOST")
            with self.lock:
                generation.accepting = False
                if self._generation is generation and not self.closed:
                    self.failure = generation.failure or error
                    self._health.update(status="degraded", error_code="BF_CONNECTION_LOST")
                abandoned = list(generation.requests.items())
            # Archive original outcomes before this thread can be replaced.
            # The caller/journal retains each Future; no write is resubmitted.
            for future, owner in abandoned:
                failed = Future()
                failed.set_exception(RuntimeError("BF_CALL_OUTCOME_UNKNOWN"))
                self._finished(generation, owner, future, failed)
            if not generation.ready.done():
                generation.ready.set_exception(error)

    async def _lifetime(self, generation):
        try:
            await self._main(generation)
        finally:
            # asyncio.run normally abandons this join after five minutes. A
            # generation must instead remain alive while its native workers do;
            # public close/drain retain their own bounded waits and can retry.
            await asyncio.get_running_loop().shutdown_default_executor()

    async def _main(self, generation):
        from fastmcp import Client

        generation.loop, generation.stop = asyncio.get_running_loop(), asyncio.Event()
        with self.lock:
            self.loop, self.stop = generation.loop, generation.stop
            if self.closed:
                generation.stop.set()
        async with Client(self.server, init_timeout=15, timeout=300) as client:
            with self.lock:
                generation.client = client
                self.client = client
                generation.accepting = not self.closed
                if generation.accepting:
                    self.failure = None
                    self._health.update(status="healthy", last_success=time.time(), error_code=None)
            generation.ready.set_result(True)
            try:
                while not generation.stop.is_set():
                    try:
                        await asyncio.wait_for(generation.stop.wait(), timeout=0.25)
                    except TimeoutError:
                        if not client.is_connected():
                            raise RuntimeError("BF_CONNECTION_LOST")
            finally:
                with self.lock:
                    generation.accepting = False
                    if self._generation is generation and not self.closed:
                        self.failure = RuntimeError("BF_CONNECTION_LOST")
                        self._health.update(status="degraded", error_code="BF_CONNECTION_LOST")

    async def _call(self, generation, name, arguments):
        result = await generation.client.call_tool_mcp(name, arguments)
        return (result.model_dump(by_alias=True, mode="json", exclude_none=True)
                if hasattr(result, "model_dump") else dict(result))

    def submit(self, name, arguments):
        arguments = deepcopy(arguments)
        owner = arguments.get("bf_task_id", "")
        with self.lock:
            if self.closed:
                raise RuntimeError("BF_EXECUTOR_CLOSED")
            generation = self._generation
            if not generation.thread.is_alive():
                generation = self._start_locked()
        # Concurrent callers share this single bounded connection attempt.
        try:
            generation.ready.result(timeout=20)
        except (Exception, asyncio.CancelledError) as exc:
            raise RuntimeError("BF_RECOVERING") from exc
        with self.lock:
            if self.closed:
                raise RuntimeError("BF_EXECUTOR_CLOSED")
            if (generation is not self._generation or not generation.thread.is_alive()
                    or not generation.accepting or generation.stop.is_set()
                    or not generation.client.is_connected()):
                self._health.update(status="degraded", error_code="BF_CONNECTION_UNAVAILABLE")
                raise RuntimeError("BF_CONNECTION_UNAVAILABLE")
            # Cancellation cannot prove native work stopped. Publish an independent,
            # non-cancellable outcome Future rather than asyncio's scheduling Future.
            future = Future()
            future.set_running_or_notify_cancel()
            coroutine = self._call(generation, name, arguments)
            try:
                dispatched = asyncio.run_coroutine_threadsafe(coroutine, generation.loop)
            except RuntimeError:
                coroutine.close()
                raise RuntimeError("BF_CONNECTION_UNAVAILABLE") from None
            generation.requests[future] = owner
            self.pending.setdefault(owner, set()).add(future)
        dispatched.add_done_callback(lambda done: self._finished(generation, owner, future, done))
        return future

    def _finished(self, generation, owner, future, dispatched):
        try:
            result, error = dispatched.result(), None
        except BaseException:
            result, error = None, RuntimeError("BF_CALL_OUTCOME_UNKNOWN")
        with self.lock:
            if future not in generation.requests:
                return
            del generation.requests[future]
            if error:
                # A protocol outcome can finish before its native worker does.
                # Keep owner cleanup fenced until this entire thread has exited.
                generation.uncertain_owners.add(owner)
                generation.accepting = False
            if self._generation is generation and not self.closed:
                if error:
                    self.failure = error
                    self._health.update(status="degraded", error_code="BF_CALL_OUTCOME_UNKNOWN")
                elif generation.accepting and not generation.stop.is_set() and self.failure is None:
                    self._health.update(status="healthy", last_success=time.time(), error_code=None)
        if error:
            # An uncertain but connected client must also retire, otherwise its
            # cleanup fence could persist forever with no recovery opportunity.
            self._stop(generation)
        # User callbacks (including journal persistence) must run outside our lock.
        if error:
            future.set_exception(error)
        else:
            future.set_result(result)
        with self.lock:
            self.pending.get(owner, set()).discard(future)
            if not self.pending.get(owner):
                self.pending.pop(owner, None)

    def call(self, name, arguments, *, timeout=180):
        future = self.submit(name, arguments)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            with self.lock:
                if not future.done() and not self.closed:
                    self._health.update(status="degraded", error_code="BF_CALL_TIMEOUT")
            raise

    def snapshot(self):
        with self.lock:
            return dict(self._health)

    def drain(self, owner, *, timeout=5):
        """Wait for outcomes and retirement of a generation with uncertain work."""
        deadline = time.monotonic() + timeout
        with self.lock:
            pending = set(self.pending.get(owner, ()))
            generation = self._generation
        _, running = wait(pending, timeout=timeout)
        if running:
            raise TimeoutError("BF_OPERATIONS_STILL_RUNNING")
        with self.lock:
            uncertain = owner in generation.uncertain_owners
        if uncertain:
            if generation.thread is not threading.current_thread():
                generation.thread.join(timeout=max(0, deadline - time.monotonic()))
            if generation.thread.is_alive():
                raise TimeoutError("BF_OPERATIONS_STILL_RUNNING")

    @staticmethod
    def _stop(generation):
        if generation.loop is not None and not generation.loop.is_closed():
            try:
                generation.loop.call_soon_threadsafe(generation.stop.set)
            except RuntimeError:
                pass  # The loop completed concurrently; never create a replacement.

    def close(self):
        with self.lock:
            self.closed = True
            self._health.update(status="stopped", next_retry=None)
            generation = self._generation
            owners = set(self.pending) | generation.uncertain_owners
        deadline = time.monotonic() + 10
        for owner in owners:
            self.drain(owner, timeout=max(0, deadline - time.monotonic()))
        self._stop(generation)
        generation.thread.join(timeout=5)
        if generation.thread.is_alive():
            raise RuntimeError("BF_SHUTDOWN_INCOMPLETE")
