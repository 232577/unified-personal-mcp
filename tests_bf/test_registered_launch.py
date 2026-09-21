import json
from pathlib import Path
from types import SimpleNamespace

from bf_automation import bf_tools, runtime
from bf_automation.lease import DesktopLease
from bf_automation.task_store import TaskStore


class Tools:
    def __init__(self):
        self.functions = {}

    def tool(self, *, name, **kwargs):
        def register(fn):
            self.functions[name] = fn
            return fn
        return register


def test_launch_is_task_private_and_reports_early_exit(tmp_path, monkeypatch):
    apps = tmp_path / "apps"
    apps.mkdir()
    executable = tmp_path / "example.exe"
    executable.write_bytes(b"MZ")
    (apps / "sample.json").write_text(json.dumps({
        "id": "sample", "launcher": {"executable": str(executable),
        "args": ["--data-dir", "{instance_dir}"], "cwd": str(tmp_path)},
    }), encoding="utf-8")
    store = TaskStore(tmp_path / "state", allowed_root=tmp_path)
    a = store.begin(tmp_path)["bf_task_id"]
    b = store.begin(tmp_path)["bf_task_id"]
    calls = []

    def spawn(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(pid=99999999, poll=lambda: 7)

    monkeypatch.setattr(store, "spawn_owned", lambda token, *a, **k: spawn(*a, **k))
    monkeypatch.setattr(store, "stop_owned", lambda process: None)
    monkeypatch.setattr(bf_tools, "_collect_descendant_pids", lambda pid: {pid})
    tools = Tools()
    bf_tools.register_bf_tools(
        tools, store=store, lease=DesktopLease(), allowed_root=tmp_path, apps_dir=apps,
        controller_factory=lambda: SimpleNamespace(inventory=lambda *a, **k: {"windows": []}),
    )
    results = [tools.functions["LaunchApplication"](t, "sample", wait_seconds=0.01) for t in (a, b)]
    for token, result, (argv, kwargs) in zip((a, b), results, calls):
        expected = Path(store.status(token)["task_directory"]) / "applications" / "sample"
        assert argv[2] == str(expected / "instance")
        assert kwargs["shell"] is False
        assert result["exit_code"] == 7
        assert result["status"] == "exited_without_window"
        assert Path(result["logs"]["stderr"]).is_file()
    assert calls[0][0][2] != calls[1][0][2]


def test_application_environment_restores_only_standard_user_directories(monkeypatch):
    base = {"USERPROFILE": "BF-private-home", "HOME": "BF-private-home", "PATH": "existing-path"}
    directories = {"USERPROFILE": "C:/Users/Test", "LOCALAPPDATA": "C:/Users/Test/AppData/Local",
                   "APPDATA": "C:/Users/Test/AppData/Roaming"}
    monkeypatch.setattr(runtime, "_windows_user_folders", lambda: directories, raising=False)
    assert callable(getattr(runtime, "application_environment", None))
    actual = runtime.application_environment(base)
    assert actual["LOCALAPPDATA"] == directories["LOCALAPPDATA"]
    assert actual["USERPROFILE"] == directories["USERPROFILE"]
    assert actual["HOME"] == directories["USERPROFILE"]
    assert actual["PATH"] == "existing-path"
    assert base["HOME"] == "BF-private-home"
    assert "LOCALAPPDATA" not in base


def test_reuse_matches_executable_and_never_claims_existing_process(tmp_path, monkeypatch):
    executable = tmp_path / "sample.exe"
    executable.write_bytes(b"MZ")
    apps = tmp_path / "profiles"
    apps.mkdir()
    (apps / "sample.json").write_text(json.dumps({
        "id": "sample", "launcher": {"executable": str(executable), "reuse_existing": True},
        "window": {"title_contains": "Sample"},
    }), encoding="utf-8")
    store = TaskStore(tmp_path / "state", allowed_root=tmp_path)
    token = store.begin(tmp_path)["bf_task_id"]
    monkeypatch.setattr(bf_tools.psutil, "process_iter", lambda *a, **k: [
        SimpleNamespace(info={"pid": 42, "exe": str(executable)}),
    ])
    def must_not_launch(*a, **k):
        raise AssertionError("must attach existing exact executable, not launch twice")
    monkeypatch.setattr(bf_tools.subprocess, "Popen", must_not_launch)
    tools = Tools()
    existing = {"pid": 42, "title": "Sample", "window_id": "bfw_existing"}
    bf_tools.register_bf_tools(tools, store=store, lease=DesktopLease(), allowed_root=tmp_path,
        apps_dir=apps, controller_factory=lambda: SimpleNamespace(
            inventory=lambda *a, **k: {"windows": [existing]}))
    result = tools.functions["LaunchApplication"](token, "sample")
    assert result["reused_existing"] is True
    assert result["window"] == existing
    assert result["owned_pids"] == []
    assert store.status(token)["owned_pids"] == []


def test_window_screenshot_contains_an_actual_mcp_image(tmp_path):
    from PIL import Image
    path = tmp_path / "capture.png"
    Image.new("RGB", (16, 16)).save(path)
    capture = {"path": str(path), "width": 16, "height": 16}
    store = TaskStore(tmp_path / "state", allowed_root=tmp_path)
    token = store.begin(tmp_path)["bf_task_id"]
    tools = Tools()
    bf_tools.register_bf_tools(tools, store=store, lease=DesktopLease(), allowed_root=tmp_path,
        apps_dir=tmp_path, controller_factory=lambda: SimpleNamespace(
            screenshot=lambda *a, **k: {"window_capture": capture}))
    result = tools.functions["WindowScreenshot"](token, "bfw_test")
    assert hasattr(result, "content"), "metadata-only output cannot be seen by the agent"
    assert any(part.type == "image" for part in result.content)
    assert result.structured_content["window_capture"]["width"] == 16


def test_existing_application_ignores_hidden_gdi_helper_window(tmp_path, monkeypatch):
    executable = tmp_path / "sample.exe"
    profile = SimpleNamespace(executable=executable.resolve(), title_contains="Sample")
    monkeypatch.setattr(bf_tools.psutil, "process_iter", lambda *a, **k: [
        SimpleNamespace(info={"pid": 42, "exe": str(executable)}),
    ])
    main = {"pid": 42, "window_id": "main", "visible": True, "rect": [0, 0, 1200, 800]}
    helper = {"pid": 42, "window_id": "gdi", "visible": False, "rect": [0, 0, 1, 1]}
    controller = SimpleNamespace(inventory=lambda *a, **k: {"windows": [main, helper]})
    assert bf_tools._existing_application_window(profile, controller, "token") == main
