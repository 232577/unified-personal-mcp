"""Observe owned commands without a write/poll operation or output consumption."""

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from jsonschema import Draft202012Validator

from tests_personal.test_host import call, host as host, workflow


def observe(host, token, command_id, **kwargs):
    assert 'command_status' in host.catalog, 'independent command observation is missing'
    return call(host, 'command_status', workflow_id=token, command_id=command_id, **kwargs)


def launch(host, token, code='import time; time.sleep(20)', request='launch', wait=0):
    result = call(host, 'exec_command', workflow_id=token, request_id=request,
                  cmd=subprocess.list2cmdline([sys.executable, '-c', code]),
                  yield_time_ms=wait, timeout_ms=30000)
    assert not result.get('isError'), result
    return result['structuredContent']['command_id']


def test_command_status_has_a_narrow_readonly_contract(host):
    assert 'command_status' in host.catalog
    tool = host.catalog['command_status']
    assert tool['annotations'] == dict(readOnlyHint=True, destructiveHint=False,
                                      idempotentHint=True, openWorldHint=False,
                                      title='Observe an owned command')
    assert set(tool['inputSchema']['properties']) == {'workflow_id', 'command_id'}
    assert tool['inputSchema']['additionalProperties'] is False


@pytest.mark.parametrize('exit_code', [0, 7])
def test_reports_actual_exit_code_not_business_success(host, exit_code):
    token = workflow(host)
    command_id = launch(host, token, f'raise SystemExit({exit_code})', wait=1000)
    result = observe(host, token, command_id)
    assert result['isError'] is False
    value = result['structuredContent']
    assert value['observation'] == 'available'
    assert value['status'] == 'exited' and value['exit_code'] == exit_code
    assert value['stdout_ref'] == f'command:{command_id}:stdout'
    Draft202012Validator(host.catalog['command_status']['outputSchema']).validate(value)


def test_observation_preserves_output_cursor_lease_and_operation_journal(host, monkeypatch):
    token = workflow(host)
    command_id = launch(host, token, 'import time; print("PRIVATE-OUTPUT", flush=True); time.sleep(20)')
    resource = next(iter(host.registry.resources.values()))
    command = resource.coding.commands[command_id]
    before = (command.stdout_cursor, command.stderr_cursor)
    updated = host.registry.status(host.principal, token)['last_activity']
    host.registry.clock = lambda: updated + 12
    with host.operations._db() as db:
        receipts = db.execute('SELECT * FROM operations').fetchall()
    monkeypatch.setattr(command, 'write_input', lambda *_: pytest.fail('observation wrote stdin'))
    for _ in range(3):
        result = observe(host, token, command_id)
        assert result['structuredContent']['status'] == 'running'
        assert result['structuredContent']['exit_code'] is None
        assert 'PRIVATE-OUTPUT' not in json.dumps(result) and token not in json.dumps(result)
    assert (command.stdout_cursor, command.stderr_cursor) == before
    assert host.registry.status(host.principal, token)['last_activity'] == updated
    with host.operations._db() as db:
        assert db.execute('SELECT * FROM operations').fetchall() == receipts


def test_observation_does_not_wait_behind_a_workflow_write(host):
    token = workflow(host)
    command_id = launch(host, token)
    entered, release = threading.Event(), threading.Event()

    def write():
        with host.registry.use(host.principal, token, write=True):
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(2) as pool:
        busy = pool.submit(write)
        assert entered.wait(2)
        try:
            value = pool.submit(observe, host, token, command_id).result(1)['structuredContent']
            assert value['observation'] == 'available' and value['status'] == 'running'
        finally:
            release.set()
            busy.result(2)


@pytest.mark.parametrize('lock_name', ['commands_lock', 'output_lock'])
def test_busy_observation_is_unknown_not_false_exit_or_blocking(host, lock_name):
    token = workflow(host)
    command_id = launch(host, token)
    coding = next(iter(host.registry.resources.values())).coding
    lock = coding.commands_lock if lock_name == 'commands_lock' else coding.commands[command_id].lock
    entered, release = threading.Event(), threading.Event()

    def hold():
        with lock:
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(2) as pool:
        holder = pool.submit(hold)
        assert entered.wait(2)
        try:
            result = pool.submit(observe, host, token, command_id).result(1)
            value = result['structuredContent']
            assert result['isError'] is True
            assert value['observation'] == 'unavailable' and value['status'] == 'unknown'
            assert value['exit_code'] is None
            assert value['error']['code'] == 'COMMAND_STATUS_BUSY'
        finally:
            release.set()
            holder.result(2)


def test_foreign_or_missing_handle_never_returns_another_workflows_command(host):
    token = workflow(host)
    command_id = launch(host, token)
    (host.config.workspace_root / 'other').mkdir()
    other = call(host, 'UnifiedTask', action='begin', project_path='other', request_id='other', access='read')
    other_token = other['structuredContent']['workflow_id']
    assert not call(host, 'UnifiedTask', action='activate', workflow_id=other_token)['isError']
    for handle in (command_id, 'missing-handle'):
        value = observe(host, other_token, handle)['structuredContent']
        assert value['ok'] is False and value['status'] == 'unknown'
        assert value['error']['code'] == 'COMMAND_NOT_FOUND'


def test_revoked_credential_remains_rejected(host):
    token = workflow(host)
    command_id = launch(host, token)
    state = host.registry.status(host.principal, token)
    fresh = host.registry.resume(host.principal, state['workflow_ref'], 'resume', state['credential_generation'])
    denied = observe(host, token, command_id)
    assert denied['structuredContent']['error']['code'] == 'WORKFLOW_CREDENTIAL_REPLACED'
    assert observe(host, fresh['workflow_id'], command_id)['structuredContent']['status'] == 'running'


@pytest.mark.parametrize('extra', [{'chars': 'not-allowed'}, {'signal': 'TERM'},
                                 {'request_id': 'not-a-poll'}, {'cmd': 'echo forbidden'}])
def test_query_rejects_input_execution_cancellation_and_poll_fields(host, extra):
    token = workflow(host)
    command_id = launch(host, token)
    value = observe(host, token, command_id, **extra)['structuredContent']
    assert value['error']['code'] == 'INVALID_ARGUMENT'
    assert observe(host, token, command_id)['structuredContent']['status'] == 'running'
