import json
import sys
import threading
import urllib.error
import urllib.request

import pytest

from coding_tools_mcp.server import MCPHandler, RuntimeHTTPServer, TOOL_REGISTRY
from personal_mcp.bf.bootstrap import build_mcp
from personal_mcp.config import load_config
from personal_mcp.host import UnifiedRuntime
from tests_personal.test_config import installation


@pytest.fixture
def host(tmp_path):
    cfg = load_config(installation(tmp_path))
    (cfg.workspace_root / "app" / "note.txt").write_text("hello unified", encoding="utf-8")
    browser = cfg.data_root / "browser.json"
    cfg.data_root.mkdir()
    browsers = cfg.data_root / "browsers"
    browsers.mkdir()
    browser.write_text(json.dumps({"enabled": True, "version": 1, "mode": "headless",
        "max_sessions": 4, "max_pages_per_session": 4, "python": sys.executable,
        "browsers_path": str(browsers), "actions_enabled": True, "transfers_enabled": True}), encoding="utf-8")
    bf = build_mcp(state_root=cfg.data_root / "bf", allowed_root=cfg.workspace_root,
                   apps_dir=cfg.data_root / "apps", browser_config_path=browser)
    runtime = UnifiedRuntime(cfg, auth_token="fixture-backend-" + "x" * 32, bf_server=bf)
    yield runtime
    runtime.close()


def call(host, name, **args):
    return host.call_tool(name, args)


def workflow(host, access="write"):
    result = call(host, "UnifiedTask", action="begin", project_path="app", request_id="begin", access=access)
    token = result["structuredContent"]["workflow_id"]
    active = call(host, "UnifiedTask", action="activate", workflow_id=token)
    assert not active["isError"], active
    return token


def test_exact_catalog_and_no_global_registry_change(host):
    tools = host.list_tools()["tools"]
    assert len(tools) == 50 and len(TOOL_REGISTRY) == 18
    assert "task_context" not in {t["name"] for t in tools}
    for tool in tools:
        assert tool["outputSchema"]
        if tool["name"] not in {"UnifiedTask", "server_info"}:
            assert "workflow_id" in tool["inputSchema"]["required"]
        assert "bf_task_id" not in tool["inputSchema"].get("properties", {})


def test_search_release_allows_repeated_search_without_ending_workflow(host):
    token = workflow(host)
    for _ in range(5):
        started = call(host, "SearchSession", action="start", workflow_id=token, pattern="hello")
        assert not started["isError"], started
        sid = started["structuredContent"]["search_id"]
        released = call(host, "SearchSession", action="release", workflow_id=token, search_id=sid)
        assert not released["isError"], released
        assert released["structuredContent"]["state"] == "released"
        assert call(host, "SearchSession", action="read", workflow_id=token, search_id=sid)["isError"]
    assert call(host, "read_file", workflow_id=token, path="note.txt")["structuredContent"]["content"] == "hello unified"


def test_file_and_bf_calls_route_through_one_workflow(host):
    token = workflow(host)
    result = call(host, "read_file", workflow_id=token, path="note.txt")
    assert result["structuredContent"]["content"] == "hello unified"
    waited = call(host, "Wait", workflow_id=token, duration=0)
    assert not waited["isError"], waited
    ended = call(host, "UnifiedTask", action="end", workflow_id=token)
    assert ended["structuredContent"]["state"] == "ENDED"
    assert call(host, "read_file", workflow_id=token, path="note.txt")["isError"]


@pytest.mark.parametrize("extra", [{}, {"workflow_id": "wf_invalid"}, {"principal": "owner"},
                                    {"bf_task_id": "bf_" + "a" * 32}, {"endpoint": "http://127.0.0.1:1"}])
def test_missing_or_injected_authority_is_rejected(host, extra):
    assert call(host, "exec_command", cmd="echo should-not-run", **extra)["isError"]
    assert not host.registry.resources


def test_read_workflow_cannot_execute_or_modify_desktop(host):
    token = workflow(host, access="read")
    assert call(host, "exec_command", workflow_id=token, cmd="echo blocked")["isError"]
    assert call(host, "Click", workflow_id=token, loc=[1, 1])["isError"]
    assert not call(host, "read_file", workflow_id=token, path="note.txt")["isError"]


def test_http_authentication_happens_before_dispatch(host):
    server = RuntimeHTTPServer(("127.0.0.1", 0), MCPHandler, host)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request_body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
    url = f"http://127.0.0.1:{server.server_port}/mcp"
    try:
        for credential in (None, "wrong"):
            headers = {"Content-Type": "application/json", "X-Owner": "local-owner"}
            if credential:
                headers["Authorization"] = "Bearer " + credential
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url, request_body, headers))
            assert error.value.code == 401
        headers["Authorization"] = "Bearer " + host.auth_token
        with urllib.request.urlopen(urllib.request.Request(url, request_body, headers)) as response:
            result = json.load(response)
        assert len(result["result"]["tools"]) == 50
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
