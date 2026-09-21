import asyncio
import json
import os
import sys
from pathlib import Path

from personal_mcp.bf.bootstrap import build_mcp


def test_factory_has_independent_registry_and_no_environment_mutation(tmp_path):
    before = dict(os.environ)
    cwd, path, stdout, stderr = os.getcwd(), list(sys.path), sys.stdout, sys.stderr
    a = build_mcp(state_root=tmp_path / "a", allowed_root=tmp_path, apps_dir=tmp_path / "apps")
    b = build_mcp(state_root=tmp_path / "b", allowed_root=tmp_path, apps_dir=tmp_path / "apps")
    assert a is not b
    assert a._bf_store is not b._bf_store
    assert dict(os.environ) == before
    assert (os.getcwd(), sys.path, sys.stdout, sys.stderr) == (cwd, path, stdout, stderr)
    assert "windows_mcp.__main__" not in sys.modules
    tools = asyncio.run(a.list_tools())
    assert len(tools) == 21
    names = {t.name for t in tools}
    assert {"WindowControl", "WindowScreenshot", "LaunchApplication"} <= names
    assert not {"PowerShell", "Registry", "FileSystem", "Process"} & names


def test_browser_factory_has_full_31_tool_internal_contract(tmp_path):
    browsers = tmp_path / "browsers"
    browsers.mkdir()
    config = tmp_path / "browser.json"
    config.write_text(json.dumps({"enabled": True, "version": 1, "mode": "headless",
        "max_sessions": 4, "max_pages_per_session": 4, "python": sys.executable,
        "browsers_path": str(browsers), "actions_enabled": True, "transfers_enabled": True}), encoding="utf-8")
    server = build_mcp(state_root=tmp_path / "state", allowed_root=tmp_path,
                       apps_dir=tmp_path / "apps", browser_config_path=config)
    tools = asyncio.run(server.list_tools())
    assert len(tools) == 31
    assert all(t.output_schema for t in tools)
    assert server._bf_browser_manager.source_root == Path(__file__).resolve().parents[1]
    server._bf_browser_manager.shutdown()
