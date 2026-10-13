"""Build a fresh BF registry without importing Windows-MCP's service singleton."""

import asyncio
import json
import threading
import time
from pathlib import Path

FOREGROUND_LEASE_TOOLS = {"App", "Click", "Clipboard", "Move", "Screenshot", "Scroll",
                          "Shortcut", "Snapshot", "Type", "WaitFor"}


class LegacyDesktopGuard:
    """所有状态读取与输入都由调用方在同一个 desktop lease 内执行。"""

    def __init__(self, get_desktop, *, clock=None, snapshot_ttl=30.0):
        self.get_desktop = get_desktop
        self.clock = clock or time.monotonic
        self.snapshot_ttl = snapshot_ttl
        self.native = None
        self.invalidate()

    def invalidate(self):
        self.owner, self.snapshot, self.created, self.points = None, None, None, ()

    def _native(self):
        if self.native is None:
            from bf_automation.window_control import NativeWinApi
            self.native = NativeWinApi()
        return self.native

    def _label_point(self, owner, label):
        desktop = self.get_desktop()
        if (self.owner != owner or self.snapshot is None
                or self.snapshot is not desktop.desktop_state
                or self.clock() - self.created >= self.snapshot_ttl
                or type(label) is not int or not 0 <= label < len(self.points)):
            raise LookupError("Snapshot label is stale; request a fresh Snapshot in this task")
        return list(self.points[label])

    @staticmethod
    def _snapshot_succeeded(result):
        if result.is_error:
            return False
        for block in result.content:
            if block.type != "text":
                continue
            message = block.text.strip()
            if message.startswith("["):
                try:
                    values = json.loads(message)
                    if isinstance(values, list) and len(values) == 1 and isinstance(values[0], str):
                        message = values[0]
                except ValueError:
                    pass
            if message.startswith("Error capturing desktop state:"):
                return False
        return True

    async def call(self, name, owner, arguments, forward):
        kwargs = dict(arguments)
        if name in {"Click", "Type", "Scroll", "Move"} and kwargs.get("label") is not None:
            kwargs["loc"] = self._label_point(owner, kwargs.pop("label"))
        if name == "Snapshot":
            desktop = self.get_desktop()
            previous = desktop.desktop_state
            self.invalidate()
            result = await forward(**kwargs)
            current = desktop.desktop_state
            if (self._snapshot_succeeded(result) and current is not None and current is not previous
                    and kwargs.get("use_ui_tree", True) is not False):
                tree = getattr(current, "tree_state", None)
                nodes = list(getattr(tree, "interactive_nodes", ())) + list(getattr(tree, "scrollable_nodes", ()))
                try:
                    self.points = tuple((int(node.center.x), int(node.center.y)) for node in nodes)
                except (AttributeError, TypeError, ValueError):
                    self.invalidate()
                else:
                    self.owner, self.snapshot, self.created = owner, current, self.clock()
            return result
        if name in {"Screenshot", "WaitFor", "App", "Click", "Type", "Scroll", "Move", "Shortcut"}:
            self.invalidate()
        if name == "Type":
            return await self._literal_type(**kwargs)
        if name == "WaitFor" and kwargs.get("condition", "").strip().lower().replace("-", "_") in {"enabled", "element_enabled"}:
            return await self._wait_enabled(**kwargs)
        return await forward(**kwargs)

    async def _literal_type(self, *, text, loc=None, clear=False, caret_position="idle", press_enter=False, **kwargs):
        from fastmcp.tools.base import ToolResult
        from mcp.types import TextContent

        if not isinstance(loc, list) or len(loc) != 2 or any(type(value) is not int for value in loc):
            raise ValueError("Type requires an explicit desktop point or a fresh Snapshot label")
        native = self._native()
        gui, uia = native.win32gui, native.uia
        target = gui.GetAncestor(gui.WindowFromPoint(tuple(loc)), 2)
        if not target or not gui.IsWindow(target):
            raise LookupError("Type point has no valid target; request a fresh Snapshot")

        def require_focus():
            if gui.GetForegroundWindow() != target:
                raise RuntimeError("foreground focus changed; text input outcome unknown")

        uia.Click(*loc, waitTime=0.05)
        require_focus()
        if caret_position in {"start", "end"}:
            require_focus()
            uia.SendKeys("{Home}" if caret_position == "start" else "{End}")
        if clear:
            require_focus()
            uia.SendKeys("{Ctrl}a")
            require_focus()
            uia.SendKeys("{Back}")
        for char in text:
            if char == "\r":
                continue
            if char in {"\n", "\t"}:
                require_focus()
                uia.SendKeys("{Enter}" if char == "\n" else "{Tab}")
            else:
                encoded = char.encode("utf-16-le")
                for offset in range(0, len(encoded), 2):
                    require_focus()
                    unit = chr(int.from_bytes(encoded[offset:offset + 2], "little"))
                    from bf_automation.native_input import send_unicode_char
                    if send_unicode_char(uia, unit) != 2:
                        raise RuntimeError("text input outcome unknown")
            await asyncio.sleep(0.01)
        if press_enter:
            require_focus()
            uia.SendKeys("{Enter}")
        return ToolResult(content=[TextContent(type="text", text="Literal text input dispatched; verify the target state.")])

    def _enabled_observation(self, text, window_name):
        uia = self._native().uia
        try:
            with uia.UIAutomationInitializerInThread():
                visited = 0
                for window in uia.GetRootControl().GetChildren():
                    if window_name and window_name.casefold() not in str(window.Name or "").casefold():
                        continue
                    for control, _ in uia.WalkControl(window, includeTop=False, maxDepth=12):
                        visited += 1
                        if visited > 2000:
                            raise NotImplementedError("enabled observation exceeds the bounded provider scan")
                        values = (str(control.Name or ""), str(control.ControlTypeName or ""))
                        if text and not any(text.casefold() in value.casefold() for value in values):
                            continue
                        enabled = control.IsEnabled
                        if type(enabled) is not bool:
                            raise NotImplementedError("enabled state cannot be verified by this provider")
                        if enabled:
                            return True
        except NotImplementedError:
            raise
        except Exception as exc:
            raise NotImplementedError("enabled state cannot be verified by this provider") from exc
        return False

    async def _wait_enabled(self, *, condition, text=None, window_name=None, timeout=10.0,
                            interval=0.25, use_dom=False, **kwargs):
        from fastmcp.tools.base import ToolResult
        from mcp.types import TextContent
        from windows_mcp.tools.input import _validate_wait_for_args

        _validate_wait_for_args(condition, text, window_name, timeout, interval)
        if use_dom:
            raise NotImplementedError("enabled DOM state requires BrowserWaitFor; desktop enabled observation is unsupported")
        deadline = self.clock() + timeout
        while True:
            if self._enabled_observation(text, window_name):
                return ToolResult(content=[TextContent(type="text", text="WaitFor element_enabled satisfied by a live enabled control.")])
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for a verified enabled control")
            await asyncio.sleep(min(interval, remaining))


def _wrap_upstream_tool(mcp, parent, *, store, lease, desktop_guard=None):
    from fastmcp.tools.tool_transform import TransformedTool, forward

    name = parent.name

    async def guarded(bf_task_id: str, **kwargs):
        store.require(bf_task_id)
        if name == "App" and kwargs.get("mode", "launch") in {"launch", "launch_executable"}:
            raise ValueError("OWNED_APPLICATION_PROFILE_REQUIRED: use LaunchApplication with a registered profile")
        if name in FOREGROUND_LEASE_TOOLS:
            async with lease.hold_async(bf_task_id):
                store.require(bf_task_id)
                if desktop_guard is not None:
                    return await desktop_guard.call(name, bf_task_id, kwargs, forward)
                return await forward(**kwargs)
        return await forward(**kwargs)

    transformed = TransformedTool.from_tool(parent, name=name, transform_fn=guarded,
        description=(parent.description or "") + " Requires task ownership; foreground use takes the desktop lease.")
    mcp._local_provider.remove_tool(name)
    mcp.add_tool(transformed)


def build_mcp(*, state_root, allowed_root, apps_dir=None, controller_factory=None,
              browser_config_path=None, full_control=False, max_hybrid_instances=2):
    from fastmcp import FastMCP
    from windows_mcp.tools import register_all
    from bf_automation.bf_tools import register_bf_tools
    from bf_automation.lease import DesktopLease
    from bf_automation.runtime import safe_upstream_tools
    from bf_automation.task_store import TaskStore
    from bf_automation.tool_contracts import apply_tool_contracts
    from personal_mcp.overlays.console_policy import install_bf_console_policy

    install_bf_console_policy()
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
    desktop_guard = LegacyDesktopGuard(get_desktop)
    for name in sorted(permitted):
        _wrap_upstream_tool(mcp, initial[name], store=store, lease=lease, desktop_guard=desktop_guard)
    register_bf_tools(mcp, store=store, lease=lease, allowed_root=allowed, apps_dir=apps,
                      controller_factory=controller_factory)
    mcp._bf_store, mcp._bf_lease = store, lease
    mcp._bf_desktop_guard = desktop_guard
    if browser_config_path is not None:
        config = json.loads(Path(browser_config_path).read_text(encoding="utf-8"))
        if type(config.get("enabled")) is not bool:
            raise ValueError("browser enabled must be boolean")
        if config["enabled"]:
            from bf_automation.browser.manager import BrowserManager
            from bf_automation.browser.tools import register_browser_tools
            from bf_automation.hybrid.manager import HybridManager
            hybrid = HybridManager(store, register_end_hook=False, max_managed=max_hybrid_instances)
            manager = BrowserManager(store, browser_config_path, apps,
                source_root=Path(__file__).resolve().parents[2], hybrid_manager=hybrid)
            mcp._bf_browser_manager, mcp._bf_hybrid_manager = manager, hybrid
            register_browser_tools(mcp, store, manager)
    apply_tool_contracts(mcp)
    return mcp
