import json
import subprocess
import sys
from types import SimpleNamespace

import psutil
import pytest

from bf_automation.bf_tools import register_bf_tools
from bf_automation.lease import DesktopLease
from bf_automation.task_store import TaskStore
from tests_bf.test_registered_launch import Tools


def test_task_end_releases_native_tree_and_keeps_unrelated_process(tmp_path):
    store = TaskStore(tmp_path / "state", allowed_root=tmp_path)
    token = store.begin(tmp_path)["bf_task_id"]
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
    owned = None
    try:
        owned = store.spawn_owned(token, [sys.executable, "-c", "import time; time.sleep(30)"])
        store.end(token)
        owned.wait(timeout=3)
        assert unrelated.poll() is None
    finally:
        if owned and owned.poll() is None:
            owned.kill()
            owned.wait(timeout=3)
        unrelated.kill()
        unrelated.wait(timeout=3)


def test_native_launch_without_a_window_cleans_up_before_reply(tmp_path):
    apps = tmp_path / "apps"
    apps.mkdir()
    (apps / "sleeper.json").write_text(json.dumps({"id": "sleeper", "launcher": {
        "executable": sys.executable, "args": ["-c", "import time; time.sleep(30)"]}}))
    store = TaskStore(tmp_path / "state", allowed_root=tmp_path)
    token = store.begin(tmp_path)["bf_task_id"]
    tools = Tools()
    register_bf_tools(tools, store=store, lease=DesktopLease(), allowed_root=tmp_path,
        apps_dir=apps, controller_factory=lambda: SimpleNamespace(inventory=lambda *a, **k: {"windows": []}))
    try:
        result = tools.functions["LaunchApplication"](token, "sleeper", wait_seconds=0.05)
        assert result["status"] == "window_timeout"
        assert not psutil.pid_exists(result["pid"])
    finally:
        store.end(token)


def test_failed_cleanup_hook_does_not_skip_native_cleanup(tmp_path):
    store = TaskStore(tmp_path / "state", allowed_root=tmp_path)
    token = store.begin(tmp_path)["bf_task_id"]
    owned = store.spawn_owned(token, [sys.executable, "-c", "import time; time.sleep(30)"])
    def broken_hook(token):
        raise RuntimeError("fixture failure")
    store.add_end_hook(broken_hook)
    try:
        with pytest.raises(RuntimeError, match="TASK_CLEANUP_INCOMPLETE"):
            store.end(token)
        owned.wait(timeout=3)
        assert store.status(token)["status"] == "active"
        store._end_hooks.remove(broken_hook)
        store.end(token)
    finally:
        if owned.poll() is None:
            owned.kill()
            owned.wait(timeout=3)
