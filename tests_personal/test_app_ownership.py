import asyncio

from fastmcp import FastMCP, Client
from bf_automation.task_store import TaskStore
from bf_automation.lease import DesktopLease
from personal_mcp.bf.bootstrap import _wrap_upstream_tool


def test_free_form_launch_cannot_escape_registered_process_ownership(tmp_path):
    called = []
    server = FastMCP("fixture")
    @server.tool(name="App")
    def app(name: str, mode: str = "launch"):
        called.append((name, mode))
        return {"dispatched": True}
    parent = asyncio.run(server.list_tools())[0]
    store = TaskStore(tmp_path / "state", allowed_root=tmp_path)
    token = store.begin(tmp_path)["bf_task_id"]
    _wrap_upstream_tool(server, parent, store=store, lease=DesktopLease())
    async def exercise():
        async with Client(server) as client:
            launched = await client.call_tool_mcp("App", {"name": "Fixture", "bf_task_id": token})
            assert launched.is_error
            switched = await client.call_tool_mcp("App", {"name": "Fixture", "mode": "switch", "bf_task_id": token})
            assert not switched.is_error
    try:
        asyncio.run(exercise())
        assert called == [("Fixture", "switch")]
    finally:
        store.end(token)
