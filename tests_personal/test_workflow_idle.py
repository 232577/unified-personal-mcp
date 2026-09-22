import hashlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from personal_mcp.config import load_config
from personal_mcp.workflows import AuthPrincipal, WorkflowError, WorkflowRegistry
from tests_personal.test_config import installation


class IdleResource:
    def __init__(self, project):
        self.project = project
        self.blockers = []
        self.closed = False
        self.fail_close = False

    def idle_blockers(self):
        return self.blockers

    def close(self):
        if self.fail_close:
            raise OSError("fixture cleanup failure")
        self.closed = True


@pytest.fixture
def idle_registry(tmp_path):
    config = load_config(installation(tmp_path))
    config = SimpleNamespace(**{**vars(config), "project": config.project, "workflow_idle_seconds": 1800})
    now, resources = [1000.0], {}

    def factory(row):
        resource = IdleResource(row["project"])
        resources[row["token_hash"]] = resource
        return resource

    registry = WorkflowRegistry(config, factory, clock=lambda: now[0])
    yield registry, AuthPrincipal("owner"), now, resources, config
    for resource in resources.values():
        resource.fail_close = False
        resource.close = lambda: None
    registry.close()


def active(registry, owner, project="app", request="first"):
    token = registry.begin(owner, project, request)["workflow_id"]
    registry.activate(owner, token)
    return token, "wfr_" + hashlib.sha256(token.encode()).hexdigest()


def test_stale_empty_workflow_is_reclaimed_before_begin(idle_registry):
    registry, owner, now, resources, _ = idle_registry
    token, ref = active(registry, owner)
    now[0] += 1801
    assert registry.begin(owner, "app", "replacement")["ok"]
    assert registry.status(owner, token)["state"] == "ENDED"
    assert resources[ref[4:]].closed


def test_expire_reaps_active_but_reservation_ttl_is_distinct(idle_registry):
    registry, owner, now, _, config = idle_registry
    (config.workspace_root / "other").mkdir()
    token, _ = active(registry, owner)
    reserved = registry.begin(owner, "other", "reserved")["workflow_id"]
    now[0] += 121
    registry.expire()
    assert registry.status(owner, reserved)["state"] == "EXPIRED"
    assert registry.status(owner, token)["state"] == "ACTIVE"
    now[0] += 1800
    registry.expire()
    assert registry.status(owner, token)["state"] == "ENDED"


@pytest.mark.parametrize("probe", ["missing", "raises", "none", "tuple", "busy"])
def test_idle_probe_fails_closed_and_keeps_lease(idle_registry, probe):
    registry, owner, now, resources, _ = idle_registry
    token, ref = active(registry, owner)
    resource = resources[ref[4:]]
    if probe == "missing":
        resource.idle_blockers = None
    elif probe == "raises":
        def failed():
            raise OSError("cannot establish resource state")
        resource.idle_blockers = failed
    else:
        resource.blockers = {"none": None, "tuple": (), "busy": ["coding_process"]}[probe]
    now[0] += 1801
    with pytest.raises(WorkflowError, match="WORKFLOW_NOT_IDLE"):
        registry.release_idle(owner, ref)
    assert registry.status(owner, token)["state"] == "ACTIVE"
    assert not resource.closed
    with pytest.raises(WorkflowError, match="PROJECT_BUSY"):
        registry.begin(owner, "app", "second")


def test_recent_activity_and_status_list_do_not_renew(idle_registry):
    registry, owner, now, _, _ = idle_registry
    token, ref = active(registry, owner)
    now[0] += 600
    registry.status(owner, token)
    row = registry.list(owner)["workflows"][0]
    assert row["workflow_ref"] == ref and row["last_activity"] == 1000.0
    assert row["idle_seconds"] == 600
    assert token not in repr(row)
    with pytest.raises(WorkflowError, match="WORKFLOW_NOT_IDLE"):
        registry.release_idle(owner, ref)
    with pytest.raises(ValueError):
        with registry.use(owner, token):
            now[0] += 100
            raise ValueError("fixture call failure")
    assert registry.list(owner)["workflows"][0]["last_activity"] == 1700.0


def test_inflight_call_never_reclaimed_and_finish_renews(idle_registry):
    registry, owner, now, resources, _ = idle_registry
    token, ref = active(registry, owner)
    entered, finish = threading.Event(), threading.Event()

    def running():
        with registry.use(owner, token):
            entered.set()
            assert finish.wait(5)

    with ThreadPoolExecutor(2) as pool:
        task = pool.submit(running)
        assert entered.wait(3)
        now[0] += 1801
        try:
            with pytest.raises(WorkflowError, match="WORKFLOW_NOT_IDLE"):
                registry.release_idle(owner, ref)
            registry.expire()
            assert not resources[ref[4:]].closed
        finally:
            finish.set()
        task.result(timeout=3)
    assert registry.list(owner)["workflows"][0]["last_activity"] == now[0]


def test_reentrant_maintenance_cannot_reap_its_own_inflight_call(idle_registry):
    registry, owner, now, resources, _ = idle_registry
    token, ref = active(registry, owner)
    with registry.use(owner, token):
        now[0] += 1801
        registry.expire()
        assert not resources[ref[4:]].closed
        with pytest.raises(WorkflowError, match="WORKFLOW_NOT_IDLE"):
            registry.release_idle(owner, ref)
    assert registry.status(owner, token)["state"] == "ACTIVE"


def test_repeated_activation_renews_activity_and_configured_idle_grace(idle_registry):
    registry, owner, now, _, _ = idle_registry
    registry.idle_timeout = 300
    token, ref = active(registry, owner)
    now[0] += 200
    assert registry.activate(owner, token)["last_activity"] == now[0]
    now[0] += 299
    with pytest.raises(WorkflowError, match="WORKFLOW_NOT_IDLE"):
        registry.release_idle(owner, ref)
    now[0] += 1
    assert registry.release_idle(owner, ref)["state"] == "ENDED"


def test_activation_cannot_renew_through_an_idle_cleanup_probe(idle_registry):
    registry, owner, now, resources, _ = idle_registry
    token, ref = active(registry, owner)
    now[0] += 1801
    probing, finish_probe, activation_observed = threading.Event(), threading.Event(), threading.Event()
    activation_thread = []
    original_row = registry._row

    def probe():
        probing.set()
        assert finish_probe.wait(5)
        return []

    def observed_row(*args):
        row = original_row(*args)
        if activation_thread and threading.get_ident() == activation_thread[0]:
            activation_observed.set()
        return row

    def activate_again():
        activation_thread.append(threading.get_ident())
        return registry.activate(owner, token)

    registry._row = observed_row
    resources[ref[4:]].idle_blockers = probe
    with ThreadPoolExecutor(2) as pool:
        cleanup = pool.submit(registry.release_idle, owner, ref)
        assert probing.wait(3)
        activation = pool.submit(activate_again)
        try:
            assert activation_observed.wait(3)
        finally:
            finish_probe.set()
        assert cleanup.result(timeout=3)["state"] == "ENDED"
        with pytest.raises(WorkflowError, match="WORKFLOW_INACTIVE"):
            activation.result(timeout=3)


def test_owner_scoped_list_release_and_busy_details(idle_registry):
    registry, owner, _, _, config = idle_registry
    (config.workspace_root / "app" / "nested").mkdir()
    _, ref = active(registry, owner)
    with pytest.raises(WorkflowError) as conflict:
        registry.begin(owner, "app/nested", "nested")
    details = conflict.value.details
    assert details["blockers"][0]["workflow_ref"] == ref
    assert details["blockers"][0]["relation"] == "ancestor"
    assert details["guidance"]
    other = AuthPrincipal("another-owner")
    assert registry.list(other)["workflows"] == []
    with pytest.raises(WorkflowError, match="WORKFLOW_DENIED"):
        registry.release_idle(other, ref)
    with pytest.raises(WorkflowError) as hidden:
        registry.begin(other, "app", "other")
    assert ref not in repr(hidden.value.details)
    assert hidden.value.details["blockers"] == []


def test_cleanup_failure_preserves_lease(idle_registry):
    registry, owner, now, resources, _ = idle_registry
    token, ref = active(registry, owner)
    resources[ref[4:]].fail_close = True
    now[0] += 1801
    result = registry.release_idle(owner, ref)
    assert result["state"] == "CLEANUP_BLOCKED"
    with pytest.raises(WorkflowError, match="PROJECT_BUSY"):
        registry.begin(owner, "app", "blocked")
    assert registry.status(owner, token)["state"] == "CLEANUP_BLOCKED"


def test_cleanup_does_not_hold_global_lock_or_accept_racing_use(idle_registry):
    registry, owner, now, resources, config = idle_registry
    (config.workspace_root / "other").mkdir()
    token, ref = active(registry, owner)
    now[0] += 1801
    closing, finish = threading.Event(), threading.Event()

    def close():
        closing.set()
        assert finish.wait(5)

    resources[ref[4:]].close = close

    def attempt_use():
        with registry.use(owner, token):
            pytest.fail("closed workflow accepted a racing call")

    with ThreadPoolExecutor(3) as pool:
        cleanup = pool.submit(registry.release_idle, owner, ref)
        assert closing.wait(3)
        racing = pool.submit(attempt_use)
        try:
            assert pool.submit(registry.begin, owner, "other", "unrelated").result(timeout=2)["ok"]
        finally:
            finish.set()
        assert cleanup.result(timeout=3)["state"] == "ENDED"
        with pytest.raises(WorkflowError, match="WORKFLOW_INACTIVE"):
            racing.result(timeout=3)


def test_32_sibling_workflows_have_independent_parallel_gates(idle_registry):
    registry, owner, _, _, config = idle_registry
    tokens = []
    for index in range(32):
        project = config.workspace_root / f"project-{index}"
        project.mkdir()
        tokens.append(active(registry, owner, str(project), f"parallel-{index}")[0])
    barrier = threading.Barrier(32)

    def work(index):
        with registry.use(owner, tokens[index], write=True) as resource:
            from pathlib import Path
            path = Path(resource.project)
            barrier.wait(timeout=10)
            (path / "result.txt").write_text(str(index), encoding="utf-8")
            return path

    with ThreadPoolExecutor(32) as pool:
        paths = list(pool.map(work, range(32)))
    assert len(set(paths)) == 32
    assert [path.joinpath("result.txt").read_text(encoding="utf-8") for path in paths] == list(map(str, range(32)))


def test_legacy_database_adds_last_activity_without_expiring_existing_work(tmp_path):
    config = load_config(installation(tmp_path))
    config.data_root.mkdir()
    with sqlite3.connect(config.data_root / "workflows.sqlite3") as db:
        db.execute("CREATE TABLE workflows (token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, "
                   "request_id TEXT NOT NULL, digest TEXT NOT NULL, project TEXT NOT NULL, "
                   "access TEXT NOT NULL, state TEXT NOT NULL, expires REAL NOT NULL, "
                   "created REAL NOT NULL, error TEXT, UNIQUE(owner, request_id))")
        db.execute("INSERT INTO workflows VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                   ("a" * 64, "owner", "legacy", "digest", str(config.project("app")),
                    "write", "ACTIVE", 120, 0))
    registry = WorkflowRegistry(config, lambda _: None, clock=lambda: 5000)
    try:
        row = registry.list(AuthPrincipal("owner"))["workflows"][0]
        assert row["last_activity"] == 5000
        assert row["state"] == "CLEANUP_BLOCKED"
    finally:
        registry.close()
