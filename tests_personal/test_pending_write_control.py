"""Pending native work keeps its write fence after the caller stops waiting."""

import asyncio
import subprocess
import sys
import threading
import time

import pytest

from tests_personal.test_host import call, host as host, workflow


def short_client_wait(host, monkeypatch):
    original = host.operations.run
    monkeypatch.setattr(host.operations, 'run', lambda *args, **kwargs: original(*args, **kwargs, timeout=0))


@pytest.fixture
def pending(host, monkeypatch):
    token = workflow(host)
    resource = next(iter(host.registry.resources.values()))
    release, entered = threading.Event(), threading.Event()
    calls = []

    async def delayed(generation, name, arguments):
        calls.append((name, arguments['bf_task_id']))
        entered.set()
        await asyncio.to_thread(release.wait, 10)
        return {'content': [], 'structuredContent': {'ok': True}, 'isError': False}

    short_client_wait(host, monkeypatch)
    monkeypatch.setattr(host.bf, '_call', delayed)
    try:
        yield token, resource, release, entered, calls
    finally:
        release.set()
        host.bf.drain(resource.bf_token, timeout=5)


def start_pending(host, pending):
    token, resource, release, entered, calls = pending
    result = call(host, 'Click', workflow_id=token, request_id='first', loc=[1, 1])
    assert result['structuredContent']['error']['code'] == 'OPERATION_RUNNING'
    assert entered.wait(2)
    with host.bf.lock:
        assert any(not future.done() for future in host.bf.pending[resource.bf_token])
    return result


def wait_for_operation(host, token, request_id, expected):
    deadline = time.monotonic() + 3
    while True:
        result = call(host, 'OperationStatus', workflow_id=token, request_id=request_id)['structuredContent']
        if result.get('state') == expected or time.monotonic() > deadline:
            assert result.get('state') == expected, result
            return result
        time.sleep(0.005)


@pytest.mark.parametrize('kind', ['desktop', 'coding'])
def test_pending_bf_write_rejects_new_write_before_dispatch_or_journaling(host, pending, kind):
    token, resource, release, entered, calls = pending
    start_pending(host, pending)
    if kind == 'desktop':
        result = call(host, 'Click', workflow_id=token, request_id='second', loc=[2, 2])
    else:
        result = call(host, 'exec_command', workflow_id=token, request_id='second', cmd='echo guarded')
    assert result['structuredContent']['error']['code'] == 'WORKFLOW_WRITE_PENDING'
    assert calls == [('Click', resource.bf_token)]
    with pytest.raises(ValueError, match='OPERATION_NOT_FOUND'):
        host.operations.status(resource.key, 'second')
    with resource.coding.commands_lock:
        assert not resource.coding.commands


def test_pending_write_keeps_status_poll_cancel_and_other_project_available(host, pending):
    token, resource, release, entered, calls = pending
    command = subprocess.list2cmdline([sys.executable, '-c', 'import time; time.sleep(20)'])
    launched = call(host, 'exec_command', workflow_id=token, request_id='launch',
                    cmd=command, timeout_ms=30000, yield_time_ms=0)
    assert not launched.get('isError'), launched
    assert launched['structuredContent']['status'] == 'running', launched
    command_id = launched['structuredContent']['command_id']
    start_pending(host, pending)
    status = call(host, 'OperationStatus', workflow_id=token, request_id='first')
    assert status['structuredContent']['state'] == 'running'
    workflow_status = call(host, 'UnifiedTask', action='status', workflow_id=token)
    assert command_id in {item['command_id'] for item in workflow_status['structuredContent']['commands']}
    polled = call(host, 'write_stdin', workflow_id=token, request_id='poll',
                  command_id=command_id, chars='', yield_time_ms=0)
    assert not polled.get('isError'), polled
    assert polled['structuredContent']['status'] == 'running', polled
    blocked_input = call(host, 'write_stdin', workflow_id=token, request_id='input',
                         command_id=command_id, chars='data', yield_time_ms=0)
    assert blocked_input['structuredContent']['error']['code'] == 'WORKFLOW_WRITE_PENDING'
    output = call(host, 'read_output', workflow_id=token, output_ref=f'command:{command_id}:stdout')
    assert not output.get('isError'), output
    cancelled = call(host, 'kill_command', workflow_id=token, request_id='cancel', command_id=command_id)
    assert not cancelled.get('isError'), cancelled
    (host.config.workspace_root / 'other').mkdir()
    other = call(host, 'UnifiedTask', action='begin', project_path='other', request_id='other')['structuredContent']['workflow_id']
    assert not call(host, 'UnifiedTask', action='activate', workflow_id=other).get('isError')
    result = call(host, 'exec_command', workflow_id=other, request_id='other-command', cmd='echo independent')
    assert not result.get('isError'), result
    desktop = call(host, 'Click', workflow_id=other, request_id='other-desktop', loc=[3, 3])
    assert desktop['structuredContent']['error']['code'] == 'OPERATION_RUNNING'
    reference = host.registry.status(host.principal, other)['workflow_ref']
    with host.bf.lock:
        other_owner = host.registry.resources[reference[4:]].bf_token
        assert other_owner != resource.bf_token and host.bf.pending[other_owner]
    assert not release.is_set()


def test_actual_completion_releases_write_fence_and_preserves_queryable_result(host, pending):
    token, resource, release, entered, calls = pending
    start_pending(host, pending)
    release.set()
    host.bf.drain(resource.bf_token, timeout=5)
    original = wait_for_operation(host, token, 'first', 'completed')
    assert original['state'] == 'completed' and original['result']['structuredContent']['ok']
    result = call(host, 'exec_command', workflow_id=token, request_id='after', cmd='echo resumed')
    assert not result.get('isError'), result
    assert calls == [('Click', resource.bf_token)]
    call(host, 'Click', workflow_id=token, request_id='after-desktop', loc=[2, 2])
    assert wait_for_operation(host, token, 'after-desktop', 'completed')['result']['structuredContent']['ok']
    assert calls == [('Click', resource.bf_token), ('Click', resource.bf_token)]


def test_unknown_outcome_keeps_native_owner_fence_and_cleanup_until_worker_exits(host, monkeypatch):
    token = workflow(host)
    resource = next(iter(host.registry.resources.values()))
    release, entered = threading.Event(), threading.Event()
    generation = host.bf._generation

    def native_work():
        entered.set()
        release.wait(10)

    async def lose_outcome(generation, name, arguments):
        asyncio.get_running_loop().run_in_executor(None, native_work)
        while not entered.is_set():
            await asyncio.sleep(0.001)
        raise RuntimeError('isolated protocol failure while native work remains')

    short_client_wait(host, monkeypatch)
    monkeypatch.setattr(host.bf, '_call', lose_outcome)
    real_drain = host.bf.drain
    monkeypatch.setattr(host.bf, 'drain', lambda owner, timeout=0.05: real_drain(owner, timeout=timeout))
    try:
        call(host, 'Click', workflow_id=token, request_id='uncertain', loc=[1, 1])
        assert entered.wait(2)
        deadline = time.monotonic() + 3
        while True:
            with host.bf.lock:
                fenced = resource.bf_token in generation.uncertain_owners
            if fenced or time.monotonic() > deadline:
                break
            time.sleep(0.005)
        assert fenced and generation.thread.is_alive()
        with pytest.raises(TimeoutError, match='BF_OPERATIONS_STILL_RUNNING'):
            real_drain(resource.bf_token, timeout=0)
        result = call(host, 'exec_command', workflow_id=token, request_id='unsafe', cmd='echo blocked')
        assert result['structuredContent']['error']['code'] == 'WORKFLOW_WRITE_PENDING'
        wait_for_operation(host, token, 'uncertain', 'unknown')
        ended = call(host, 'UnifiedTask', action='end', workflow_id=token)
        assert ended['structuredContent']['state'] == 'CLEANUP_BLOCKED'
        assert not resource.bf_closed and resource.key in host.registry.resources
        release.set()
        generation.thread.join(5)
        assert not generation.thread.is_alive()
        ended = call(host, 'UnifiedTask', action='end', workflow_id=token)
        assert ended['structuredContent']['state'] == 'ENDED'
    finally:
        release.set()
        generation.thread.join(5)
