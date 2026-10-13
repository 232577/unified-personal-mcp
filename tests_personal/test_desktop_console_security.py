"""独立复核：只用隔离状态与假桌面，验证授权撤销和私密输出边界。"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from personal_mcp.desktop_console import DesktopConsoleManager, resource_read, RESOURCE_URI
from personal_mcp.operations import OperationJournal
from personal_mcp.workflows import WorkflowError
from tests_personal.test_host import host as host, call as host_call, workflow


class Registry:
    def __init__(self, resource):
        self.resource = resource
        self.revoked = False
        self.on_write_enter = None

    @contextmanager
    def use(self, principal, workflow, *, write=False, control=False, touch=True):
        if self.revoked:
            raise WorkflowError("WORKFLOW_CREDENTIAL_REPLACED")
        if write and self.on_write_enter:
            self.on_write_enter()
        yield self.resource


class Surface:
    def __init__(self):
        self.actions = []

    def inventory(self, resource):
        return {"displays": [], "windows": []}

    def capture(self, resource, target):
        return {"data_url": "data:image/jpeg;base64,private-frame-marker", "width": 100, "height": 100,
                "geometry_verified": True, "target": target, "native_state": {"owner": resource.key}}

    def clipboard_read(self, resource):
        return "private-clipboard-marker"

    def clipboard_write(self, resource, text, *, authorize=None):
        if authorize:
            authorize()
        self.actions.append("clipboard_write")
        return {"ok": True}

    def act(self, resource, frame, args):
        self.actions.append(args["action"])
        return {"ok": True, "accepted": True}


@pytest.fixture
def fixture(tmp_path):
    clock = [0.0]
    resource = SimpleNamespace(key="fixture-owner", bf_token="private-bf-token")
    registry = Registry(resource)
    host = SimpleNamespace(config=SimpleNamespace(data_root=tmp_path, device_label="fixture"),
        principal=object(), registry=registry, bf=SimpleNamespace(drain=lambda *args, **kwargs: None),
        bf_server=SimpleNamespace(), operations=OperationJournal(tmp_path / "receipts.sqlite"))
    manager = DesktopConsoleManager(host, clock=lambda: clock[0])
    manager.native = Surface()
    opened = manager.open("private-workflow-token")
    secret = opened["_meta"]["desktop"]["ui_session"]
    context = SimpleNamespace(ui_session=secret)

    def call(name, **args):
        return manager.handle(name, args, context)

    return SimpleNamespace(clock=clock, manager=manager, host=host, registry=registry, context=context,
        call=call, opened=opened, secret=secret, session=manager.sessions[manager._hash(secret)])


def test_console_secrets_and_image_stay_out_of_public_output_and_journal(fixture):
    frame = fixture.call("DesktopView", action="frame", target={"kind": "display", "id": "screen"})
    clipboard = fixture.call("DesktopView", action="clipboard")
    fixture.call("DesktopAction", action="arm", request_id="arm")
    outputs = [fixture.opened, frame, clipboard]
    for output in outputs:
        visible = json.dumps({key: value for key, value in output.items() if key != "_meta"})
        assert fixture.secret not in visible
        assert "private-workflow-token" not in visible
        assert "private-frame-marker" not in visible
        assert "private-clipboard-marker" not in visible
    database = fixture.host.operations.path.read_bytes()
    assert fixture.secret.encode() not in database
    assert b"private-frame-marker" not in database
    assert b"private-clipboard-marker" not in database


def test_readonly_polling_does_not_extend_arm_lease(fixture):
    fixture.call("DesktopAction", action="arm", request_id="arm")
    fixture.clock[0] = 29
    fixture.call("DesktopView", action="inventory")
    assert fixture.session.arm_expires == 30
    fixture.clock[0] = 31
    result = fixture.call("DesktopAction", action="clipboard_write", text="fixture", request_id="late")
    assert result["isError"] and result["structuredContent"]["error"]["code"] == "DESKTOP_NOT_ARMED"
    assert fixture.manager.native.actions == []


def test_arm_expiry_is_rechecked_after_workflow_write_admission_wait(fixture):
    fixture.call("DesktopAction", action="arm", request_id="arm")
    fixture.clock[0] = 29
    fixture.registry.on_write_enter = lambda: fixture.clock.__setitem__(0, 31)
    result = fixture.call("DesktopAction", action="clipboard_write", text="fixture", request_id="queued")
    assert result["isError"]
    assert fixture.manager.native.actions == []


def test_disarm_cannot_be_undone_by_an_already_admitted_arm_request(fixture):
    entered, release = threading.Event(), threading.Event()
    original = fixture.host.operations.run

    def journal(owner, request_id, name, arguments, execute, **kwargs):
        if request_id != "arm-race":
            return original(owner, request_id, name, arguments, execute, **kwargs)

        def pause_after_admission():
            entered.set()
            assert release.wait(3)
            return execute()

        return original(owner, request_id, name, arguments, pause_after_admission, **kwargs)

    fixture.host.operations.run = journal
    with ThreadPoolExecutor(1) as pool:
        arm = pool.submit(fixture.call, "DesktopAction", action="arm", request_id="arm-race")
        try:
            assert entered.wait(3)
            stopped = fixture.call("DesktopAction", action="disarm", request_id="stop")
            assert stopped["structuredContent"]["armed"] is False
        finally:
            release.set()
        arm.result(3)
    assert fixture.session.armed is False


def test_disarm_does_not_wait_for_inflight_frame_lock(fixture):
    entered, release = threading.Event(), threading.Event()
    original = fixture.manager.native.capture

    def capture(resource, target):
        entered.set()
        assert release.wait(3)
        return original(resource, target)

    fixture.manager.native.capture = capture
    with ThreadPoolExecutor(1) as pool:
        frame = pool.submit(fixture.call, "DesktopView", action="frame", target={"kind": "display", "id": "screen"})
        try:
            assert entered.wait(3)
            stopped = fixture.call("DesktopAction", action="disarm", request_id="stop")
            assert stopped["structuredContent"]["armed"] is False
            assert not frame.done()
        finally:
            release.set()
        frame.result(3)


def test_disarm_blocks_native_dispatch_after_native_lease_wait(fixture):
    entered, release = threading.Event(), threading.Event()
    fixture.call("DesktopAction", action="arm", request_id="arm")
    frame = fixture.call("DesktopView", action="frame", target={"kind": "display", "id": "screen"})
    calls = []

    def native(resource, captured, args):
        entered.set()
        assert release.wait(3)
        args["_authorize"]()
        calls.append("dispatch")
        return {"ok": True}

    fixture.manager.native.act = native
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(fixture.call, "DesktopAction", action="click", x=3, y=4,
            frame_id=frame["structuredContent"]["frame_id"], request_id="queued-native")
        try:
            assert entered.wait(3)
            fixture.call("DesktopAction", action="disarm", request_id="stop")
        finally:
            release.set()
        assert pending.result(3)["isError"]
    assert calls == [] and fixture.session.armed is False


def test_explicit_unknown_native_result_keeps_unknown_receipt_and_never_replays(fixture):
    fixture.call("DesktopAction", action="arm", request_id="arm")
    frame = fixture.call("DesktopView", action="frame", target={"kind": "display", "id": "screen"})
    calls = []

    def native(*args):
        calls.append("once")
        return {"ok": False, "accepted": True, "error": {"code": "NATIVE_INPUT_OUTCOME_UNKNOWN", "outcome": "unknown"}}

    fixture.manager.native.act = native
    arguments = {"action": "click", "x": 3, "y": 4, "frame_id": frame["structuredContent"]["frame_id"], "request_id": "uncertain-result"}
    assert fixture.call("DesktopAction", **arguments)["isError"]
    receipt = fixture.host.operations.status("fixture-owner", "uncertain-result")
    assert receipt["state"] == "unknown"
    assert receipt["result"]["structuredContent"]["error"]["outcome"] == "unknown"
    assert fixture.call("DesktopAction", **arguments)["isError"]
    assert calls == ["once"]


def test_unknown_native_action_is_not_replayed_for_original_request(fixture):
    fixture.call("DesktopAction", action="arm", request_id="arm")
    frame = fixture.call("DesktopView", action="frame", target={"kind": "display", "id": "screen"})
    calls = []

    def uncertain(*args):
        calls.append("once")
        raise RuntimeError("OUTCOME_UNKNOWN")

    fixture.manager.native.act = uncertain
    arguments = {"action": "click", "x": 3, "y": 4, "frame_id": frame["structuredContent"]["frame_id"], "request_id": "click"}
    assert fixture.call("DesktopAction", **arguments)["isError"]
    assert fixture.call("DesktopAction", **arguments)["isError"]
    assert calls == ["once"]


def test_revoked_workflow_refuses_and_closes_ui_session(fixture):
    fixture.registry.revoked = True
    with pytest.raises(WorkflowError):
        fixture.call("DesktopView", action="inventory")
    assert fixture.session.closed and not fixture.session.armed


def test_frames_have_bounded_cache_and_expire_before_action(fixture):
    fixture.call("DesktopAction", action="arm", request_id="arm")
    frame = None
    for _ in range(4):
        frame = fixture.call("DesktopView", action="frame", target={"kind": "display", "id": "screen"})
    assert len(fixture.session.frames) == 2
    fixture.clock[0] = 9
    result = fixture.call("DesktopAction", action="click", x=3, y=4,
                          frame_id=frame["structuredContent"]["frame_id"], request_id="stale")
    assert result["structuredContent"]["error"]["code"] == "DESKTOP_FRAME_STALE"
    assert fixture.manager.native.actions == []


def test_ui_resource_is_inlined_with_no_network_permissions():
    content = resource_read(RESOURCE_URI)["contents"][0]
    assert 'src="desktop.js"' not in content["text"] and 'href="desktop.css"' not in content["text"]
    assert content["_meta"]["ui"]["csp"] == {"connectDomains": [], "resourceDomains": []}


def test_rpc_capability_is_private_request_context_not_tool_arguments():
    from coding_tools_mcp.protocol import dispatch_rpc

    seen = []
    secret = "isolated-ui-capability-" + "x" * 32

    def invoke(name, arguments, *, context):
        seen.append((arguments, context))
        return {"content": [], "structuredContent": {"ok": True}, "isError": False}

    runtime = SimpleNamespace(call_tool=invoke, telemetry=SimpleNamespace(record_request=lambda *args: None),
                              server_identity=lambda: {"name": "fixture", "version": "0"})
    reply = dispatch_rpc(runtime, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "DesktopView", "arguments": {"action": "inventory"},
        "_meta": {"unified/ui_session": secret}}})
    arguments, context = seen[0]
    assert context.ui_session == secret and secret not in repr(context)
    assert secret not in json.dumps(arguments) and secret not in json.dumps(reply)


def test_ui_only_descriptors_do_not_accept_capability_as_model_argument():
    from personal_mcp.desktop_catalog import desktop_tools

    descriptors = desktop_tools()
    for name in ("DesktopView", "DesktopAction", "DesktopStatus", "DesktopFiles"):
        assert descriptors[name]["_meta"]["ui"]["visibility"] == ["app"]
        assert "ui_session" not in descriptors[name]["inputSchema"]["properties"]
        assert "workflow_id" not in descriptors[name]["inputSchema"]["properties"]


def test_credential_rotation_revokes_admitted_but_not_dispatched_input(host):
    token = workflow(host)
    secret = host_call(host, "DesktopConsole", workflow_id=token)["_meta"]["desktop"]["ui_session"]
    native = host.desktop.native = Surface()
    context = SimpleNamespace(ui_session=secret)

    def call(name, **kwargs):
        return host.call_tool(name, kwargs, context=context)

    call("DesktopAction", action="arm", request_id="arm")
    frame = call("DesktopView", action="frame", target={"kind": "display", "id": "screen"})
    listing = host_call(host, "workflow_list")["structuredContent"]["workflows"][0]
    entered, release, dispatched = threading.Event(), threading.Event(), []

    def waiting_native(resource, captured, args):
        entered.set()
        assert release.wait(3)
        args["_authorize"]()
        dispatched.append("dispatch")
        return {"ok": True}

    native.act = waiting_native
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(call, "DesktopAction", action="click", x=3, y=4,
                              frame_id=frame["structuredContent"]["frame_id"], request_id="queued-before-rotation")
        try:
            assert entered.wait(3)
            rotated = host_call(host, "workflow_resume", workflow_ref=listing["workflow_ref"],
                expected_generation=listing["credential_generation"], request_id="rotate-during-native-wait")
            assert not rotated["isError"]
        finally:
            release.set()
        pending.result(3)
    assert dispatched == []


def test_host_resume_does_not_hold_registry_lock_when_registry_acquires_workflow_gate():
    from personal_mcp.host import UnifiedRuntime

    registry_lock = threading.RLock()
    revoked = []

    def resume(principal, reference, request_id, generation):
        # WorkflowRegistry.resume / use 的层级是 workflow gate → registry lock。
        # 调用方先持 registry lock 会与另一线程持 gate 后等 registry lock 互锁。
        assert not registry_lock._is_owned(), "registry lock must not be held while acquiring workflow gate"
        return {"ok": True, "workflow_ref": reference, "credential_generation": generation + 1}

    runtime = UnifiedRuntime.__new__(UnifiedRuntime)
    runtime.principal = object()
    runtime.registry = SimpleNamespace(lock=registry_lock, workflow_locks={},
        _reference_row=lambda *args: {"credential_generation": 0, "token_hash": "owner"}, resume=resume)
    runtime.desktop = SimpleNamespace(revoke_owner=revoked.append)
    result = runtime._workflow_action("resume", {"workflow_ref": "wfr_owner",
        "request_id": "rotate", "expected_generation": 0})
    assert result["ok"] and revoked == ["owner"]
