import hashlib
import os

import psutil
import pytest

from bf_automation.task_store import TaskStore
from personal_mcp.config import load_config
from personal_mcp.workflows import AuthPrincipal, WorkflowRegistry
from tests_personal.test_config import installation


def test_restart_can_release_verified_dead_resources_without_resuming(tmp_path):
    cfg = load_config(installation(tmp_path))
    owner = AuthPrincipal("owner")
    original = WorkflowRegistry(cfg, lambda row: object())
    token = original.begin(owner, "app", "begin")["workflow_id"]
    original.activate(owner, token)
    recovered = []
    restarted = WorkflowRegistry(cfg, lambda row: pytest.fail("must not activate"),
                                 recovery=lambda row: recovered.append(row["token_hash"]))
    assert restarted.recover()["blocked"] == 0
    assert recovered == [hashlib.sha256(token.encode()).hexdigest()]
    assert restarted.status(owner, token)["state"] == "ENDED"
    assert restarted.begin(owner, "app", "new")["ok"]


def test_recovery_failure_retains_project_lease(tmp_path):
    cfg = load_config(installation(tmp_path))
    owner = AuthPrincipal("owner")
    registry = WorkflowRegistry(cfg, lambda row: object())
    token = registry.begin(owner, "app", "begin")["workflow_id"]
    registry.activate(owner, token)
    def refuse(row):
        raise RuntimeError("still alive")
    restarted = WorkflowRegistry(cfg, lambda row: object(), recovery=refuse)
    assert restarted.recover()["blocked"] == 1
    assert restarted.status(owner, token)["state"] == "CLEANUP_BLOCKED"
    with pytest.raises(RuntimeError, match="PROJECT_BUSY"):
        restarted.begin(owner, "app", "new")


def test_task_recovery_checks_process_identity_and_preserves_unrelated_records(tmp_path):
    store = TaskStore(tmp_path / "state", allowed_root=tmp_path)
    a = store.begin(tmp_path, context_id="a" * 64)["bf_task_id"]
    b = store.begin(tmp_path, context_id="b" * 64)["bf_task_id"]
    store.add_owned_pid(a, os.getpid())
    with pytest.raises(RuntimeError, match="RECOVERY_PROCESS_STILL_ALIVE"):
        store.recover_context("a" * 64)
    task_dir, record = store.require(a)
    record["owned_processes"][str(os.getpid())]["created"] -= 1
    store._write_json(task_dir / "task.json", record)
    store.recover_context("a" * 64)
    assert psutil.pid_exists(os.getpid())
    assert store.status(b)["status"] == "active"
    with pytest.raises(PermissionError):
        store.require(a)
