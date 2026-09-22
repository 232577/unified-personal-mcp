import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from personal_mcp.config import load_config
from personal_mcp.workflows import AuthPrincipal, WorkflowError, WorkflowRegistry
from tests_personal.test_config import installation
from tests_personal.test_workflows import Resource


def make_registry(tmp_path, factory):
    config = load_config(installation(tmp_path))
    (config.workspace_root / 'other').mkdir()
    registry = WorkflowRegistry(config, factory)
    owner = AuthPrincipal('owner')
    a = registry.begin(owner, 'app', 'a')['workflow_id']
    b = registry.begin(owner, 'other', 'b')['workflow_id']
    return registry, owner, a, b


def test_slow_project_factory_does_not_block_other_project_activation(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def factory(row):
        if row['project'].endswith('app'):
            entered.set()
            assert release.wait(5)
        return Resource()

    registry, owner, a, b = make_registry(tmp_path, factory)
    try:
        with ThreadPoolExecutor(2) as pool:
            slow = pool.submit(registry.activate, owner, a)
            assert entered.wait(2)
            try:
                assert pool.submit(registry.activate, owner, b).result(timeout=1)['state'] == 'ACTIVE'
            finally:
                release.set()
            assert slow.result(timeout=3)['state'] == 'ACTIVE'
    finally:
        release.set()
        registry.close()


def test_close_during_starting_reclaims_late_resource_without_accepting_it(tmp_path):
    entered, release = threading.Event(), threading.Event()
    resource = Resource()

    def factory(row):
        entered.set()
        assert release.wait(5)
        return resource

    registry, owner, a, _ = make_registry(tmp_path, factory)
    registry.cleanup_timeout = 0.05
    try:
        with ThreadPoolExecutor(2) as pool:
            activation = pool.submit(registry.activate, owner, a)
            assert entered.wait(2)
            closing = pool.submit(registry.close)
            try:
                with pytest.raises(WorkflowError, match='CLEANUP_FAILED'):
                    closing.result(timeout=1)
            finally:
                release.set()
            with pytest.raises(WorkflowError, match='WORKFLOW_INACTIVE'):
                activation.result(timeout=3)
        assert resource.closed
        assert not registry.resources
    finally:
        release.set()
        registry.close()


def test_control_and_status_are_available_during_long_write(tmp_path):
    registry, owner, a, _ = make_registry(tmp_path, lambda _: Resource())
    registry.activate(owner, a)
    entered, release = threading.Event(), threading.Event()

    def write():
        with registry.use(owner, a, write=True):
            entered.set()
            assert release.wait(5)

    def control():
        with registry.use(owner, a, write=True, control=True):
            return registry.status(owner, a)

    try:
        with ThreadPoolExecutor(2) as pool:
            writer = pool.submit(write)
            assert entered.wait(2)
            try:
                assert pool.submit(control).result(timeout=1)['state'] == 'ACTIVE'
            finally:
                release.set()
            writer.result(timeout=2)
    finally:
        registry.close()


def test_close_waits_for_control_inflight_and_timeout_preserves_resource(tmp_path):
    registry, owner, a, _ = make_registry(tmp_path, lambda _: Resource())
    registry.activate(owner, a)
    registry.cleanup_timeout = 0.05
    entered, release = threading.Event(), threading.Event()
    resources = list(registry.resources.values())

    def control():
        with registry.use(owner, a, control=True):
            entered.set()
            assert release.wait(5)

    try:
        with ThreadPoolExecutor(1) as pool:
            task = pool.submit(control)
            try:
                assert entered.wait(1)
                with pytest.raises(WorkflowError, match='CLEANUP_FAILED'):
                    registry.close()
                assert not resources[0].closed
                assert registry.resources
                with pytest.raises(WorkflowError, match='WORKFLOW_INACTIVE'):
                    with registry.use(owner, a, control=True):
                        pytest.fail('closing admitted a new call')
            finally:
                release.set()
            task.result(timeout=2)
        registry.close()
        assert resources[0].closed
    finally:
        release.set()
        registry.close()


def test_status_does_not_trigger_slow_expiry_or_touch_activity(tmp_path, monkeypatch):
    registry, owner, a, _ = make_registry(tmp_path, lambda _: Resource())
    registry.activate(owner, a)
    before = registry.list(owner)['workflows'][0]['last_activity']
    monkeypatch.setattr(registry, 'expire', lambda: pytest.fail('status must not perform cleanup'))
    try:
        assert registry.status(owner, a)['last_activity'] == before
    finally:
        registry.close()


def test_snapshot_exposes_only_owner_scoped_queue_and_admission_counts(tmp_path):
    registry, owner, a, _ = make_registry(tmp_path, lambda _: Resource())
    registry.activate(owner, a)
    entered, release = threading.Event(), threading.Event()

    def writing(block):
        with registry.use(owner, a, write=True):
            if block:
                entered.set()
                assert release.wait(5)

    try:
        assert hasattr(registry, 'snapshot'), 'cached resource usage is missing'
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(writing, True)
            assert entered.wait(1)
            second = pool.submit(writing, False)
            try:
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline and not registry.snapshot(owner)['queued']:
                    time.sleep(0.005)
                snapshot = registry.snapshot(owner)
                assert snapshot['inflight'] == 1
                assert len(snapshot['queued']) == 1
                assert snapshot['queued'][0]['reason'] == 'WORKFLOW_WRITE_SERIALIZATION'
                assert registry.snapshot(AuthPrincipal('stranger'))['queued'] == []
                assert registry.snapshot(AuthPrincipal('stranger'))['inflight'] == 0
            finally:
                release.set()
            first.result(timeout=2)
            second.result(timeout=2)
    finally:
        release.set()
        registry.close()


def test_rotation_keeps_dispatched_write_but_rejects_queued_old_credentials(tmp_path):
    registry, owner, old, _ = make_registry(tmp_path, lambda _: Resource())
    registry.activate(owner, old)
    ref = registry.status(owner, old)['workflow_ref']
    resource = registry.resources[ref[4:]]
    entered, release = threading.Event(), threading.Event()
    side_effects = []

    def writing(block):
        with registry.use(owner, old, write=True):
            if block:
                entered.set()
                assert release.wait(5)
            side_effects.append(block)

    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(writing, True)
            assert entered.wait(1)
            queued = pool.submit(writing, False)
            try:
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline and not registry.snapshot(owner)['queued']:
                    time.sleep(0.005)
                assert registry.snapshot(owner)['queued']
                result = registry.resume(owner, ref, 'resume', 0)
                assert registry.resources[ref[4:]] is resource
                assert not resource.closed
                with registry.use(owner, result['workflow_id'], control=True):
                    assert registry.snapshot(owner)['inflight'] == 2
            finally:
                release.set()
            first.result(timeout=2)
            with pytest.raises(WorkflowError, match='WORKFLOW_CREDENTIAL_REPLACED'):
                queued.result(timeout=2)
        assert side_effects == [True]
        assert not registry.snapshot(owner)['queued']
    finally:
        release.set()
        registry.close()


def test_read_only_and_cross_owner_checks_still_apply_to_control(tmp_path):
    config = load_config(installation(tmp_path))
    registry = WorkflowRegistry(config, lambda _: Resource())
    owner = AuthPrincipal('owner')
    token = registry.begin(owner, 'app', 'begin', access='read')['workflow_id']
    registry.activate(owner, token)
    try:
        with pytest.raises(WorkflowError, match='READ_ONLY_WORKFLOW'):
            with registry.use(owner, token, write=True, control=True):
                pytest.fail('write escaped read-only lease')
        with pytest.raises(WorkflowError, match='WORKFLOW_DENIED'):
            with registry.use(AuthPrincipal('stranger'), token, control=True):
                pytest.fail('control escaped ownership')
    finally:
        registry.close()


def test_concurrent_activation_builds_only_one_resource(tmp_path):
    entered, release = threading.Event(), threading.Event()
    built = []

    def factory(row):
        resource = Resource()
        built.append(resource)
        entered.set()
        assert release.wait(5)
        return resource

    registry, owner, token, _ = make_registry(tmp_path, factory)
    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(registry.activate, owner, token)
            assert entered.wait(1)
            second = pool.submit(registry.activate, owner, token)
            release.set()
            assert first.result(timeout=2)['state'] == 'ACTIVE'
            assert second.result(timeout=2)['state'] == 'ACTIVE'
        assert len(built) == 1
    finally:
        release.set()
        registry.close()


def test_cleanup_finishing_at_gate_timeout_does_not_regress_ended_state(tmp_path):
    registry, owner, token, _ = make_registry(tmp_path, lambda _: Resource())
    registry.activate(owner, token)
    key = registry._row(owner, token)['token_hash']
    resource = registry.resources[key]

    class CompletedAtTimeout:
        def acquire(self, **kwargs):
            resource.close()
            registry.resources.pop(key)
            registry._state(key, 'ENDED')
            return False

    registry.workflow_locks[key] = CompletedAtTimeout()
    try:
        assert registry.end(owner, token)['state'] == 'ENDED'
        assert resource.closed
    finally:
        registry.close()


def test_snapshot_returns_owner_cache_without_waiting_for_registry_lock(tmp_path):
    registry, owner, token, _ = make_registry(tmp_path, lambda _: Resource())
    registry.activate(owner, token)
    before = registry.snapshot(owner)
    held, release = threading.Event(), threading.Event()

    def hold_registry_lock():
        with registry.lock:
            held.set()
            assert release.wait(5)

    try:
        with ThreadPoolExecutor(2) as pool:
            holder = pool.submit(hold_registry_lock)
            assert held.wait(1)
            try:
                cached = pool.submit(registry.snapshot, owner).result(timeout=0.2)
                assert cached['stale'] is True
                assert cached['workflows'] == before['workflows']
                stranger = pool.submit(registry.snapshot, AuthPrincipal('stranger')).result(timeout=0.2)
                assert stranger['stale'] is True
                assert stranger['workflows'] == {} and stranger['queued'] == []
                assert stranger['resources'] == 0 and stranger['inflight'] == 0
                cached['workflows'].clear()
                again = pool.submit(registry.snapshot, owner).result(timeout=0.2)
                assert again['workflows'] == before['workflows']
            finally:
                release.set()
            holder.result(timeout=2)
        assert registry.snapshot(owner)['stale'] is False
    finally:
        release.set()
        registry.close()
