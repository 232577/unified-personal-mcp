import json
from tests_bf.support import browser_config
from pathlib import Path
from types import SimpleNamespace

from bf_automation.browser.manager import BrowserManager
from bf_automation.task_store import TaskStore

ROOT = Path(__file__).resolve().parents[2]


class FakeWorker:
    instances = []

    def __init__(self, *_args, **_kwargs):
        self.pid = 33000 + len(type(self).instances)
        self.boot_args = None
        self.events = []
        type(self).instances.append(self)

    def call(self, operation, arguments):
        self.events.append(operation)
        if operation == 'boot':
            self.boot_args = arguments
            assert arguments['engine_mode'] == 'webview2'
            assert arguments['connection']['endpoint'] == 'http://127.0.0.1:53123'
            return {
                'engine': 'webview2', 'version': '153.0.4234.32',
                'mode': 'hybrid', 'ownership': 'managed',
                'worker_pid': self.pid, 'console_visible': False,
            }
        if operation == 'health':
            return {'alive': True}
        if operation == 'close':
            return {'closed': True}
        raise AssertionError(operation)

    def close(self):
        self.events.append('worker-close')
        return {'released': True, 'remaining': [], 'deadline_exceeded': False}


class FakeHybridManager:
    def __init__(self):
        self.prepared = []
        self.released = []
        self.events = []

    def end_task(self, token):
        self.events.append('end-task')

    def prepare_managed(self, token, profile):
        self.events.append('prepare')
        self.prepared.append((token, profile))
        return SimpleNamespace(
            hybrid_instance_id='bfh_' + '1' * 32,
            shell_window_id='bfw_' + '2' * 24,
            public_identity=lambda: {
                'hybrid_instance_id': 'bfh_' + '1' * 32,
                'ownership': 'managed',
                'shell_window_id': 'bfw_' + '2' * 24,
            },
        )

    def connection_spec(self, token, hybrid_instance_id):
        self.events.append('connection')
        assert hybrid_instance_id == 'bfh_' + '1' * 32
        return {
            'endpoint': 'http://127.0.0.1:53123',
            'hybrid_instance_id': hybrid_instance_id,
            'engine_version': '153.0.4234.32',
            'ownership': 'managed',
        }

    def release(self, token, hybrid_instance_id):
        self.events.append('release')
        self.released.append((token, hybrid_instance_id))
        return {
            'hybrid_instance_id': hybrid_instance_id, 'ownership': 'managed',
            'process_terminated': True, 'remaining': [],
        }


def write_hybrid_profile(apps, project):
    executable = project / 'fixture.exe'
    executable.write_bytes(b'fixture')
    row = {
        'version': 1, 'id': 'u4a-fixture', 'kind': 'hybrid-webview2',
        'adapter': 'hybrid-webview2', 'project_root': str(project),
        'launcher': {
            'executable': str(executable), 'args': [], 'cwd': str(project),
            'reuse_existing': False,
        },
        'window': {'title_contains': 'U4A Fixture'},
        'hybrid': {
            'engine': 'webview2', 'ownership': 'managed',
            'debug_mode': 'managed-loopback', 'max_instances': 2,
        },
        'web': {
            'navigation_origins': ['http://127.0.0.1:65533'],
            'resource_origins': ['http://127.0.0.1:65533'],
            'upload_roots': [],
        },
    }
    apps.mkdir()
    (apps / 'u4a-fixture.json').write_text(json.dumps(row), encoding='utf-8')


def make_manager(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    apps = tmp_path / 'apps'
    write_hybrid_profile(apps, project)
    store = TaskStore(tmp_path / 'state', allowed_root=tmp_path)
    config = browser_config(tmp_path)
    config_path = tmp_path / 'browser.json'
    config_path.write_text(json.dumps(config), encoding='utf-8')
    hybrid = FakeHybridManager()
    FakeWorker.instances = []
    monkeypatch.setattr('bf_automation.browser.manager.WorkerProcess', FakeWorker)
    manager = BrowserManager(
        store, config_path, apps, source_root=ROOT, hybrid_manager=hybrid,
    )
    return project, store, manager, hybrid


def test_hybrid_start_boots_webview_worker_and_returns_only_public_identity(tmp_path, monkeypatch):
    project, store, manager, hybrid = make_manager(tmp_path, monkeypatch)
    token = store.begin(project)['bf_task_id']
    try:
        operation = manager.start(token, 'u4a-fixture', 'hybrid-start')
        assert operation['state'] == 'completed'
        session = operation['result']['session']
        assert session['engine'] == 'webview2'
        assert session['mode'] == 'hybrid'
        assert session['ownership'] == 'managed'
        assert session['hybrid_instance_id'] == 'bfh_' + '1' * 32
        assert session['shell_window_id'] == 'bfw_' + '2' * 24
        assert 'endpoint' not in session and 'user_data_dir' not in session
        worker = FakeWorker.instances[0]
        assert worker.boot_args['engine_mode'] == 'webview2'
        assert worker.boot_args['connection']['hybrid_instance_id'] == session['hybrid_instance_id']
        assert hybrid.events[:2] == ['prepare', 'connection']
    finally:
        store.end(token)
        manager.shutdown()


def test_hybrid_close_disconnects_worker_before_releasing_shell(tmp_path, monkeypatch):
    project, store, manager, hybrid = make_manager(tmp_path, monkeypatch)
    token = store.begin(project)['bf_task_id']
    operation = manager.start(token, 'u4a-fixture', 'start')
    session_id = operation['result']['session']['session_id']
    worker = FakeWorker.instances[0]
    closed = manager.request(token, session_id, 'close', 'close', {})
    assert closed['state'] == 'completed'
    assert worker.events[-1] == 'worker-close'
    assert hybrid.events[-1] == 'release'
    assert hybrid.released == [(token, 'bfh_' + '1' * 32)]
    store.end(token)
    manager.shutdown()


def test_hybrid_profile_is_refused_when_adapter_is_not_installed(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    apps = tmp_path / 'apps'
    write_hybrid_profile(apps, project)
    store = TaskStore(tmp_path / 'state', allowed_root=tmp_path)
    config_path = tmp_path / 'browser.json'
    config_path.write_text(json.dumps(browser_config(tmp_path)), encoding='utf-8')
    manager = BrowserManager(store, config_path, apps, source_root=ROOT, hybrid_manager=None)
    token = store.begin(project)['bf_task_id']
    try:
        try:
            manager.start(token, 'u4a-fixture', 'start')
        except RuntimeError as exc:
            assert str(exc) == 'HYBRID_ADAPTER_DISABLED'
        else:
            raise AssertionError('hybrid start unexpectedly succeeded without adapter')
    finally:
        store.end(token)
        manager.shutdown()
