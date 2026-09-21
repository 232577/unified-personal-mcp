import concurrent.futures
import importlib
import json
from pathlib import Path

import pytest


def operation_store(path):
    assert Path('bf_automation/browser/operation_store.py').is_file(), 'operation journal missing'
    return importlib.import_module('bf_automation.browser.operation_store').OperationStore(path)


def test_duplicate_request_is_reserved_once_and_arguments_are_not_logged(tmp_path):
    store = operation_store(tmp_path / 'operations.sqlite3')
    args = {'url': 'http://fixture/app?token=DO_NOT_LOG'}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.reserve('task-a', 'request-1', 'navigate', args), range(8)))
    assert sum(created for _, created in results) == 1
    with pytest.raises(ValueError, match='REQUEST_ID_CONFLICT'):
        store.reserve('task-a', 'request-1', 'navigate', {'url': 'other'})
    assert store.get('task-b', 'request-1') is None
    assert b'DO_NOT_LOG' not in (tmp_path / 'operations.sqlite3').read_bytes()


def test_recovery_does_not_replay_dispatched_calls(tmp_path):
    store = operation_store(tmp_path / 'operations.sqlite3')
    store.reserve('a', 'done', 'navigate', {})
    store.dispatch('a', 'done')
    store.finish('a', 'done', 'completed', {'page_id': 'page'})
    store.reserve('a', 'uncertain', 'navigate', {})
    store.dispatch('a', 'uncertain')
    store.reserve('a', 'queued', 'navigate', {})
    store.recover()
    assert store.get('a', 'done')['state'] == 'completed'
    assert store.get('a', 'uncertain')['state'] == 'unknown'
    assert store.get('a', 'queued')['state'] == 'failed'
    _, new = store.reserve('a', 'uncertain', 'navigate', {})
    assert not new
    with pytest.raises(ValueError):
        store.dispatch('a', 'uncertain')


@pytest.mark.parametrize('request_id', ['', '../x', 'x' * 129, 'with spaces'])
def test_invalid_request_id_never_writes(tmp_path, request_id):
    store = operation_store(tmp_path / 'operations.sqlite3')
    with pytest.raises(ValueError):
        store.reserve('a', request_id, 'navigate', {})


def test_unknown_has_no_claim_of_verification(tmp_path):
    store = operation_store(tmp_path / 'operations.sqlite3')
    store.reserve('a', 'r', 'navigate', {})
    store.dispatch('a', 'r')
    store.finish('a', 'r', 'unknown', None)
    assert store.get('a', 'r')['verification'] == 'unknown'
    assert 'token' not in json.dumps(store.get('a', 'r'))
