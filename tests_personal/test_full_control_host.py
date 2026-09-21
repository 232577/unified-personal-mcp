from pathlib import Path

import pytest

from personal_mcp.bf.bootstrap import build_mcp
from personal_mcp.config import load_config
from personal_mcp.host import UnifiedRuntime
from tests_personal.test_config import installation


@pytest.fixture
def full_host(tmp_path):
    cfg = load_config(installation(tmp_path, permission_mode="full_control"))
    bf = build_mcp(state_root=cfg.data_root / "bf", allowed_root=cfg.workspace_root,
                   full_control=True)
    host = UnifiedRuntime(cfg, auth_token="fixture-backend-" + "x" * 32, bf_server=bf)
    try:
        yield host
    finally:
        host.close()


def test_full_control_host_activates_external_project_and_routes_files(full_host, tmp_path):
    external = tmp_path / "external project"
    external.mkdir()
    (external / "readme.txt").write_text("external project", encoding="utf-8")
    result = full_host.call_tool("UnifiedTask", {"action": "begin", "project_path": str(external),
                                               "request_id": "external"})
    token = result["structuredContent"]["workflow_id"]
    active = full_host.call_tool("UnifiedTask", {"action": "activate", "workflow_id": token})
    assert not active["isError"], active
    read = full_host.call_tool("read_file", {"workflow_id": token, "path": str(external / "readme.txt")})
    assert read["structuredContent"]["content"] == "external project"
    wait = full_host.call_tool("Wait", {"workflow_id": token, "duration": 0})
    assert not wait["isError"], wait
    ended = full_host.call_tool("UnifiedTask", {"action": "end", "workflow_id": token})
    assert ended["structuredContent"]["state"] == "ENDED"


def test_full_control_discovery_reports_actual_scope(full_host):
    info = full_host.call_tool("server_info", {})["structuredContent"]
    assert info["permission_mode"] == "full_control"
    assert info["absolute_paths_allowed"] is True
    assert info["filesystem_scope"] == "current_windows_user"
    tools = {tool["name"]: tool for tool in full_host.list_tools()["tools"]}
    assert "absolute paths" in tools["exec_command"]["description"]
    assert "absolute" in tools["read_file"]["description"]


def test_full_control_keeps_default_project_write_lease(full_host):
    project = full_host.config.project("app")
    first = full_host.call_tool("UnifiedTask", {"action": "begin", "project_path": str(project),
                                              "request_id": "first"})
    second = full_host.call_tool("UnifiedTask", {"action": "begin", "project_path": str(project),
                                               "request_id": "conflict"})
    assert second["structuredContent"]["error"]["code"] == "PROJECT_BUSY"
    token = first["structuredContent"]["workflow_id"]
    full_host.call_tool("UnifiedTask", {"action": "end", "workflow_id": token})
    assert project == Path(full_host.config.workspace_root) / "app"
