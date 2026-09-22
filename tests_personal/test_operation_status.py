from concurrent.futures import Future
import json
import sqlite3

import pytest

from personal_mcp.operations import OperationJournal


def test_legacy_completed_results_receive_migration_retention_grace(tmp_path):
    path = tmp_path / 'operations.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE operations (owner TEXT, request_id TEXT, digest TEXT NOT NULL, '
                   'state TEXT NOT NULL, result TEXT, PRIMARY KEY(owner, request_id))')
        db.execute('INSERT INTO operations VALUES (?,?,?,?,?)',
                   ('owner', 'legacy', 'digest', 'completed', json.dumps({'ok': True})))
    journal = OperationJournal(path, clock=lambda: 1000000)
    assert journal.status('owner', 'legacy')['result'] == {'ok': True}
    assert journal.status('owner', 'legacy')['updated'] == 1000000


def test_late_future_result_is_queryable_without_replay(tmp_path):
    journal = OperationJournal(tmp_path / 'operations.sqlite3')
    future, calls = Future(), []

    def dispatch():
        calls.append(1)
        return future

    with pytest.raises(TimeoutError, match='OPERATION_RUNNING'):
        journal.run('owner', 'request', 'write', {}, dispatch, timeout=0)
    assert journal.status('owner', 'request')['state'] == 'running'
    future.set_result({'ok': True, 'value': 'saved'})
    assert journal.status('owner', 'request')['result']['value'] == 'saved'
    assert journal.run('owner', 'request', 'write', {}, dispatch)['value'] == 'saved'
    assert calls == [1]
    with pytest.raises(ValueError, match='OPERATION_NOT_FOUND'):
        journal.status('other-owner', 'request')


def test_completed_future_cannot_be_overwritten_by_wait_deadline(tmp_path):
    journal = OperationJournal(tmp_path / 'operations.sqlite3')
    for i in range(20):
        future = Future()
        future.set_result({'ok': True, 'number': i})
        assert journal.run('owner', str(i), 'write', {}, lambda: future, timeout=0)['number'] == i
        assert journal.status('owner', str(i))['state'] == 'completed'


def test_restart_unknown_and_error_outcomes_are_not_replayed(tmp_path):
    path = tmp_path / 'operations.sqlite3'
    journal, future = OperationJournal(path), Future()
    with pytest.raises(TimeoutError):
        journal.run('owner', 'request', 'write', {}, lambda: future, timeout=0)
    restarted = OperationJournal(path)
    assert restarted.status('owner', 'request')['state'] == 'unknown'
    with pytest.raises(TimeoutError, match='OUTCOME_UNKNOWN'):
        restarted.run('owner', 'request', 'write', {}, lambda: pytest.fail('replayed'))


def test_oversized_result_keeps_truthful_completed_summary(tmp_path):
    journal = OperationJournal(tmp_path / 'operations.sqlite3', max_result_bytes=512)
    result = journal.run('owner', 'big', 'write', {}, lambda: {'text': 'x' * 4096})
    assert result['structuredContent']['result_truncated']
    status = journal.status('owner', 'big')
    assert status['state'] == 'completed' and status['result_ref']
    assert status['result']['structuredContent']['serialized_bytes'] > 4096


def test_retention_preserves_dedup_tombstone_and_capacity_refuses_before_dispatch(tmp_path):
    now = [1000.0]
    journal = OperationJournal(tmp_path / 'operations.sqlite3', clock=lambda: now[0],
                               retention_seconds=10, max_records=2)
    journal.run('owner', 'one', 'write', {}, lambda: {'ok': True})
    now[0] += 11
    assert journal.status('owner', 'one')['result_expired']
    with pytest.raises(ValueError, match='RESULT_EXPIRED'):
        journal.run('owner', 'one', 'write', {}, lambda: pytest.fail('replayed'))
    journal.run('owner', 'two', 'write', {}, lambda: {'ok': True})
    with pytest.raises(ValueError, match='OPERATION_CAPACITY'):
        journal.run('owner', 'three', 'write', {}, lambda: pytest.fail('dispatched'))


def test_failed_future_is_unknown_without_exception_text_leak(tmp_path):
    journal, future = OperationJournal(tmp_path / 'operations.sqlite3'), Future()
    with pytest.raises(TimeoutError):
        journal.run('owner', 'one', 'write', {}, lambda: future, timeout=0)
    future.set_exception(RuntimeError('secret credential value'))
    result = journal.status('owner', 'one')
    assert result['state'] == 'unknown'
    assert 'secret' not in str(result)
