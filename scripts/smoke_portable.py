"""Opt-in smoke using the bundled interpreter after moving the whole program directory."""
import argparse
import http.server
import json
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--bundle", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
bundle, output = args.bundle.resolve(strict=True), args.output.resolve()
assert Path(sys.executable).resolve() == bundle / "resources/python/python.exe"
output.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(bundle / "app"))

from personal_mcp.config import load_config  # noqa: E402
from personal_mcp.service import LocalService  # noqa: E402

projects = output / "projects"
project = projects / "smoke"
project.mkdir(parents=True)
with socket.socket() as port:
    port.bind(("127.0.0.1", 0))
    number = port.getsockname()[1]
config_file = output / "settings.local.json"
config_file.write_text(json.dumps({"schema_version": 1, "workspace_root": "projects",
    "data_root": "private", "host": "127.0.0.1", "port": number, "permission_mode": "trusted",
    "tunnel": {"id": "tunnel_" + "0" * 32, "key_file": "private/unused.key"}}), encoding="utf-8")
service = LocalService(load_config(config_file), assets_root=bundle / "resources")
report = {"status": "RUNNING", "relocated": True, "tunnel": "not started"}
try:
    service.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def raw_call(name, **arguments):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": name, "arguments": arguments}}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{number}/mcp", body,
            {"Authorization": "Bearer " + service.runtime.auth_token, "Content-Type": "application/json"})
        with opener.open(request, timeout=40) as response:
            result = json.load(response)["result"]
        return result

    def call(name, **arguments):
        result = raw_call(name, **arguments)
        assert not result.get("isError"), result
        return result["structuredContent"]

    token = call("UnifiedTask", action="begin", project_path="smoke", request_id="begin")["workflow_id"]
    call("UnifiedTask", action="activate", workflow_id=token)
    code = "from pathlib import Path;p=Path('counter.txt');p.write_text(str(int(p.read_text())+1) if p.exists() else '1')"
    command = '"' + str(sys.executable) + '" -B -c "' + code + '"'
    params = {"workflow_id": token, "request_id": "build-once", "cmd": command, "workdir": ".",
              "yield_time_ms": 1000, "timeout_ms": 10000}
    first = call("exec_command", **params)
    assert call("exec_command", **params) == first
    assert first["exit_code"] == 0 and (project / "counter.txt").read_text() == "1"
    status = call('OperationStatus', workflow_id=token, request_id='build-once')
    assert status['state'] == 'completed' and status['result']['structuredContent'] == first
    listed = call('UnifiedTask', action='list')['workflows'][0]
    resume = {'action': 'resume', 'workflow_ref': listed['workflow_ref'],
              'request_id': 'resume-once', 'expected_generation': listed['credential_generation']}
    recovered = call('UnifiedTask', **resume)
    assert call('UnifiedTask', **resume) == recovered
    denied = raw_call('UnifiedTask', action='status', workflow_id=token)
    assert denied['structuredContent']['error']['code'] == 'WORKFLOW_CREDENTIAL_REPLACED'
    token = recovered['workflow_id']
    assert call('OperationStatus', workflow_id=token, request_id='build-once')['result']['structuredContent'] == first
    info = call('server_info')
    expected_version = json.loads((bundle / 'package-metadata.json').read_text(encoding='utf-8'))['version']
    assert info['version'] == expected_version and len(info['catalog_revision']) == 64
    assert info['health']['bf']['status'] == 'healthy'
    processes = call('UnifiedTask', action='status', workflow_id=token)['job_processes']
    assert processes['status'] == 'available' and processes['active_process_count'] == 0
    assert info['resource_usage']['coding_job_active_processes'] == 0
    (project / "note.txt").write_text("unified 中文 search", encoding="utf-8")
    search = call("SearchSession", workflow_id=token, action="start", pattern="unified")
    deadline = time.monotonic() + 5
    while True:
        found = call("SearchSession", workflow_id=token, action="read", search_id=search["search_id"])
        if found["state"] != "running":
            break
        assert time.monotonic() < deadline
        time.sleep(.05)
    assert found["state"] == "completed" and found["results"][0]["path"] == "note.txt"
    assert Path(service.runtime.searches.rg).resolve() == bundle / "resources/bin/rg.exe"

    class Page(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = b"<title>Portable fixture</title><h1 data-testid='ready'>portable-ready</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    thread = threading.Thread(target=site.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{site.server_port}"
        apps = project / ".bf/apps"
        apps.mkdir(parents=True)
        (apps / "web.json").write_text(json.dumps({"version": 1, "id": "web", "kind": "web",
            "project_root": "../..", "entry_url": origin, "navigation_origins": [origin],
            "resource_origins": [origin]}), encoding="utf-8")
        started = call("BrowserSession", workflow_id=token, action="start",
                       application_id="web", request_id="browser")
        assert started["operation"]["state"] == "completed", started
        session = started["operation"]["result"]["session"]["session_id"]
        page = call("BrowserPages", workflow_id=token, session_id=session, action="new",
                    request_id="page")["operation"]["result"]["page_id"]
        common = {"workflow_id": token, "session_id": session, "page_id": page}
        navigated = call("BrowserNavigate", **common, request_id="navigate", url=origin)
        assert navigated["operation"]["state"] == "completed"
        observed = call("BrowserWaitFor", **common, test_id="ready",
                        condition="text_equals", expected="portable-ready", timeout_ms=5000)
        assert observed["observation"]["matched"]
        shot = call("BrowserScreenshot", **common)["capture"]
        report["browser_sha256"] = shot["sha256"]
    finally:
        site.shutdown()
        site.server_close()
        thread.join(3)
    assert call("UnifiedTask", workflow_id=token, action="end")["state"] == "ENDED"
    assert not service.runtime.searches.sessions
    assert not service.runtime.bf_server._bf_browser_manager.sessions
    report.update(status="PASS", version=info['version'], tools=service.status()["tools"],
                  command_exactly_once=True, search=True, browser=True, credential_resume=True,
                  operation_status=True, job_process_diagnostics=True,
                  catalog_revision=info['catalog_revision'])
finally:
    service.stop()
    (output / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report))
