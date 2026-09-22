import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from personal_mcp.config import load_config
from personal_mcp.workflows import AuthPrincipal, WorkflowError, WorkflowRegistry
from tests_personal.test_config import installation
from tests_personal.test_workflows import Resource


@pytest.fixture
def active_registry(tmp_path):
    config = load_config(installation(tmp_path))
    now = [1000.0]
    registry = WorkflowRegistry(config, lambda _: Resource(), clock=lambda: now[0])
    owner = AuthPrincipal('owner')
    token = registry.begin(owner, 'app', 'begin')['workflow_id']
    registry.activate(owner, token)
    ref = 'wfr_' + hashlib.sha256(token.encode()).hexdigest()
    yield registry, owner, token, ref, now
    registry.close()


def resume(registry, owner, ref, request='resume-one', generation=0):
    assert hasattr(registry, 'resume'), 'explicit credential resume is missing'
    return registry.resume(owner, ref, request, generation)


def test_rotation_preserves_live_resource_and_revokes_every_old_entrypoint(active_registry):
    registry, owner, old, ref, _ = active_registry
    resource = registry.resources[ref[4:]]
    result = resume(registry, owner, ref)
    assert result['credential_generation'] == 1
    assert result['workflow_id'] != old
    assert result['workflow_ref'] == ref
    assert registry.resources[ref[4:]] is resource
    assert registry._row(owner, result['workflow_id'])['token_hash'] == ref[4:]
    for action in (registry.status, registry.activate, registry.end):
        with pytest.raises(WorkflowError, match='WORKFLOW_CREDENTIAL_REPLACED'):
            action(owner, old)
    with pytest.raises(WorkflowError, match='WORKFLOW_CREDENTIAL_REPLACED'):
        with registry.use(owner, old):
            pytest.fail('revoked credential admitted')
    assert registry.list(owner)['workflows'][0]['credential_generation'] == 1
    assert not resource.closed


def test_lost_response_replays_before_generation_check_and_token_is_encrypted(active_registry):
    registry, owner, old, ref, _ = active_registry
    result = resume(registry, owner, ref)
    assert resume(registry, owner, ref) == result
    assert registry.status(owner, result['workflow_id'])['credential_generation'] == 1
    data = registry.path.read_bytes()
    assert old.encode() not in data
    assert result['workflow_id'].encode() not in data


def test_parallel_same_request_rotates_once(active_registry):
    registry, owner, _, ref, _ = active_registry
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: resume(registry, owner, ref), range(8)))
    assert all(result == results[0] for result in results)
    assert results[0]['credential_generation'] == 1


def test_conflicting_resume_and_wrong_owner_are_rejected(active_registry):
    registry, owner, _, ref, _ = active_registry
    resume(registry, owner, ref)
    with pytest.raises(WorkflowError, match='RESUME_CONFLICT'):
        resume(registry, owner, ref, generation=1)
    with pytest.raises(WorkflowError, match='CREDENTIAL_GENERATION_CONFLICT'):
        resume(registry, owner, ref, request='another')
    with pytest.raises(WorkflowError, match='WORKFLOW_DENIED'):
        resume(registry, AuthPrincipal('stranger'), ref)


def test_expired_response_tombstone_never_rotates_again(active_registry):
    registry, owner, _, ref, now = active_registry
    result = resume(registry, owner, ref)
    now[0] += 301
    with pytest.raises(WorkflowError, match='RESUME_RESPONSE_EXPIRED'):
        resume(registry, owner, ref)
    assert registry.status(owner, result['workflow_id'])['credential_generation'] == 1


def test_replaced_cached_response_never_returns_a_revoked_token(active_registry):
    registry, owner, _, ref, _ = active_registry
    resume(registry, owner, ref)
    current = resume(registry, owner, ref, request='second', generation=1)
    with pytest.raises(WorkflowError, match='RESUME_RESPONSE_REPLACED'):
        resume(registry, owner, ref)
    assert registry.status(owner, current['workflow_id'])['credential_generation'] == 2


def test_restart_migration_does_not_resurrect_legacy_credential(active_registry):
    registry, owner, old, ref, _ = active_registry
    result = resume(registry, owner, ref)
    restarted = WorkflowRegistry(registry.config, lambda _: pytest.fail('must not restart resources'))
    try:
        with pytest.raises(WorkflowError, match='WORKFLOW_CREDENTIAL_REPLACED'):
            restarted.status(owner, old)
        assert restarted.status(owner, result['workflow_id'])['state'] == 'CLEANUP_BLOCKED'
    finally:
        restarted.close()


def test_reserved_workflow_cannot_be_resumed(active_registry):
    registry, owner, token, _, _ = active_registry
    registry.end(owner, token)
    token = registry.begin(owner, 'app', 'new')['workflow_id']
    ref = 'wfr_' + hashlib.sha256(token.encode()).hexdigest()
    with pytest.raises(WorkflowError, match='WORKFLOW_INACTIVE'):
        resume(registry, owner, ref)


def test_legacy_rows_receive_revocable_generation_zero_credentials(tmp_path):
    config = load_config(installation(tmp_path))
    config.data_root.mkdir()
    old = 'wf_legacy-test-capability'
    key = hashlib.sha256(old.encode()).hexdigest()
    with sqlite3.connect(config.data_root / 'workflows.sqlite3') as db:
        db.execute('CREATE TABLE workflows (token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, '
            'request_id TEXT NOT NULL, digest TEXT NOT NULL, project TEXT NOT NULL, '
            'access TEXT NOT NULL, state TEXT NOT NULL, expires REAL NOT NULL, '
            'created REAL NOT NULL, error TEXT, UNIQUE(owner, request_id))')
        db.execute('INSERT INTO workflows VALUES (?,?,?,?,?,?,?,?,?,NULL)',
            (key, 'owner', 'legacy', 'digest', str(config.project('app')), 'write', 'RESERVED', 2000, 1000))
    registry = WorkflowRegistry(config, lambda _: Resource(), clock=lambda: 1000)
    owner = AuthPrincipal('owner')
    try:
        registry.activate(owner, old)
        result = resume(registry, owner, 'wfr_' + key)
        with pytest.raises(WorkflowError, match='WORKFLOW_CREDENTIAL_REPLACED'):
            registry.status(owner, old)
        assert registry._row(owner, result['workflow_id'])['token_hash'] == key
    finally:
        registry.close()


def test_response_protection_failure_does_not_revoke_the_current_credential(active_registry, monkeypatch):
    import personal_mcp.workflows as workflows
    registry, owner, old, ref, _ = active_registry

    def unavailable(*args):
        raise OSError('fixture-protection-failure')

    monkeypatch.setattr(workflows, 'protect_response', unavailable)
    with pytest.raises(WorkflowError, match='CREDENTIAL_PROTECTION_FAILED'):
        resume(registry, owner, ref)
    assert registry.status(owner, old)['credential_generation'] == 0


def test_cached_response_corruption_cannot_rotate_or_leak_ciphertext(active_registry):
    registry, owner, _, ref, _ = active_registry
    result = resume(registry, owner, ref)
    with registry._db() as db:
        db.execute('UPDATE workflow_resume_responses SET response=?', (b'invalid-protected-response',))
    with pytest.raises(WorkflowError, match='RESUME_RESPONSE_UNAVAILABLE'):
        resume(registry, owner, ref)
    assert registry.status(owner, result['workflow_id'])['credential_generation'] == 1
