"""No false failure after dispatch; cleanup remains observable and cancellable."""

import concurrent.futures
import threading
from types import SimpleNamespace

import pytest
from tests_bf.browser.test_lifecycle import manager_for, register

from bf_automation.browser.manager import Session
from bf_automation.browser.models import WebProfile
from bf_automation.browser.process import WorkerReplyError


def owned_session(tmp_path, worker):
    store, manager, project = manager_for(tmp_path)
    token = store.begin(project)['bf_task_id']
    _, row = store.require(token)
    profile = WebProfile('fixture', 'http://127.0.0.1:65533/app',
                         ('http://127.0.0.1:65533',), ('http://127.0.0.1:65533',), project)
    session = Session('bfb_'+'c'*32, row['task_key'], 'fixture', tmp_path, profile,
                      worker=worker, state='active')
    manager.sessions[session.session_id] = session
    return store, manager, token, session


@pytest.mark.parametrize('error', [WorkerReplyError('BROWSER_OPERATION_FAILED'), ValueError('post-dispatch validation')])
def test_provider_error_after_dispatch_is_unknown_not_safe_to_repeat(tmp_path, error):
    dispatched = []

    def lost(*args):
        dispatched.append(True)
        raise error

    worker = SimpleNamespace(call=lost, close=lambda: {'released': True, 'remaining': [], 'deadline_exceeded': False})
    store, manager, token, session = owned_session(tmp_path, worker)
    try:
        args = {'page_id': 'bfp_'+'d'*32, 'url': 'http://127.0.0.1:65533/app'}
        result = manager.request(token, session.session_id, 'navigate', 'uncertain', args)
        assert result['state'] == 'unknown'
        assert manager.request(token, session.session_id, 'navigate', 'uncertain', args) == result
        assert len(dispatched) == 1
        assert manager.operation_status(token, 'uncertain') == result
    finally:
        store.end(token)
        manager.shutdown()
    assert manager.journal.get(session.owner, 'uncertain')['state'] == 'unknown'


def test_cleanup_failure_keeps_session_available_for_cleanup_retry(tmp_path):
    worker = SimpleNamespace(close=lambda: {'released': False, 'remaining': [123], 'deadline_exceeded': True})
    store, manager, token, session = owned_session(tmp_path, worker)
    try:
        with pytest.raises(RuntimeError, match='CLEANUP'):
            manager._close(session)
        assert manager.sessions.get(session.session_id) is session
    finally:
        worker.close = lambda: {'released': True, 'remaining': [], 'deadline_exceeded': False}
        store.end(token)
        manager.shutdown()


def test_task_end_interrupts_owned_worker_instead_of_waiting_for_request_timeout(tmp_path):
    entered, stop = threading.Event(), threading.Event()

    def wait(*args):
        entered.set()
        stop.wait(4)
        raise EOFError('stopped')

    def close():
        stop.set()
        return {'released': True, 'remaining': [], 'deadline_exceeded': False}

    store, manager, token, session = owned_session(tmp_path, SimpleNamespace(call=wait, close=close))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        try:
            operation = pool.submit(manager.request, token, session.session_id, 'navigate', 'slow',
                                     {'page_id': 'bfp_'+'d'*32, 'url': 'http://127.0.0.1:65533/app'})
            assert entered.wait(1)
            ended = pool.submit(store.end, token)
            assert ended.result(timeout=1)['status'] == 'ended'
            assert operation.result(timeout=1)['state'] == 'unknown'
        finally:
            stop.set()
    manager.shutdown()


def test_start_does_not_reserve_a_session_after_its_task_ended(tmp_path, monkeypatch):
    store, manager, project = manager_for(tmp_path)
    register(manager, project, SimpleNamespace(origin='http://127.0.0.1:65533'))
    token = store.begin(project)['bf_task_id']
    from bf_automation.browser import manager as browser_module
    load_profile = browser_module.load_browser_profile

    def end_during_profile_read(*args):
        profile = load_profile(*args)
        store.end(token)
        return profile

    monkeypatch.setattr(browser_module, 'load_browser_profile', end_during_profile_read)
    try:
        with pytest.raises(PermissionError):
            manager.start(token, 'fixture', 'start-after-end')
        assert not manager.sessions, 'ended task must not consume a browser session slot'
    finally:
        manager.shutdown()
