import asyncio
import threading

import pytest

from bf_automation.lease import DesktopLease
from bf_automation.task_store import TaskStore


def test_same_task_cannot_reenter_desktop_from_another_request():
    lease = DesktopLease()
    acquired = []

    def contender():
        try:
            with lease.hold("same-task", timeout=0.03):
                acquired.append(True)
        except TimeoutError:
            acquired.append(False)

    with lease.hold("same-task"):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(1)
        assert not thread.is_alive()
        assert acquired == [False]
        assert lease.status("same-task")["desktop_lease"] == "owned_by_task"


def test_async_desktop_wait_does_not_block_event_loop_and_cancellation_releases():
    lease = DesktopLease()
    assert callable(getattr(lease, "hold_async", None))

    async def run():
        entered = asyncio.Event()
        order = []

        async def first():
            async with lease.hold_async("a"):
                order.append("a-start")
                entered.set()
                await asyncio.sleep(0.05)
                order.append("a-end")

        async def second():
            await entered.wait()
            async with lease.hold_async("b", timeout=1):
                order.append("b")

        await asyncio.wait_for(asyncio.gather(first(), second()), timeout=2)
        assert order == ["a-start", "a-end", "b"]
        async with lease.hold_async("a"):
            waiting = asyncio.create_task(second())
            await asyncio.sleep(0.01)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
        assert lease.status()["desktop_lease"] == "available"

    asyncio.run(run())


def test_window_write_ownership_survives_new_store_and_releases_on_end(tmp_path):
    store = TaskStore(tmp_path / "state", allowed_root=tmp_path)
    a = store.begin(tmp_path)["bf_task_id"]
    b = store.begin(tmp_path)["bf_task_id"]
    wa = store.bind_window(a, hwnd=100, pid=10, title="target")
    wb = store.bind_window(b, hwnd=100, pid=10, title="target")
    assert callable(getattr(store, "claim_window", None))
    store.claim_window(a, wa)
    other = TaskStore(tmp_path / "state", allowed_root=tmp_path)
    with pytest.raises(PermissionError, match="another task"):
        other.claim_window(b, wb)
    store.end(a)
    other.claim_window(b, wb)
    # A different actual window remains independently writable.
    c = store.begin(tmp_path)["bf_task_id"]
    wc = store.bind_window(c, hwnd=200, pid=20, title="other")
    store.claim_window(c, wc)
