import json
import sys

import pytest

from bf_automation.browser.process import WorkerProcess
from personal_mcp.bf.bootstrap import build_mcp
from personal_mcp.protection import InstanceLock
from personal_mcp.service import LocalService
from tests_bf.hybrid.test_hybrid_lifecycle import FakePlatform, make_profile
from tests_personal.test_service import config


def worker_at(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text("import sys\nsys.stdin.buffer.read()\n", encoding="utf-8")
    return WorkerProcess(sys.executable, script, tmp_path, tmp_path)


def test_worker_retries_log_close_failure(tmp_path):
    worker = worker_at(tmp_path)
    original = worker.log

    class FailingLog:
        def close(self):
            worker.log = original
            raise OSError("temporary close failure")

    worker.log = FailingLog()
    try:
        with pytest.raises(OSError):
            worker.close()
        assert worker.close()["released"]
        assert original.closed and worker.process.poll() is not None
        assert worker.close()["released"]
    finally:
        original.close()


def test_worker_keeps_job_until_drain_can_be_confirmed(tmp_path, monkeypatch):
    worker = worker_at(tmp_path)
    try:
        with monkeypatch.context() as fault:
            ticks = iter([0, 1, 6])
            fault.setattr("bf_automation.browser.process.time.monotonic", lambda: next(ticks))
            fault.setattr(worker, "active_pids", lambda: [worker.process.pid])
            assert not worker.close()["released"]
        assert worker.job is not None
        assert worker.close()["released"]
        assert worker.job is None
    finally:
        worker.close()


def test_stop_releases_installation_when_final_status_write_fails(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    service = LocalService(cfg)
    key = "installation:" + str(cfg.data_root.resolve())
    service.guard = InstanceLock(key)
    def failed_write():
        raise OSError("disk full")
    monkeypatch.setattr(service, "_save_status", failed_write)
    try:
        service.stop()
        assert service.guard is None
        assert service.status()["error"] == "FINAL_STATUS_WRITE_FAILED"
        replacement = InstanceLock(key)
        replacement.close()
        service.stop()
    finally:
        if service.guard is not None:
            service.guard.close()


def test_integrated_task_end_recovers_failed_hybrid_launch_capacity(tmp_path, monkeypatch):
    browsers = tmp_path / "browsers"
    browsers.mkdir()
    cfg = tmp_path / "browser.json"
    cfg.write_text(json.dumps({"enabled": True, "version": 1, "mode": "headless",
        "max_sessions": 4, "max_pages_per_session": 4, "python": sys.executable,
        "browsers_path": str(browsers), "actions_enabled": True, "transfers_enabled": True}), encoding="utf-8")
    server = build_mcp(state_root=tmp_path / "state", allowed_root=tmp_path,
                       browser_config_path=cfg)
    executable = tmp_path / "fixture.exe"
    executable.write_bytes(b"fixture")
    platform = FakePlatform(executable)
    hybrid, store = server._bf_hybrid_manager, server._bf_store
    hybrid.platform = platform
    profile = make_profile(tmp_path, executable)
    tokens = [store.begin(tmp_path)["bf_task_id"] for _ in range(3)]
    try:
        with monkeypatch.context() as fault:
            def missing(*args):
                raise TimeoutError("fixture readiness timeout")
            def incomplete(identity):
                raise RuntimeError("fixture cleanup failure")
            fault.setattr(platform, "wait_ready", missing)
            fault.setattr(platform, "terminate_tree", incomplete)
            for token in tokens[:2]:
                with pytest.raises(RuntimeError, match="HYBRID_CLEANUP_INCOMPLETE"):
                    hybrid.prepare_managed(token, profile)
        for token in tokens[:2]:
            store.end(token)
        assert not hybrid._failed_launches
        instance = hybrid.prepare_managed(tokens[2], profile)
        assert instance.ownership == "managed"
        store.end(tokens[2])
    finally:
        server._bf_browser_manager.shutdown()
