import concurrent.futures
import importlib
import json
from pathlib import Path

import psutil
import pytest

from bf_automation.task_store import TaskStore

from tests_bf.browser.fixtures.web_app import WebFixture
from tests_bf.support import browser_config

ROOT = Path(__file__).resolve().parents[2]


def manager_for(tmp_path):
    assert (ROOT / 'bf_automation/browser/manager.py').is_file(), 'browser manager missing'
    mod = importlib.import_module('bf_automation.browser.manager')
    project = tmp_path / 'project'
    project.mkdir()
    store = TaskStore(tmp_path / 'state', allowed_root=tmp_path)
    config_path = tmp_path / 'browser.json'
    config_path.write_text(json.dumps(browser_config(tmp_path)), encoding='utf-8')
    manager = mod.BrowserManager(store, config_path, tmp_path / 'apps', source_root=ROOT)
    return store, manager, project


def register(manager, project, site):
    manager.apps_dir.mkdir(exist_ok=True)
    (manager.apps_dir / 'fixture.json').write_text(json.dumps({
        'version': 1, 'id': 'fixture', 'kind': 'web', 'project_root': str(project),
        'entry_url': site.origin + '/app', 'navigation_origins': [site.origin],
        'resource_origins': [site.origin],
    }), encoding='utf-8')


def test_two_real_workers_are_private_deduplicated_and_cleanup_on_task_end(tmp_path):
    store, m, project = manager_for(tmp_path)
    with WebFixture() as site:
        register(m, project, site)
        a, b = [store.begin(project)['bf_task_id'] for _ in range(2)]
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                created = list(pool.map(lambda t: m.start(t, 'fixture', 'start'), [a, b]))
            sa, sb = [c['result']['session']['session_id'] for c in created]
            assert sa != sb
            assert m.start(a, 'fixture', 'start') == created[0]
            assert all(not c['result']['session']['console_visible'] for c in created)
            pa = m.request(a, sa, 'new_page', 'page-a', {})['result']['page_id']
            pb = m.request(b, sb, 'new_page', 'page-b', {})['result']['page_id']
            nav = m.request(a, sa, 'navigate', 'nav-a', {'page_id': pa, 'url': site.origin + '/app?label=A'})
            count = site.total_requests()
            assert m.request(a, sa, 'navigate', 'nav-a', {'page_id': pa, 'url': site.origin + '/app?label=A'}) == nav
            assert site.total_requests() == count
            assert m.request(b, sb, 'navigate', 'nav-b', {'page_id': pb, 'url': site.origin + '/app?label=B'})['state'] == 'completed'
            with pytest.raises(PermissionError):
                m.read(b, sa, 'snapshot', {'page_id': pa})
            pid_a = created[0]['result']['session']['worker_pid']
            store.end(a)
            assert not psutil.pid_exists(pid_a)
            assert m.read(b, sb, 'snapshot', {'page_id': pb})['title'] == 'BF Browser B'
            assert m.operation_status(b, 'nav-a') is None
            store.end(b)
            assert not m.sessions
        finally:
            m.shutdown()


def test_project_mismatch_denied_without_starting_worker(tmp_path):
    store, m, project = manager_for(tmp_path)
    other = tmp_path / 'other'
    other.mkdir()
    with WebFixture() as site:
        register(m, project, site)
        token = store.begin(other)['bf_task_id']
        with pytest.raises(PermissionError):
            m.start(token, 'fixture', 'start')
        assert not m.sessions
        store.end(token)
    m.shutdown()


def test_status_does_not_report_a_crashed_worker_as_active(tmp_path):
    store, manager, project = manager_for(tmp_path)
    with WebFixture() as site:
        register(manager, project, site)
        token = store.begin(project)['bf_task_id']
        try:
            info = manager.start(token, 'fixture', 'start')['result']['session']
            worker = psutil.Process(info['worker_pid'])
            worker.terminate()
            worker.wait(timeout=5)
            with pytest.raises(LookupError, match='WORKER'):
                manager.read(token, info['session_id'], 'status')
            assert not manager.sessions
        finally:
            store.end(token)
            manager.shutdown()
