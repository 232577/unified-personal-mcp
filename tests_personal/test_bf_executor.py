import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastmcp import FastMCP

from personal_mcp.bf.executor import BFExecutor


def test_parallel_callers_use_one_event_loop_and_preserve_arguments():
    mcp = FastMCP("executor fixture")
    threads = []

    @mcp.tool()
    async def inspect_owner(bf_task_id: str):
        threads.append(threading.get_ident())
        await asyncio.sleep(0.02)
        return {"owner": bf_task_id}

    executor = BFExecutor(mcp)
    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda owner: executor.call("inspect_owner", {"bf_task_id": owner}), ("a", "b")))
        assert [r["structuredContent"]["owner"] for r in results] == ["a", "b"]
        assert len(set(threads)) == 1 and threads[0] != threading.get_ident()
    finally:
        executor.close()
    assert not executor.thread.is_alive()


def test_timeout_is_not_replayed_and_drain_waits_for_dispatched_operation():
    mcp = FastMCP("timeout fixture")
    calls = []

    @mcp.tool()
    async def delayed(bf_task_id: str):
        calls.append(bf_task_id)
        await asyncio.sleep(0.1)
        return {"done": True}

    executor = BFExecutor(mcp)
    try:
        with pytest.raises(TimeoutError):
            executor.call("delayed", {"bf_task_id": "a"}, timeout=0.02)
        executor.drain("a", timeout=2)
        assert calls == ["a"]
    finally:
        executor.close()
