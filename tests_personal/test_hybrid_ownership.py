import subprocess
import sys

import pytest

from bf_automation.hybrid.process import HybridPlatform
from bf_automation.task_store import TaskStore
from tests_bf.hybrid.test_hybrid_lifecycle import manager_for, make_profile


def test_pending_hybrid_launch_counts_towards_capacity(tmp_path):
    _, _, _, _, manager = manager_for(tmp_path)
    manager._reserve_managed("first")
    with pytest.raises(ValueError, match="LIMIT"):
        manager._reserve_managed("first")
    manager._reserve_managed("second")
    with pytest.raises(ValueError, match="LIMIT"):
        manager._reserve_managed("third")


def test_failed_hybrid_launch_cleanup_is_retried_at_task_end(tmp_path, monkeypatch):
    project, executable, store, platform, manager = manager_for(tmp_path)
    token = store.begin(project)["bf_task_id"]
    def missing(*args):
        raise TimeoutError("fixture readiness timeout")
    def cleanup_failed(identity):
        raise RuntimeError("fixture cleanup failure")
    monkeypatch.setattr(platform, "wait_ready", missing)
    original = platform.terminate_tree
    monkeypatch.setattr(platform, "terminate_tree", cleanup_failed)
    with pytest.raises(RuntimeError, match="HYBRID_CLEANUP_INCOMPLETE"):
        manager.prepare_managed(token, make_profile(project, executable))
    monkeypatch.setattr(platform, "terminate_tree", original)
    store.end(token)
    assert platform.terminated == [platform.shell]


def test_real_hybrid_platform_owns_process_before_resume(tmp_path):
    store = TaskStore(tmp_path / "state", allowed_root=tmp_path)
    token = store.begin(tmp_path)["bf_task_id"]
    platform = HybridPlatform()
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        identity = platform.launch(executable=sys.executable, args=["-c", "import time; time.sleep(30)"],
            cwd=tmp_path, env=None, stdout_path=tmp_path / "out.log", stderr_path=tmp_path / "err.log",
            owner=token, store=store)
        assert platform.owns_job(identity)
        assert platform.terminate_tree(identity)["remaining"] == []
        assert unrelated.poll() is None
    finally:
        store.end(token)
        unrelated.kill()
        unrelated.wait(timeout=3)
