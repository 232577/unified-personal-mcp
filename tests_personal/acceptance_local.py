"""Opt-in real HTTP acceptance. Uses only controlled project fixtures; never a live tunnel.

Run: python -m tests_personal.acceptance_local --output verification/live-01
Requires the fixture EXE built by scripts/build-fixture.ps1.
"""
import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
import traceback
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import psutil
from jsonschema import Draft202012Validator

from bf_automation.runtime import application_environment
from personal_mcp.config import load_config
from personal_mcp.service import LocalService
from personal_mcp.windows_jobs import OwnedJob
from tests_personal.web_fixture import U4AFixtureServer

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class Client:
    def __init__(self, service, output):
        self.url = f"http://127.0.0.1:{service.config.port}/mcp"
        self.key = service.runtime.auth_token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.output, self.sequence = output, 0
        self.tools = {t["name"]: t for t in self.rpc("tools/list", {})["tools"]}
        assert len(self.tools) == 50

    def rpc(self, method, params):
        self.sequence += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.url, body, {"Content-Type": "application/json",
            "Authorization": "Bearer " + self.key})
        with self.opener.open(req, timeout=190) as response:
            value = json.load(response)
        assert "error" not in value, value
        return value["result"]

    def call(self, name, args=None, *, allow_error=False, **kwargs):
        args = {**(args or {}), **kwargs}
        Draft202012Validator(self.tools[name]["inputSchema"]).validate(args)
        result = self.rpc("tools/call", {"name": name, "arguments": args})
        data = result.get("structuredContent", {})
        if not result.get("isError"):
            Draft202012Validator(self.tools[name]["outputSchema"]).validate(data)
        # Private evidence may contain opaque workflow IDs and paths.
        dump(self.output / f"call-{self.sequence:04}-{name}.json",
             {k: v for k, v in result.items() if k != "content"})
        assert allow_error or not result.get("isError"), (name, data, result.get("content"))
        return data

    def op(self, name, args=None, **kwargs):
        data = self.call(name, args, **kwargs)["operation"]
        assert data["state"] == "completed", data
        return data["result"]


def begin(client, project):
    token = client.call("UnifiedTask", action="begin", project_path=project.name,
                        request_id=uuid.uuid4().hex)["workflow_id"]
    client.call("UnifiedTask", action="activate", workflow_id=token)
    return token


def target(client, common, test_id):
    snap = client.call("BrowserSnapshot", common, max_elements=160)["snapshot"]
    elements = [e for e in snap["elements"] if e.get("test_id") == test_id]
    assert len(elements) == 1, (test_id, snap)
    return {"snapshot_version": snap["snapshot_version"], "element_id": elements[0]["element_id"]}


def action(client, common, test_id, kind, **kwargs):
    args = {**common, **target(client, common, test_id), "action": kind,
            "request_id": uuid.uuid4().hex, **kwargs}
    result = client.op("BrowserAction", args)
    # Identical retry reads the durable result without dispatching another click.
    assert client.op("BrowserAction", args) == result
    return result


def wait_text(client, common, test_id, expected):
    data = client.call("BrowserWaitFor", common, test_id=test_id,
        condition="text_equals", expected=expected, timeout_ms=5000)
    assert data["observation"]["matched"], data


def capture(client, common, path, window=None, **kwargs):
    name, key = ("WindowScreenshot", "window_capture") if window else ("BrowserScreenshot", "capture")
    args = {"workflow_id": common["workflow_id"], "window_id": window} if window else common
    data = client.call(name, args, **kwargs)[key]
    raw = Path(data["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == data["sha256"]
    path.write_bytes(raw)
    return {k: data.get(k) for k in ("sha256", "capture_mode", "staging_used", "window_status",
                                    "foreground_restored", "placement_restored")}


def profile(project, fixture, server, *, attached=False, web=False):
    apps = project / ".bf/apps"
    apps.mkdir(parents=True)
    (project / "fixture-inputs").mkdir()
    (project / "fixture-inputs/upload.txt").write_text("独立附件 中文验证\n", encoding="utf-8")
    if web:
        instance = "bfh_" + uuid.uuid4().hex
        row = {"version": 1, "id": "fixture", "kind": "web", "project_root": "../..",
            "entry_url": server.origin + "/page.html?instance=" + instance,
            "navigation_origins": [server.origin], "resource_origins": [server.origin],
            "transfer": {"upload_roots": ["fixture-inputs"]}}
    else:
        instance = None
        (project / "build").mkdir()
        shutil.copy2(fixture, project / "build/fixture.exe")
        row = {"version": 1, "id": "fixture", "kind": "hybrid-webview2", "adapter": "hybrid-webview2",
            "project_root": "../..", "launcher": {"executable": "../../build/fixture.exe",
            "args": ["--url", server.origin + "/page.html"], "cwd": "../..", "reuse_existing": attached},
            "window": {"title_contains": "U4A WebView2 Fixture"}, "hybrid": {
            "engine": "webview2", "ownership": "attached" if attached else "managed",
            "debug_mode": "existing-proven" if attached else "managed-loopback", "max_instances": 2},
            "web": {"navigation_origins": [server.origin], "resource_origins": [server.origin],
                    "upload_roots": ["fixture-inputs"]}}
    dump(apps / "fixture.json", row)
    return instance


def open_session(client, token, url=None):
    session = client.op("BrowserSession", workflow_id=token, action="start",
                        application_id="fixture", request_id=uuid.uuid4().hex)["session"]
    pages = client.call("BrowserPages", workflow_id=token, session_id=session["session_id"], action="list")["pages"]
    if not pages and url:
        client.op("BrowserPages", workflow_id=token, session_id=session["session_id"],
                  action="new", request_id=uuid.uuid4().hex)
        pages = client.call("BrowserPages", workflow_id=token, session_id=session["session_id"], action="list")["pages"]
    assert len(pages) == 1
    common = {"workflow_id": token, "session_id": session["session_id"], "page_id": pages[0]["page_id"]}
    if url:
        client.op("BrowserNavigate", common, url=url, request_id=uuid.uuid4().hex)
    return session, common


def exercise(client, common, session, project, instance, output, tag):
    name = "记录-" + tag
    action(client, common, "record-name", "fill", text=name)
    action(client, common, "category", "select", values=["ops"])
    action(client, common, "confirmed", "check", checked=True)
    action(client, common, "save", "click")
    wait_text(client, common, "result", f"saved:{instance}:{name}:ops:true")
    wait_text(client, common, "cookie-result", f"cookie:u4a_instance={instance}")
    wait_text(client, common, "storage-result", f"storage:{instance}")
    upload = project / "fixture-inputs/upload.txt"
    uploaded = client.op("BrowserUpload", common, **target(client, common, "attachment"),
        source_path="fixture-inputs/upload.txt", request_id=uuid.uuid4().hex)
    assert uploaded["verified"] and uploaded["sha256"] == hashlib.sha256(upload.read_bytes()).hexdigest()
    action(client, common, "upload-submit", "click")
    wait_text(client, common, "upload-result", f"uploaded:{upload.name}:{upload.stat().st_size}")
    downloaded = client.op("BrowserDownload", common, **target(client, common, "download"),
        request_id=uuid.uuid4().hex, timeout_ms=10000)
    expected = f"U4A report {instance}\n".encode()
    assert downloaded["verified"] and Path(downloaded["path"]).read_bytes() == expected
    assert downloaded["sha256"] == hashlib.sha256(expected).hexdigest()
    return {"upload": uploaded["sha256"], "download": downloaded["sha256"],
        "browser": capture(client, common, output / (tag + "-browser.png"))}


def native_export(client, common, session, output, tag):
    action(client, common, "export", "click")
    snap = client.call("WindowSnapshot", workflow_id=common["workflow_id"],
                      window_id=session["shell_window_id"], include_image=False, max_elements=40)["window_snapshot"]
    deadline = time.monotonic() + 12
    button, dialog = None, None
    while time.monotonic() < deadline:
        windows = client.call("WindowInventory", workflow_id=common["workflow_id"],
                              process_id=snap["pid"], include_hidden=False)["windows"]
        dialogs = [w for w in windows if w["class_name"] == "#32770"]
        if len(dialogs) == 1:
            dialog = dialogs[0]
            state = client.call("WindowSnapshot", workflow_id=common["workflow_id"],
                window_id=dialog["window_id"], include_image=False, max_elements=120)["window_snapshot"]
            buttons = [e for e in state["elements"] if "button" in e["control_type"].lower()
                       and e["name"].strip().lower() in {"save", "save(s)", "保存", "保存(s)", "保存(&s)"}]
            if len(buttons) == 1:
                button = buttons[0]
                break
        time.sleep(.15)
    assert button, "Native Save As was not ready"
    evidence = capture(client, common, output / (tag + "-dialog.png"), window=dialog["window_id"],
                       allow_staging=True, include_frame=False)
    controlled = client.call("WindowControl", workflow_id=common["workflow_id"],
        window_id=dialog["window_id"], action="invoke", element_id=button["element_id"],
        allow_foreground_fallback=False, request_id=uuid.uuid4().hex)
    assert controlled["accepted"]
    filename = "u4a-" + session["hybrid_instance_id"][-6:] + ".txt"
    wait_text(client, common, "export-result", "exported:" + filename)
    exported = (output / "private/bf/tasks" / session["task_key"] / "hybrid"
                / session["hybrid_instance_id"] / "exports" / filename)
    assert exported.read_text(encoding="utf-8") == f"U4A export {session['hybrid_instance_id']}\n"
    evidence["export_sha256"] = hashlib.sha256(exported.read_bytes()).hexdigest()
    return evidence


def window_states(client, sessions, service, output, round_no):
    import win32con
    import win32gui
    instances = service.runtime.bf_server._bf_hybrid_manager.instances
    a = instances[sessions[0][0]["hybrid_instance_id"]]
    b = instances[sessions[1][0]["hybrid_instance_id"]]
    places = {item.shell_hwnd: win32gui.GetWindowPlacement(item.shell_hwnd) for item in (a, b)}
    topmost = {item.shell_hwnd: bool(win32gui.GetWindowLong(item.shell_hwnd, win32con.GWL_EXSTYLE)
                                   & win32con.WS_EX_TOPMOST) for item in (a, b)}
    before = win32gui.GetForegroundWindow()
    common = sessions[0][1]
    try:
        for item in (a, b):
            win32gui.SetWindowPos(item.shell_hwnd, win32con.HWND_TOPMOST, 20, 20, 980, 720,
                                 win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)
        time.sleep(.3)
        hit = win32gui.WindowFromPoint((400, 300))
        observed = win32gui.GetAncestor(hit, win32con.GA_ROOT)
        dump(output / f"occlusion-precondition-{round_no}.json", {"hit": hit, "root": observed,
            "expected": b.shell_hwnd, "hit_class": win32gui.GetClassName(hit),
            "windows": [{"hwnd": item.shell_hwnd, "rect": win32gui.GetWindowRect(item.shell_hwnd),
                "enabled": bool(win32gui.IsWindowEnabled(item.shell_hwnd)),
                "visible": bool(win32gui.IsWindowVisible(item.shell_hwnd)),
                "minimized": bool(win32gui.IsIconic(item.shell_hwnd))} for item in (a, b)]})
        assert observed and observed != a.shell_hwnd, "the target point must be covered by another window"
        occluded = capture(client, common, output / f"occluded-{round_no}.png",
                           window=a.shell_window_id, allow_staging=True, include_frame=False)
        client.call("BrowserSnapshot", common)
        win32gui.ShowWindow(a.shell_hwnd, win32con.SW_SHOWMINNOACTIVE)
        assert win32gui.IsIconic(a.shell_hwnd)
        data = client.call("WindowScreenshot", workflow_id=common["workflow_id"], window_id=a.shell_window_id,
                           allow_staging=True, include_frame=False, allow_error=True)
        minimized = {"capture_returned": "window_capture" in data,
                     "still_minimized": bool(win32gui.IsIconic(a.shell_hwnd)),
                     "freshness": "not asserted for a staged minimized capture"}
        if "window_capture" in data:
            metadata = data["window_capture"]
            raw = Path(metadata["path"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == metadata["sha256"]
            (output / f"minimized-{round_no}.png").write_bytes(raw)
            minimized.update({k: metadata.get(k) for k in ("capture_mode", "staging_used", "window_status",
                "foreground_restored", "placement_restored")})
        client.call("BrowserSnapshot", common)
        assert minimized["still_minimized"]
        return {"occluded": occluded, "minimized": minimized,
                "foreground_preserved": win32gui.GetForegroundWindow() == before}
    finally:
        for hwnd, placement in places.items():
            win32gui.SetWindowPlacement(hwnd, placement)
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST if topmost[hwnd] else win32con.HWND_NOTOPMOST,
                                 0, 0, 0, 0, win32con.SWP_NOACTIVATE | win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)


def close(client, common):
    client.op("BrowserSession", workflow_id=common["workflow_id"], session_id=common["session_id"],
              action="close", request_id=uuid.uuid4().hex)
    assert client.call("UnifiedTask", workflow_id=common["workflow_id"], action="end")["state"] == "ENDED"


def wait_ready(path, process):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert process.poll() is None, "External fixture exited"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(.15)
    raise TimeoutError("External fixture readiness")


def run(output, fixture):
    output = output.resolve()
    assert output.is_relative_to(ROOT / "verification"), "Evidence must stay inside this project's verification directory"
    output.mkdir(parents=True, exist_ok=False)
    projects = output / "projects"
    projects.mkdir()
    settings = output / "settings.local.json"
    dump(settings, {"schema_version": 1, "workspace_root": "projects", "data_root": "private",
        "host": "127.0.0.1", "port": free_port(), "permission_mode": "trusted",
        "tunnel": {"id": "tunnel_" + "0" * 32, "key_file": "private/unused.key"}})
    service = LocalService(load_config(settings), assets_root=ROOT / "resources")
    evidence = {"status": "RUNNING", "transport": "authenticated local HTTP", "external_client": "NOT_RUN",
                "u3": [], "u4a": [], "attached": None}
    try:
        service.start()
        manager = service.runtime.bf_server._bf_browser_manager
        original_once = manager._once

        def diagnostic_once(owner, request_id, operation, args, execute):
            def observed_execute():
                try:
                    return execute()
                except Exception:
                    traceback.print_exc()
                    raise
            return original_once(owner, request_id, operation, args, observed_execute)

        manager._once = diagnostic_once
        client = Client(service, output)
        with U4AFixtureServer(port=0) as server:
            sessions = []
            for i in range(4):
                project = projects / f"web-{i}"
                project.mkdir()
                instance = profile(project, fixture, server, web=True)
                token = begin(client, project)
                session, common = open_session(client, token, server.origin + "/page.html?instance=" + instance)
                sessions.append((session, common, project, instance))
            assert len({s["worker_pid"] for s, *_ in sessions}) == 4
            for i, (session, common, project, instance) in enumerate(sessions):
                evidence["u3"].append(exercise(client, common, session, project, instance, output, f"web-{i}"))
            first, second = sessions[:2]
            denied = client.call("BrowserSnapshot", first[1], workflow_id=second[1]["workflow_id"], allow_error=True)
            assert denied.get("error") or "snapshot" not in denied
            close(client, first[1])
            client.call("BrowserSnapshot", second[1])
            for _, common, *_ in sessions[1:]:
                close(client, common)
            assert not service.runtime.bf_server._bf_browser_manager.sessions

            for round_no in range(3):
                sessions = []
                for i in range(2):
                    project = projects / f"hybrid-{round_no}-{i}"
                    project.mkdir()
                    profile(project, fixture, server)
                    token = begin(client, project)
                    session, common = open_session(client, token)
                    assert session["engine"] == "webview2" and session["ownership"] == "managed"
                    sessions.append((session, common, project))
                assert sessions[0][0]["hybrid_instance_id"] != sessions[1][0]["hybrid_instance_id"]
                for i, (session, common, project) in enumerate(sessions):
                    tag = f"hybrid-{round_no}-{i}"
                    checked = exercise(client, common, session, project, session["hybrid_instance_id"], output, tag)
                    checked["shell"] = capture(client, common, output / (tag + "-shell.png"),
                        window=session["shell_window_id"], allow_staging=True, include_frame=False)
                    checked["dialog"] = native_export(client, common, session, output, tag)
                    evidence["u4a"].append(checked)
                    dump(output / "summary.json", evidence)
                evidence.setdefault("window_states", []).append(window_states(client, sessions, service, output, round_no))
                close(client, sessions[0][1])
                client.call("BrowserSnapshot", sessions[1][1])
                client.call("WindowSnapshot", workflow_id=sessions[1][1]["workflow_id"],
                            window_id=sessions[1][0]["shell_window_id"], include_image=False)
                close(client, sessions[1][1])
                assert not service.runtime.bf_server._bf_hybrid_manager.instances

            project = projects / "attached"
            project.mkdir()
            profile(project, fixture, server, attached=True)
            external = output / "external"
            user_data = external / "user-data"
            user_data.mkdir(parents=True)
            ready = external / "ready.json"
            env = application_environment(os.environ)
            instance = "bfh_" + uuid.uuid4().hex
            env.update(BF_HYBRID_INSTANCE=instance, WEBVIEW2_USER_DATA_FOLDER=str(user_data),
                BF_HYBRID_READY_FILE=str(ready),
                WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f"--remote-debugging-port={free_port()}")
            job = OwnedJob()
            try:
                proc = job.spawn([str(project / "build/fixture.exe"), "--url", server.origin + "/page.html"],
                    cwd=project, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                row = wait_ready(ready, proc)
                identities = [(pid, psutil.Process(pid).create_time()) for pid in (proc.pid, row["shell_pid"], row["webview_pid"])]
                token = begin(client, project)
                session, common = open_session(client, token)
                assert session["ownership"] == "attached"
                client.call("BrowserSnapshot", common)
                close(client, common)
                assert all(psutil.Process(pid).create_time() == created for pid, created in identities)
                evidence["attached"] = {"after_close_and_end": "original processes survived",
                    "resolver": "default live descendant/listener proof"}
            finally:
                job.close()
        evidence["status"] = "PASS"
    except BaseException as exc:
        evidence["status"], evidence["failure_type"] = "FAIL", type(exc).__name__
        raise
    finally:
        try:
            service.stop()
        finally:
            dump(output / "summary.json", evidence)
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, default=ROOT / "verification/fixture-bin/UPM-WebView2-Fixture.exe")
    args = parser.parse_args()
    run(args.output, args.fixture.resolve(strict=True))
