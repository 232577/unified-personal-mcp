"""Build a fresh BF registry without importing Windows-MCP's service singleton."""

import asyncio
import json
import threading
from pathlib import Path

FOREGROUND_LEASE_TOOLS = {"App", "Click", "Clipboard", "Move", "Screenshot", "Scroll",
                          "Shortcut", "Snapshot", "Type", "WaitFor"}


def _wrap_upstream_tool(mcp, parent, *, store, lease):
    from fastmcp.tools.tool_transform import TransformedTool, forward

    name = parent.name

    async def guarded(bf_task_id: str, **kwargs):
        store.require(bf_task_id)
        if name == "App" and kwargs.get("mode", "launch") in {"launch", "launch_executable"}:
            raise ValueError("OWNED_APPLICATION_PROFILE_REQUIRED: use LaunchApplication with a registered profile")
        if name in FOREGROUND_LEASE_TOOLS:
            async with lease.hold_async(bf_task_id):
                return await forward(**kwargs)
        return await forward(**kwargs)

    transformed = TransformedTool.from_tool(parent, name=name, transform_fn=guarded,
        description=(parent.description or "") + " Requires task ownership; foreground use takes the desktop lease.")
    mcp._local_provider.remove_tool(name)
    mcp.add_tool(transformed)


def build_mcp(*, state_root, allowed_root, apps_dir=None, controller_factory=None,
              browser_config_path=None, full_control=False):
    from fastmcp import FastMCP
    from windows_mcp.tools import register_all
    from bf_automation.bf_tools import register_bf_tools
    from bf_automation.lease import DesktopLease
    from bf_automation.runtime import safe_upstream_tools
    from bf_automation.task_store import TaskStore
    from bf_automation.tool_contracts import apply_tool_contracts

    state, allowed = Path(state_root).resolve(), Path(allowed_root).resolve()
    apps = Path(apps_dir).resolve() if apps_dir else state / "apps"
    apps.mkdir(parents=True, exist_ok=True)
    desktop, desktop_lock = None, threading.Lock()

    def get_desktop():
        nonlocal desktop
        with desktop_lock:
            if desktop is None:
                from windows_mcp.desktop.service import Desktop
                desktop = Desktop()
        return desktop

    mcp = FastMCP(name="BF automation", instructions="Use task ownership and prefer background Window tools.")
    register_all(mcp, get_desktop=get_desktop, get_analytics=lambda: None)
    permitted = set(safe_upstream_tools())
    initial = {t.name: t for t in asyncio.run(mcp.list_tools())}
    for name in initial.keys() - permitted:
        mcp._local_provider.remove_tool(name)
    store = TaskStore(state, allowed_root=allowed, allow_external_projects=full_control)
    lease = DesktopLease()
    for name in sorted(permitted):
        _wrap_upstream_tool(mcp, initial[name], store=store, lease=lease)
    register_bf_tools(mcp, store=store, lease=lease, allowed_root=allowed, apps_dir=apps,
                      controller_factory=controller_factory)
    mcp._bf_store, mcp._bf_lease = store, lease
    if browser_config_path is not None:
        config = json.loads(Path(browser_config_path).read_text(encoding="utf-8"))
        if type(config.get("enabled")) is not bool:
            raise ValueError("browser enabled must be boolean")
        if config["enabled"]:
            from bf_automation.browser.manager import BrowserManager
            from bf_automation.browser.tools import register_browser_tools
            from bf_automation.hybrid.manager import HybridManager
            hybrid = HybridManager(store, register_end_hook=False)
            manager = BrowserManager(store, browser_config_path, apps,
                source_root=Path(__file__).resolve().parents[2], hybrid_manager=hybrid)
            mcp._bf_browser_manager, mcp._bf_hybrid_manager = manager, hybrid
            register_browser_tools(mcp, store, manager)
    apply_tool_contracts(mcp)
    return mcp
