import json
from tests_bf.support import browser_config
from pathlib import Path
from types import SimpleNamespace

from bf_automation.browser.manager import BrowserManager
from bf_automation.task_store import TaskStore

ROOT = Path(__file__).resolve().parents[2]


class AttachedWorker:
    instances = []

    def __init__(self, *_args, **_kwargs):
        self.pid = 46000 + len(type(self).instances)
        self.events = []
        self.boot = None
        type(self).instances.append(self)

    def call(self, operation, arguments):
        self.events.append(operation)
        if operation == "boot":
            self.boot = arguments
            assert arguments["engine_mode"] == "webview2"
            assert arguments["connection"]["ownership"] == "attached"
            return {
                "engine": "webview2",
                "version": "153.0.4234.32",
                "mode": "hybrid",
                "ownership": "attached",
                "worker_pid": self.pid,
                "console_visible": False,
            }
        if operation == "health":
            return {"alive": True}
        raise AssertionError(operation)

    def close(self):
        self.events.append("worker-close")
        return {"released": True, "remaining": [], "deadline_exceeded": False}


class AttachedHybridManager:
    def __init__(self):
        self.events = []
        self.released = []

    def end_task(self, token):
        self.events.append(("end_task", token))

    def prepare_attached(self, token, profile):
        self.events.append(("prepare_attached", token, profile.application_id))
        return SimpleNamespace(
            hybrid_instance_id="bfh_" + "3" * 32,
            shell_window_id="bfw_" + "4" * 24,
            public_identity=lambda: {
                "hybrid_instance_id": "bfh_" + "3" * 32,
                "ownership": "attached",
                "shell_window_id": "bfw_" + "4" * 24,
            },
        )

    def connection_spec(self, token, hybrid_instance_id):
        self.events.append(("connection", token, hybrid_instance_id))
        return {
            "endpoint": "http://127.0.0.1:53123",
            "hybrid_instance_id": hybrid_instance_id,
            "engine_version": "153.0.4234.32",
            "ownership": "attached",
        }

    def release(self, token, hybrid_instance_id):
        self.events.append(("release", token, hybrid_instance_id))
        self.released.append(hybrid_instance_id)
        return {
            "hybrid_instance_id": hybrid_instance_id,
            "ownership": "attached",
            "process_terminated": False,
            "remaining": [],
        }


def write_attached_profile(apps, project):
    executable = project / "fixture.exe"
    executable.write_bytes(b"fixture")
    row = {
        "version": 1,
        "id": "u4a-attached",
        "kind": "hybrid-webview2",
        "adapter": "hybrid-webview2",
        "project_root": str(project),
        "launcher": {
            "executable": str(executable),
            "args": [],
            "cwd": str(project),
            "reuse_existing": True,
        },
        "window": {"title_contains": "U4A Fixture"},
        "hybrid": {
            "engine": "webview2",
            "ownership": "attached",
            "debug_mode": "existing-proven",
            "max_instances": 2,
        },
        "web": {
            "navigation_origins": ["http://127.0.0.1:65533"],
            "resource_origins": ["http://127.0.0.1:65533"],
            "upload_roots": [],
        },
    }
    apps.mkdir()
    (apps / "u4a-attached.json").write_text(json.dumps(row), encoding="utf-8")


def test_browser_manager_routes_attached_profile_and_never_requests_managed_launch(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    apps = tmp_path / "apps"
    write_attached_profile(apps, project)
    store = TaskStore(tmp_path / "state", allowed_root=tmp_path)
    config = browser_config(tmp_path)
    config_path = tmp_path / "browser.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    hybrid = AttachedHybridManager()
    AttachedWorker.instances = []
    monkeypatch.setattr("bf_automation.browser.manager.WorkerProcess", AttachedWorker)
    manager = BrowserManager(
        store, config_path, apps, source_root=ROOT, hybrid_manager=hybrid,
    )
    token = store.begin(project)["bf_task_id"]
    try:
        started = manager.start(token, "u4a-attached", "attached-start")
        assert started["state"] == "completed"
        session = started["result"]["session"]
        assert session["engine"] == "webview2"
        assert session["ownership"] == "attached"
        assert session["hybrid_instance_id"] == "bfh_" + "3" * 32
        assert hybrid.events[0][0] == "prepare_attached"
        assert all(event[0] != "prepare_managed" for event in hybrid.events if isinstance(event, tuple))
        closed = manager.request(
            token, session["session_id"], "close", "attached-close", {},
        )
        assert closed["state"] == "completed"
        assert hybrid.released == ["bfh_" + "3" * 32]
        assert hybrid.events[-1][0] == "release"
        assert AttachedWorker.instances[0].events[-1] == "worker-close"
    finally:
        store.end(token)
        manager.shutdown()
