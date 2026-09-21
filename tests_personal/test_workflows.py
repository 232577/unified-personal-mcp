import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from personal_mcp.config import load_config
from personal_mcp.workflows import AuthPrincipal, WorkflowError, WorkflowRegistry
from tests_personal.test_config import installation


class Resource:
    def __init__(self):
        self.closed = False
        self.fail = False

    def close(self):
        if self.fail:
            raise OSError("cleanup failed")
        self.closed = True


@pytest.fixture
def setup(tmp_path):
    cfg = load_config(installation(tmp_path))
    (cfg.workspace_root / "app" / "nested").mkdir()
    (cfg.workspace_root / "other").mkdir()
    now, created = [1000.0], []

    def factory(record):
        resource = Resource()
        created.append(resource)
        return resource

    registry = WorkflowRegistry(cfg, factory, clock=lambda: now[0])
    yield registry, AuthPrincipal("owner-a"), AuthPrincipal("owner-b"), now, created, cfg
    registry.close()


def begin(registry, owner, request="first", project="app", access="write"):
    return registry.begin(owner, project, request, access)


def assert_code(code, action):
    with pytest.raises(WorkflowError) as err:
        action()
    assert err.value.code == code


def test_reservation_has_no_process_and_retry_never_recovers_secret(setup):
    registry, a, _, now, created, cfg = setup
    first = begin(registry, a)
    assert first["state"] == "RESERVED" and not created
    now[0] += 60
    repeated = begin(registry, a)
    assert repeated["code"] == "BEGIN_ALREADY_ACCEPTED"
    assert "workflow_id" not in repeated and "token" not in json.dumps(repeated)
    assert first["workflow_id"].encode() not in (cfg.data_root / "workflows.sqlite3").read_bytes()
    now[0] += 61
    registry.expire()
    assert_code("WORKFLOW_INACTIVE", lambda: registry.activate(a, first["workflow_id"]))
    assert begin(registry, a, request="new")["state"] == "RESERVED"


@pytest.mark.parametrize("project,access", [("other", "write"), ("app", "read")])
def test_request_id_payload_collision_does_not_recover_capability(setup, project, access):
    registry, a, *_ = setup
    begin(registry, a)
    assert begin(registry, a, project=project, access=access) == {"ok": False, "code": "BEGIN_CONFLICT"}


def test_wrong_owner_cannot_inspect_activate_or_end(setup):
    registry, a, b, *_ = setup
    token = begin(registry, a)["workflow_id"]
    for action in (registry.status, registry.activate, registry.end):
        assert_code("WORKFLOW_DENIED", lambda: action(b, token))
    assert registry.status(a, token)["state"] == "RESERVED"


@pytest.mark.parametrize("project", ["app", "app/.", "app/nested"])
def test_canonical_and_overlapping_writes_are_exclusive(setup, project):
    registry, a, b, *_ = setup
    begin(registry, a)
    assert_code("PROJECT_BUSY", lambda: begin(registry, b, request="second", project=project))


def test_reads_can_share_but_cannot_overlap_writer(setup):
    registry, a, b, *_ = setup
    begin(registry, a, access="read")
    begin(registry, b, access="read")
    assert_code("PROJECT_BUSY", lambda: begin(registry, b, request="write"))


def test_activation_and_expiry_are_mutually_exclusive(setup):
    registry, a, _, now, created, _ = setup
    token = begin(registry, a)["workflow_id"]
    now[0] += 121
    with ThreadPoolExecutor(2) as pool:
        expired = pool.submit(registry.expire)
        active = pool.submit(registry.activate, a, token)
        expired.result()
        assert_code("WORKFLOW_INACTIVE", active.result)
    assert not created


def test_cleanup_failure_retains_lease_and_can_be_retried(setup):
    registry, a, b, _, created, _ = setup
    token = begin(registry, a)["workflow_id"]
    registry.activate(a, token)
    created[0].fail = True
    assert registry.end(a, token)["state"] == "CLEANUP_BLOCKED"
    assert_code("PROJECT_BUSY", lambda: begin(registry, b))
    created[0].fail = False
    assert registry.end(a, token)["state"] == "ENDED"
    assert registry.end(a, token)["state"] == "ENDED"
    assert created[0].closed
    assert begin(registry, b)["state"] == "RESERVED"


def test_end_waits_for_accepted_call_and_rejects_new_calls(setup):
    registry, a, _, _, created, _ = setup
    token = begin(registry, a)["workflow_id"]
    registry.activate(a, token)
    entered, finish = threading.Event(), threading.Event()

    def call():
        with registry.use(a, token):
            entered.set()
            assert finish.wait(3)

    with ThreadPoolExecutor(2) as pool:
        call_future = pool.submit(call)
        assert entered.wait(3)
        end_future = pool.submit(registry.end, a, token)
        assert not created[0].closed
        finish.set()
        call_future.result()
        assert end_future.result()["state"] == "ENDED"
    assert_code("WORKFLOW_INACTIVE", lambda: registry.activate(a, token))


def test_restart_never_resumes_active_business_or_reissues_token(setup):
    registry, a, b, _, created, cfg = setup
    token = begin(registry, a)["workflow_id"]
    registry.activate(a, token)
    other = WorkflowRegistry(cfg, lambda _: pytest.fail("must not resume"))
    try:
        assert other.status(a, token)["state"] == "CLEANUP_BLOCKED"
        assert begin(other, a)["code"] == "BEGIN_ALREADY_ACCEPTED"
        assert_code("PROJECT_BUSY", lambda: begin(other, b))
        assert len(created) == 1
    finally:
        other.close()
