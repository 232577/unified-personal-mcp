"""A slow owned search cleanup must not serialize unrelated project searches."""

import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest

from personal_mcp.search import SearchManager
from tests_personal.test_search import settled


@pytest.mark.parametrize('cleanup', ['release', 'end_owner'])
def test_slow_cleanup_leaves_other_owner_readable(tmp_path, monkeypatch, cleanup):
    (tmp_path / 'note.txt').write_text('needle\n', encoding='utf-8')
    manager = SearchManager()
    entered, proceed = threading.Event(), threading.Event()
    pool = ThreadPoolExecutor(2)
    try:
        first = manager.start('first', tmp_path, 'needle')['search_id']
        other = manager.start('other', tmp_path, 'needle')['search_id']
        settled(manager, 'first', first)
        settled(manager, 'other', other)
        session = manager.sessions[first]
        session.thread.join(5)
        original = session.job.close

        def slow_close():
            entered.set()
            assert proceed.wait(5)
            original()

        monkeypatch.setattr(session.job, 'close', slow_close)
        future = pool.submit(manager.release, 'first', first) if cleanup == 'release' else (
            pool.submit(manager.end_owner, 'first'))
        assert entered.wait(2)
        observation = pool.submit(manager.read, 'other', other)
        try:
            result = observation.result(timeout=1)
        except TimeoutError:
            pytest.fail('unrelated project blocked by slow search cleanup')
        assert result['state'] == 'completed' and result['results'][0]['text'] == 'needle'
        proceed.set()
        future.result(timeout=3)
        assert first not in manager.sessions
        assert manager.read('other', other)['state'] == 'completed'
    finally:
        proceed.set()
        pool.shutdown(wait=True)
        manager.close()


def test_concurrent_release_preserves_other_owner_and_frees_capacity(tmp_path):
    (tmp_path / 'note.txt').write_text('needle\n', encoding='utf-8')
    manager = SearchManager()
    try:
        row = manager.start('first', tmp_path, 'needle')['search_id']
        other = manager.start('other', tmp_path, 'needle')['search_id']
        with ThreadPoolExecutor(3) as pool:
            results = [pool.submit(manager.release, 'first', row) for _ in range(3)]
            for future in results:
                try:
                    assert future.result(timeout=10)['state'] == 'released'
                except PermissionError:
                    pass  # Another release completed before this caller's admission.
        assert row not in manager.sessions
        assert manager.read('other', other)['search_id'] == other
        assert manager.start('first', tmp_path, 'needle')['state'] == 'running'
    finally:
        manager.close()
