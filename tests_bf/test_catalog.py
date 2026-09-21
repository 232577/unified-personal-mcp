import asyncio

from personal_mcp.bf.bootstrap import build_mcp


def test_catalog_contains_background_tools_and_excludes_high_risk_tools(tmp_path):
    mcp = build_mcp(state_root=tmp_path / "state", allowed_root=tmp_path)
    tools = asyncio.run(mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}
    names = set(by_name)

    assert {
        "task_context",
        "task_diagnostics",
        "prepare_browser_session",
        "WindowInventory",
        "WindowScreenshot",
        "WindowSnapshot",
        "WindowControl",
        "LaunchApplication",
    } <= names
    assert {"PowerShell", "Registry", "FileSystem"}.isdisjoint(names)
    assert "bf_task_id" in by_name["Click"].parameters["required"]
    assert "bf_task_id" in by_name["App"].parameters["required"]


def test_background_window_tools_do_not_expose_desktop_lease_parameter(tmp_path):
    mcp = build_mcp(state_root=tmp_path / "state", allowed_root=tmp_path)
    tools = asyncio.run(mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}

    for name in ("WindowInventory", "WindowScreenshot", "WindowSnapshot", "WindowControl"):
        properties = by_name[name].parameters["properties"]
        assert "bf_task_id" in properties
        assert "desktop_lease" not in properties
