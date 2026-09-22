import asyncio
import json
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastmcp import FastMCP

from personal_mcp.bf.bootstrap import build_mcp
from personal_mcp.bf.executor import BFExecutor
from personal_mcp.host import WorkflowResources
from bf_automation.task_store import TaskStore


def retire(executor):
    old = executor.thread
    executor.loop.call_soon_threadsafe(executor.stop.set)
    old.join(5)
    assert not old.is_alive()
    return old


def test_real_lifespan_reconnect_preserves_store_managers_and_owned_pid(tmp_path):
    browsers = tmp_path / "browsers"
    browsers.mkdir()
    config = tmp_path / "browser.json"
    config.write_text(json.dumps({"enabled": True, "version": 1, "mode": "headless",
        "max_sessions": 4, "max_pages_per_session": 4, "python": sys.executable,
        "browsers_path": str(browsers), "actions_enabled": True, "transfers_enabled": True}),
        encoding="utf-8")
    server = build_mcp(state_root=tmp_path / "state", allowed_root=tmp_path,
                       browser_config_path=config)
    store, browser, hybrid = server._bf_store, server._bf_browser_manager, server._bf_hybrid_manager
    lifespan = server._lifespan
    transitions = []

    @asynccontextmanager
    async def observed_lifespan(mcp):
        transitions.append("enter")
        try:
            async with lifespan(mcp) as value:
                yield value
        finally:
            transitions.append("exit")

    server._lifespan = observed_lifespan
    token = store.begin(tmp_path)["bf_task_id"]
    process = store.spawn_owned(token, [sys.executable, "-c", "import time; time.sleep(60)"])
    executor = BFExecutor(server)
    try:
        assert not executor.call("Wait", {"bf_task_id": token, "duration": 0})["isError"]
        old = retire(executor)
        assert transitions == ["enter", "exit"]
        barrier = threading.Barrier(6)

        def call(_):
            barrier.wait(5)
            return executor.submit("Wait", {"bf_task_id": token, "duration": 0}).result(5)

        with ThreadPoolExecutor(6) as pool:
            assert all(not result["isError"] for result in pool.map(call, range(6)))
        assert executor.thread is not old
        assert transitions == ["enter", "exit", "enter"]
        assert server._bf_store is store and server._bf_browser_manager is browser
        assert server._bf_hybrid_manager is hybrid
        assert process.poll() is None
        assert store.status(token)["status"] == "active"
        health = executor.snapshot()
        assert health["status"] == "healthy" and health["recovery_attempts"] == 1
        assert health["last_success"] and health["error_code"] is None
        assert health["next_retry"] is None
    finally:
        executor.close()
        store.end(token)
        browser.shutdown()
        process.wait(5)
    assert transitions == ["enter", "exit", "enter", "exit"]


def test_timeout_and_cancel_do_not_cancel_or_replay_dispatched_work():
    server = FastMCP("future fixture")
    entered, release = threading.Event(), threading.Event()
    calls = []

    @server.tool()
    async def effect(bf_task_id: str):
        calls.append(bf_task_id)
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return {"done": True}

    executor = BFExecutor(server)
    try:
        future = executor.submit("effect", {"bf_task_id": "owner"})
        assert isinstance(future, Future) and entered.wait(3)
        assert not future.cancel()
        with pytest.raises(TimeoutError):
            future.result(0.01)
        with pytest.raises(TimeoutError, match="BF_OPERATIONS_STILL_RUNNING"):
            executor.drain("owner", timeout=0.01)
        release.set()
        assert future.result(3)["structuredContent"]["done"]
        executor.drain("owner")
        retire(executor)
        assert executor.submit("effect", {"bf_task_id": "next"}).result(5)
        assert calls == ["owner", "next"]
    finally:
        release.set()
        executor.close()


def test_live_exiting_generation_is_not_replaced_and_close_is_terminal():
    entered, release = threading.Event(), threading.Event()

    @asynccontextmanager
    async def lifespan(_):
        try:
            yield {}
        finally:
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)

    server = FastMCP("slow exit", lifespan=lifespan)

    @server.tool()
    def noop():
        return True

    executor = BFExecutor(server)
    old = executor.thread
    try:
        executor.loop.call_soon_threadsafe(executor.stop.set)
        assert entered.wait(3)
        assert executor.snapshot()["status"] == "degraded"
        with pytest.raises(RuntimeError, match="BF_(RECOVERING|CONNECTION_UNAVAILABLE)"):
            executor.submit("noop", {})
        assert executor.thread is old and old.is_alive()
        assert executor.snapshot()["status"] == "degraded"
        assert executor.snapshot()["recovery_attempts"] == 0
    finally:
        release.set()
        old.join(5)
        executor.close()
    with pytest.raises(RuntimeError, match="BF_EXECUTOR_CLOSED"):
        executor.submit("noop", {})
    assert executor.snapshot()["status"] == "stopped"


def test_old_completion_cannot_poison_recovered_health():
    server = FastMCP("late completion")

    @server.tool()
    def noop():
        return True

    executor = BFExecutor(server)
    try:
        old = executor._generation
        retire(executor)
        executor.submit("noop", {}).result(3)
        current = executor.snapshot()
        late = Future()
        late.set_exception(RuntimeError("old transport failed"))
        # A delayed scheduler callback belongs to its original generation.
        executor._finished(old, "old", Future(), late)
        assert executor.snapshot() == current
    finally:
        executor.close()


def test_cancelled_real_client_session_archives_unknown_without_replay(tmp_path, monkeypatch):
    # Runner's default executor grace period must not let native work outlive the
    # generation used as our cleanup fence. Compress the pinned runtime timeout.
    monkeypatch.setattr(asyncio.constants, "THREAD_JOIN_TIMEOUT", 0.02)
    server = FastMCP("session failure fixture")
    entered, release = threading.Event(), threading.Event()
    calls = []
    store = TaskStore(tmp_path / "state", allowed_root=tmp_path)
    token = store.begin(tmp_path)["bf_task_id"]

    def native_work():
        entered.set()
        release.wait(10)
        store.require(token)
        (tmp_path / "native-result.txt").write_text("once", encoding="utf-8")

    @server.tool()
    async def effect(bf_task_id: str):
        calls.append(bf_task_id)
        await asyncio.to_thread(native_work)
        return True

    @server.tool()
    def noop():
        return True

    executor = BFExecutor(server)
    resource = WorkflowResources.__new__(WorkflowResources)
    resource.key, resource.bf_token, resource.bf_closed = "workflow", token, False
    resource.host = SimpleNamespace(bf=executor, bf_server=SimpleNamespace(_bf_store=store),
                                   searches=SimpleNamespace(end_owner=lambda _: None))
    resource.coding = SimpleNamespace(close=lambda: None)
    real_drain = executor.drain
    monkeypatch.setattr(executor, "drain", lambda owner, timeout=0.01: real_drain(owner, timeout=timeout))
    try:
        old = executor.thread
        future = executor.submit("effect", {"bf_task_id": token})
        assert entered.wait(3)
        # Isolated in-memory client fault, never a running service or tunnel.
        executor.loop.call_soon_threadsafe(executor.client._session_state.session_task.cancel)
        with pytest.raises(RuntimeError, match="BF_CALL_OUTCOME_UNKNOWN"):
            future.result(5)
        old.join(0.15)
        assert old.is_alive()
        # WorkflowResources must not mistake the completed protocol Future for idle
        # native work while asyncio is still waiting for its worker thread.
        assert executor.failure is not None
        with pytest.raises(TimeoutError, match="BF_OPERATIONS_STILL_RUNNING"):
            executor.drain(token, timeout=0.01)
        executor.drain("unrelated-owner", timeout=0)
        with pytest.raises(RuntimeError, match="WORKFLOW_CLEANUP_INCOMPLETE"):
            resource.close()
        assert store.status(token)["status"] == "active"
        assert not resource.bf_closed
        with pytest.raises(RuntimeError, match="BF_CONNECTION_UNAVAILABLE"):
            executor.submit("noop", {})
        assert executor.thread is old
        release.set()
        old.join(5)
        assert not old.is_alive()
        assert (tmp_path / "native-result.txt").read_text(encoding="utf-8") == "once"
        executor.drain(token, timeout=1)
        resource.close()
        assert resource.bf_closed
        assert executor.submit("noop", {}).result(5)
        assert calls == [token]
        assert executor.snapshot()["status"] == "healthy"
        assert executor.snapshot()["recovery_attempts"] == 1
    finally:
        release.set()
        executor.close()
        resource.close()


def test_close_during_recovery_prevents_dispatch_and_further_generations():
    entered, release = threading.Event(), threading.Event()
    entries, calls = [], []

    @asynccontextmanager
    async def lifespan(_):
        entries.append("entry")
        if len(entries) == 2:
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
        yield {}

    server = FastMCP("close recovery fixture", lifespan=lifespan)

    @server.tool()
    def effect():
        calls.append("effect")
        return True

    executor = BFExecutor(server)
    try:
        retire(executor)
        with ThreadPoolExecutor(2) as pool:
            submitting = pool.submit(executor.submit, "effect", {})
            assert entered.wait(3)
            closing = pool.submit(executor.close)
            # Wait for the terminal flag via its actual state lock.
            for _ in range(300):
                with executor.lock:
                    if executor.closed:
                        break
                threading.Event().wait(0.01)
            assert executor.closed
            release.set()
            closing.result(5)
            with pytest.raises(RuntimeError, match="BF_EXECUTOR_CLOSED"):
                submitting.result(5)
        with pytest.raises(RuntimeError, match="BF_EXECUTOR_CLOSED"):
            executor.submit("effect", {})
        assert entries == ["entry", "entry"] and calls == []
        assert not executor.thread.is_alive()
        assert executor.snapshot()["status"] == "stopped"
    finally:
        release.set()
        executor.close()


def test_unknown_outcome_retires_still_connected_generation(monkeypatch):
    server = FastMCP("uncertain response fixture")
    effects = []

    @server.tool()
    def effect():
        effects.append("once")
        return True

    executor = BFExecutor(server)
    original = executor._call

    async def lost_response(generation, name, arguments):
        await original(generation, name, arguments)
        assert generation.client.is_connected()
        raise RuntimeError("fixture response lost after side effect")

    try:
        old = executor.thread
        monkeypatch.setattr(executor, "_call", lost_response)
        future = executor.submit("effect", {})
        with pytest.raises(RuntimeError, match="BF_CALL_OUTCOME_UNKNOWN"):
            future.result(3)
        old.join(1)
        assert not old.is_alive(), "uncertain generation must retire even if transport stays connected"
        executor.drain("", timeout=0.1)
        assert effects == ["once"]
        monkeypatch.setattr(executor, "_call", original)
        assert executor.submit("effect", {}).result(3)
        assert effects == ["once", "once"]
    finally:
        executor.close()
