import asyncio
from contextlib import nullcontext
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from personal_mcp.bf import bootstrap
from bf_automation import window_control
from bf_automation.tool_contracts import _governed_tool


def state(x=10):
    return SimpleNamespace(tree_state=SimpleNamespace(interactive_nodes=[
        SimpleNamespace(center=SimpleNamespace(x=x, y=20))], scrollable_nodes=[]))


@pytest.fixture
def desktop_tools(monkeypatch):
    desktop = SimpleNamespace(desktop_state=None)
    calls, tool_context = [], ContextVar("fixture-tool")
    native_state = {"hwnd": 100}
    uia = SimpleNamespace(UIAutomationInitializerInThread=nullcontext,
                          Click=lambda *args, **kwargs: calls.append(("click", args)),
                          SendKeys=lambda keys, **kwargs: calls.append(("keys", keys)),
                          SendUnicodeChar=lambda char: calls.append(("unicode", char)) or 2)
    native = SimpleNamespace(uia=uia, win32gui=SimpleNamespace(
        WindowFromPoint=lambda point: 101, GetAncestor=lambda hwnd, flags: 100,
        GetForegroundWindow=lambda: native_state["hwnd"], IsWindow=lambda hwnd: True))
    monkeypatch.setattr(window_control, "NativeWinApi", lambda: native)
    import windows_mcp.tools as upstream
    import windows_mcp.desktop.service as desktop_module
    import fastmcp.tools.tool_transform as transform
    import fastmcp
    import bf_automation.bf_tools as bf_tools
    import bf_automation.task_store as tasks
    import bf_automation.tool_contracts as contracts
    import personal_mcp.overlays.console_policy as console

    class Registry:
        def __init__(self, **kwargs):
            self.tools = {}
            self._local_provider = SimpleNamespace(remove_tool=lambda name: self.tools.pop(name))

        async def list_tools(self):
            return list(self.tools.values())

        def add_tool(self, tool):
            self.tools[tool.name] = tool

    monkeypatch.setattr(fastmcp, "FastMCP", Registry)
    monkeypatch.setattr(tasks, "TaskStore", lambda *args, **kwargs: SimpleNamespace(require=lambda token: None))
    monkeypatch.setattr(console, "install_bf_console_policy", lambda: None)
    monkeypatch.setattr(bf_tools, "register_bf_tools", lambda *args, **kwargs: None)
    monkeypatch.setattr(contracts, "apply_tool_contracts", lambda *args: None)
    monkeypatch.setattr(desktop_module, "Desktop", lambda: desktop)

    def register(mcp, **kwargs):
        for name in bootstrap.FOREGROUND_LEASE_TOOLS | {"DisplayInventory", "Wait", "Notification"}:
            mcp.add_tool(SimpleNamespace(name=name, description="fixture"))

    monkeypatch.setattr(upstream, "register_all", register)
    monkeypatch.setattr(transform.TransformedTool, "from_tool", lambda parent, **kwargs:
                        SimpleNamespace(name=parent.name, fn=kwargs["transform_fn"]))

    async def forward(**kwargs):
        name = tool_context.get()
        if name == "Snapshot" and kwargs.get("fail"):
            return ToolResult(content=[TextContent(type="text", text="fixture failed")], is_error=True)
        if name == "Snapshot" and kwargs.get("legacy_capture_error"):
            desktop.desktop_state = state(900)
            return ToolResult(content=[TextContent(type="text", text='["Error capturing desktop state: fixture failed"]')])
        if name in {"Snapshot", "Screenshot", "WaitFor"}:
            desktop.desktop_state = state(500 if kwargs.get("alternate") else 10)
        if name == "Type":
            calls.append(("legacy-clipboard-type", kwargs["text"]))
        elif name not in {"Snapshot", "Screenshot", "WaitFor"}:
            calls.append((name, kwargs))
        return ToolResult(content=[TextContent(type="text", text="fixture returned")])

    monkeypatch.setattr(transform, "forward", forward)
    current = Path.cwd()
    server = bootstrap.build_mcp(state_root=current, allowed_root=current, apps_dir=current)

    async def run(name, owner="a", **kwargs):
        token = tool_context.set(name)
        try:
            return await server.tools[name].fn(bf_task_id=owner, **kwargs)
        finally:
            tool_context.reset(token)

    return SimpleNamespace(server=server, run=run, calls=calls, desktop=desktop, native=native,
                           native_state=native_state, uia=uia)


@pytest.mark.parametrize("name", ["Click", "Type", "Scroll", "Move"])
def test_snapshot_labels_cannot_be_borrowed_by_another_task(desktop_tools, name):
    async def scenario():
        await desktop_tools.run("Snapshot", "a")
        with pytest.raises(LookupError, match="Snapshot"):
            await desktop_tools.run(name, "b", label=0, text="fixture")
    asyncio.run(scenario())
    assert desktop_tools.calls == []


@pytest.mark.parametrize("observer", ["Snapshot", "Screenshot", "WaitFor"])
def test_other_observation_invalidates_previous_snapshot_labels(desktop_tools, observer):
    async def scenario():
        await desktop_tools.run("Snapshot", "a")
        await desktop_tools.run(observer, "b", alternate=True)
        with pytest.raises(LookupError, match="Snapshot"):
            await desktop_tools.run("Click", "a", label=0)
    asyncio.run(scenario())
    assert desktop_tools.calls == []


def test_type_keeps_long_text_literal_without_clipboard(desktop_tools):
    value = "中文 {Win} 😀 " + "long literal input " * 4
    asyncio.run(desktop_tools.run("Type", loc=[10, 20], text=value))
    unicode = [value for mode, value in desktop_tools.calls if mode == "unicode"]
    expected = value.encode("utf-16-le")
    assert [ord(char) for char in unicode] == [int.from_bytes(expected[i:i + 2], "little")
                                              for i in range(0, len(expected), 2)]
    assert not any(mode == "legacy-clipboard-type" for mode, _ in desktop_tools.calls)


def test_type_stops_if_focus_changes_mid_text(desktop_tools):
    def send(char):
        desktop_tools.calls.append(("unicode", char))
        desktop_tools.native_state["hwnd"] = 200
        return 2
    desktop_tools.uia.SendUnicodeChar = send
    with pytest.raises(RuntimeError, match="focus"):
        asyncio.run(desktop_tools.run("Type", loc=[10, 20], text="abc"))
    assert [value for mode, value in desktop_tools.calls if mode == "unicode"] == ["a"]


def test_enabled_wait_refuses_dom_or_unverifiable_provider(desktop_tools):
    with pytest.raises(NotImplementedError, match="enabled"):
        asyncio.run(desktop_tools.run("WaitFor", condition="element_enabled", text="Save", use_dom=True))


def test_enabled_wait_does_not_accept_disabled_matching_control(desktop_tools):
    control = SimpleNamespace(Name="Save", ControlTypeName="ButtonControl", IsEnabled=False)
    desktop_tools.uia.GetRootControl = lambda: SimpleNamespace(GetChildren=lambda: [
        SimpleNamespace(Name="Fixture")])
    desktop_tools.uia.WalkControl = lambda root, **kwargs: [(control, 1)]
    with pytest.raises(TimeoutError):
        asyncio.run(desktop_tools.run("WaitFor", condition="element_enabled", text="Save",
                                     timeout=0.01, interval=0.005))


def test_capture_mapping_failure_has_specific_contract_error(monkeypatch):
    import fastmcp.tools.tool_transform as transform
    import bf_automation.tool_contracts as contracts
    from fastmcp.tools.base import Tool

    def screenshot(bf_task_id: str, window_id: str):
        pass

    parent = Tool.from_function(screenshot, name="WindowScreenshot")
    monkeypatch.setattr(transform.TransformedTool, "from_tool", lambda parent, **kwargs:
                        SimpleNamespace(fn=kwargs["transform_fn"]))

    async def failed(**kwargs):
        raise window_control.CaptureGeometryUnavailable("COORDINATE_MAPPING_UNAVAILABLE: fixture")

    monkeypatch.setattr(contracts, "forward", failed)
    governed = _governed_tool(parent)
    result = asyncio.run(governed.fn(bf_task_id="bf_" + "a" * 32, window_id="bfw_" + "b" * 24))
    assert result.structured_content["error"]["code"] == "COORDINATE_MAPPING_UNAVAILABLE"


def test_fresh_owner_label_is_resolved_under_lease_to_its_frozen_point(desktop_tools):
    async def scenario():
        await desktop_tools.run("Snapshot", "a")
        await desktop_tools.run("Click", "a", label=0)
    asyncio.run(scenario())
    assert desktop_tools.calls == [("Click", {"loc": [10, 20]})]


def test_snapshot_label_ttl_refuses_expired_observation(desktop_tools):
    async def scenario():
        await desktop_tools.run("Snapshot", "a")
        guard = desktop_tools.server._bf_desktop_guard
        guard.clock = lambda: guard.created + 31
        with pytest.raises(LookupError, match="Snapshot"):
            await desktop_tools.run("Click", "a", label=0)
    asyncio.run(scenario())
    assert desktop_tools.calls == []


def test_snapshot_label_refuses_external_state_replacement(desktop_tools):
    async def scenario():
        await desktop_tools.run("Snapshot", "a")
        desktop_tools.desktop.desktop_state = state(900)
        with pytest.raises(LookupError, match="Snapshot"):
            await desktop_tools.run("Click", "a", label=0)
    asyncio.run(scenario())
    assert desktop_tools.calls == []


def test_failed_snapshot_does_not_revive_previous_labels(desktop_tools):
    async def scenario():
        await desktop_tools.run("Snapshot", "a")
        await desktop_tools.run("Snapshot", "a", fail=True)
        with pytest.raises(LookupError, match="Snapshot"):
            await desktop_tools.run("Click", "a", label=0)
    asyncio.run(scenario())
    assert desktop_tools.calls == []


def test_legacy_string_capture_failure_cannot_create_new_usable_labels(desktop_tools):
    async def scenario():
        await desktop_tools.run("Snapshot", "a", legacy_capture_error=True)
        with pytest.raises(LookupError, match="Snapshot"):
            await desktop_tools.run("Click", "a", label=0)
    asyncio.run(scenario())
    assert desktop_tools.calls == []


def test_type_preserves_caret_clear_newline_tab_and_submission_semantics(desktop_tools):
    asyncio.run(desktop_tools.run("Type", loc=[10, 20], text="{a}\r\n\tb",
                                 clear=True, caret_position="end", press_enter=True))
    assert [value for mode, value in desktop_tools.calls if mode == "keys"] == [
        "{End}", "{Ctrl}a", "{Back}", "{Enter}", "{Tab}", "{Enter}"]
    assert [value for mode, value in desktop_tools.calls if mode == "unicode"] == ["{", "a", "}", "b"]


def test_type_no_text_is_sent_if_click_did_not_focus_target(desktop_tools):
    desktop_tools.native_state["hwnd"] = 200
    with pytest.raises(RuntimeError, match="focus"):
        asyncio.run(desktop_tools.run("Type", loc=[10, 20], text="abc"))
    assert not any(mode == "unicode" for mode, _ in desktop_tools.calls)


def test_enabled_wait_uses_live_false_to_true_state(desktop_tools):
    count = []
    desktop_tools.uia.GetRootControl = lambda: SimpleNamespace(GetChildren=lambda: [SimpleNamespace(Name="Fixture")])

    def controls(root, **kwargs):
        count.append("poll")
        return [(SimpleNamespace(Name="Save", ControlTypeName="ButtonControl", IsEnabled=len(count) > 1), 1)]

    desktop_tools.uia.WalkControl = controls
    result = asyncio.run(desktop_tools.run("WaitFor", condition="enabled", text="Save", window_name="Fixture",
                                          timeout=1, interval=0.005))
    assert len(count) == 2 and not result.is_error


def test_enabled_wait_refuses_unknown_enabled_attribute(desktop_tools):
    desktop_tools.uia.GetRootControl = lambda: SimpleNamespace(GetChildren=lambda: [SimpleNamespace(Name="Fixture")])
    desktop_tools.uia.WalkControl = lambda *args, **kwargs: [(SimpleNamespace(Name="Save", ControlTypeName="ButtonControl"), 1)]
    with pytest.raises(NotImplementedError, match="enabled"):
        asyncio.run(desktop_tools.run("WaitFor", condition="enabled", text="Save"))


def test_stale_label_is_exposed_as_stale_target_contract_error(monkeypatch):
    import fastmcp.tools.tool_transform as transform
    import bf_automation.tool_contracts as contracts
    from fastmcp.tools.base import Tool

    def click(bf_task_id: str, label: int):
        pass

    parent = Tool.from_function(click, name="Click")
    monkeypatch.setattr(transform.TransformedTool, "from_tool", lambda parent, **kwargs:
                        SimpleNamespace(fn=kwargs["transform_fn"]))

    async def stale(**kwargs):
        raise LookupError("Snapshot is stale")

    monkeypatch.setattr(contracts, "forward", stale)
    result = asyncio.run(_governed_tool(parent).fn(bf_task_id="bf_" + "a" * 32, label=0))
    assert result.structured_content["error"]["code"] == "STALE_TARGET"


def test_literal_type_result_passes_real_transformed_tool_contract():
    from fastmcp.tools.base import Tool
    from bf_automation.lease import DesktopLease

    calls = []
    native = SimpleNamespace(win32gui=SimpleNamespace(WindowFromPoint=lambda point: 101,
        GetAncestor=lambda hwnd, flags: 100, IsWindow=lambda hwnd: True, GetForegroundWindow=lambda: 100),
        uia=SimpleNamespace(Click=lambda *args, **kwargs: None,
                            SendUnicodeChar=lambda char: calls.append(char) or 2))

    def legacy(text: str, loc: list[int], clear: bool = False, caret_position: str = "idle",
               press_enter: bool = False) -> str:
        raise AssertionError("legacy clipboard typing must not run")

    parent = Tool.from_function(legacy, name="Type")
    holders = []
    mcp = SimpleNamespace(_local_provider=SimpleNamespace(remove_tool=lambda name: None), add_tool=holders.append)
    guard = bootstrap.LegacyDesktopGuard(lambda: SimpleNamespace(desktop_state=None))
    guard.native = native
    bootstrap._wrap_upstream_tool(mcp, parent, store=SimpleNamespace(require=lambda token: None),
                                  lease=DesktopLease(), desktop_guard=guard)
    result = asyncio.run(_governed_tool(holders[0]).run({
        "bf_task_id": "bf_" + "a" * 32, "loc": [10, 20], "text": "{Win}中文😀"}))
    assert not result.is_error
    assert result.structured_content["status"] == "returned"
    assert result.structured_content["verification_required"]
    assert calls[:5] == list("{Win}")


def test_mapping_failure_from_real_provider_preserves_specific_contract_error():
    from fastmcp.tools.base import Tool

    def screenshot(bf_task_id: str, window_id: str) -> dict:
        raise window_control.CaptureGeometryUnavailable("COORDINATE_MAPPING_UNAVAILABLE: fixture")

    parent = Tool.from_function(screenshot, name="WindowScreenshot")
    result = asyncio.run(_governed_tool(parent).run({
        "bf_task_id": "bf_" + "a" * 32, "window_id": "bfw_" + "b" * 24}))
    assert result.is_error
    assert result.structured_content["error"]["code"] == "COORDINATE_MAPPING_UNAVAILABLE"
