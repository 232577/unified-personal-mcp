"""A pending effect fences new writes, not previously completed receipts."""

from concurrent.futures import Future
from contextlib import contextmanager

import pytest

from personal_mcp.operations import OperationJournal
from tests_personal.test_host import call, host as host, workflow


@contextmanager
def pending_native_work(host):
    resource = next(iter(host.registry.resources.values()))
    pending = Future()
    pending.set_running_or_notify_cancel()
    with host.bf.lock:
        host.bf.pending[resource.bf_token] = {pending}
    try:
        yield
    finally:
        pending.set_result(None)
        with host.bf.lock:
            host.bf.pending.pop(resource.bf_token, None)


def test_completed_command_receipt_survives_unrelated_pending_native_work(host):
    token = workflow(host)
    args = dict(workflow_id=token, request_id='original-result',
                cmd='echo receipt-isolation', yield_time_ms=1000)
    original = call(host, 'exec_command', **args)
    assert original['structuredContent']['exit_code'] == 0
    with pending_native_work(host):
        assert call(host, 'exec_command', **args) == original
        blocked = call(host, 'exec_command', **{**args, 'request_id': 'new-write'})
        assert blocked['structuredContent']['error']['code'] == 'WORKFLOW_WRITE_PENDING'
        absent = call(host, 'OperationStatus', workflow_id=token, request_id='new-write')
        assert absent['structuredContent']['error']['code'] == 'OPERATION_NOT_FOUND'
    resource = next(iter(host.registry.resources.values()))
    assert len(resource.coding.command_inventory()) == 1


def test_cached_failed_patch_is_not_masked_by_pending_native_work(host):
    token = workflow(host)
    args = dict(workflow_id=token, request_id='failed-patch',
                patch='*** Begin Patch\n*** Update File: missing.txt\n@@\n-old\n+new\n*** End Patch')
    rejected = call(host, 'apply_patch', **args)
    assert rejected['isError']
    with pending_native_work(host):
        assert call(host, 'apply_patch', **args) == rejected
        assert call(host, 'read_file', workflow_id=token, path='note.txt')['structuredContent']['content'] == 'hello unified'


def test_receipt_argument_conflict_is_preserved_during_pending_work(host):
    token = workflow(host)
    args = dict(workflow_id=token, request_id='fixed-request', cmd='echo first', yield_time_ms=1000)
    assert call(host, 'exec_command', **args)['structuredContent']['exit_code'] == 0
    with pending_native_work(host):
        conflicted = call(host, 'exec_command', **{**args, 'cmd': 'echo second'})
        assert conflicted['structuredContent']['error']['code'] == 'REQUEST_CONFLICT'


def test_journal_admission_runs_only_for_new_operations_and_before_reserving(tmp_path):
    journal = OperationJournal(tmp_path / 'operations.sqlite3')
    executions = []

    def execute():
        executions.append('executed')
        return {'ok': True, 'value': 7}

    def refuse():
        raise PermissionError('admission-denied')

    first = journal.run('owner', 'first', 'fixture', {}, execute)
    # This hook only inspects already-held workflow resource protection. It must
    # never be invoked for receipt retrieval or turn refusal into unknown work.
    assert journal.run('owner', 'first', 'fixture', {}, execute, before_dispatch=refuse) == first
    with pytest.raises(PermissionError, match='admission-denied'):
        journal.run('owner', 'new', 'fixture', {}, execute, before_dispatch=refuse)
    with pytest.raises(ValueError, match='OPERATION_NOT_FOUND'):
        journal.status('owner', 'new')
    assert executions == ['executed']
