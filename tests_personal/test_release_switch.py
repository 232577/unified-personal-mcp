"""Release orchestration uses owned task control, not arbitrary process kills."""

from contextlib import contextmanager
from importlib import import_module
from pathlib import Path

import pytest


def switch(backend):
    path = Path(__file__).resolve().parents[1] / 'scripts/switch_managed_release.py'
    assert path.is_file(), 'controlled release switch is missing'
    return import_module('scripts.switch_managed_release').switch_release(backend)


class Backend:
    def __init__(self, failure=None):
        self.failure, self.events, self.locked = failure, [], False

    def event(self, name):
        self.events.append(name)
        if name == self.failure:
            raise RuntimeError(name)

    def preflight(self):
        self.event('preflight')

    def wait_idle(self):
        self.event('idle')

    def task(self):
        self.event('task')
        return {'xml': 'old' if 'install' not in self.events else 'new'}

    def install(self):
        self.event('install')
        return {'xml': 'new'}

    @contextmanager
    def quiescent(self):
        self.event('lock')
        self.locked = True
        try:
            yield
        finally:
            self.locked = False
            self.events.append('unlock')

    def stop(self, expected):
        assert self.locked and expected['xml'] == 'new'
        self.event('stop')

    def wait_stopped(self):
        assert self.locked
        self.event('stopped')

    def start(self, expected):
        assert not self.locked and expected['xml'] == 'new'
        self.event('start')

    def ready(self):
        self.event('ready')
        return {'version': '0.1.12', 'tool_count': 58}

    def rollback(self, previous, installed):
        assert previous['xml'] == 'old' and installed['xml'] == 'new'
        self.events.append('rollback')
        return {'version': '0.1.11', 'tool_count': 50}


def test_switch_stops_only_after_validation_and_atomic_idle_check():
    backend = Backend()
    result = switch(backend)
    assert result['status'] == 'LOCAL_READY'
    assert result['running']['version'] == '0.1.12'
    assert backend.events == ['preflight', 'idle', 'task', 'install', 'task',
                              'idle', 'lock', 'stop', 'stopped', 'unlock', 'start', 'ready']


@pytest.mark.parametrize('failure', ['preflight', 'idle', 'install'])
def test_failure_before_switch_does_not_stop_service(failure):
    backend = Backend(failure)
    with pytest.raises(RuntimeError, match=failure):
        switch(backend)
    assert 'stop' not in backend.events and 'start' not in backend.events


@pytest.mark.parametrize('failure', ['lock', 'stop', 'stopped', 'start', 'ready'])
def test_switch_failure_restores_old_release_and_reports_rollback(failure):
    backend = Backend(failure)
    result = switch(backend)
    assert result['status'] == 'ROLLED_BACK'
    assert result['running']['version'] == '0.1.11'
    assert backend.events[-1] == 'rollback'
    assert backend.events.count('install') == 1


def test_failed_rollback_is_not_reported_as_a_success():
    class FailedRollback(Backend):
        def rollback(self, previous, installed):
            raise RuntimeError('CONCURRENT_TASK_CHANGE')

    with pytest.raises(RuntimeError, match='CONCURRENT_TASK_CHANGE'):
        switch(FailedRollback('ready'))


def test_post_registration_task_read_failure_rolls_back_without_stopping():
    class FailedRead(Backend):
        def task(self):
            if 'install' in self.events:
                raise RuntimeError('STATUS_READ_FAILED')
            return super().task()
    backend = FailedRead()
    result = switch(backend)
    assert result['status'] == 'ROLLED_BACK'
    assert 'stop' not in backend.events and backend.events[-1] == 'rollback'


def test_local_run_needs_no_workflow_reference_and_does_not_remove_task(tmp_path, monkeypatch):
    import json
    import sys
    from types import SimpleNamespace
    from scripts import switch_managed_release as release

    output = tmp_path / 'output'
    output.mkdir()
    backend = SimpleNamespace(output=output, record=lambda *a, **kw: None)
    monkeypatch.setattr(release, 'ManagedRelease', lambda args: backend)
    monkeypatch.setattr(release, 'InstanceLock', lambda name: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(release, 'switch_release', lambda backend: {'status': 'LOCAL_READY'})
    monkeypatch.setattr(release, 'remove_own_task', lambda args: pytest.fail('local run must not delete a task'))
    monkeypatch.setattr(sys, 'argv', ['switch', '--run', '--config', str(tmp_path / 'config'),
        '--candidate', str(tmp_path / 'new'), '--old-bundle', str(tmp_path / 'old'),
        '--output', str(output), '--version', '0.1.12', '--old-version', '0.1.11', '--tools', '58'])
    assert release.main() == 0
    assert json.loads((output / 'result.json').read_text())['temporary_task_removed'] is None


def test_local_run_initialization_failure_still_persists_result(tmp_path, monkeypatch):
    import json
    import sys
    from scripts import switch_managed_release as release
    output = tmp_path / 'output'
    def fail(args):
        raise RuntimeError('CONFIG_UNAVAILABLE')
    monkeypatch.setattr(release, 'ManagedRelease', fail)
    monkeypatch.setattr(release, 'remove_own_task', lambda args: pytest.fail('local run must not delete a task'))
    monkeypatch.setattr(sys, 'argv', ['switch', '--run', '--config', str(tmp_path / 'config'),
        '--candidate', str(tmp_path / 'new'), '--old-bundle', str(tmp_path / 'old'),
        '--output', str(output), '--version', '0.1.12', '--old-version', '0.1.11', '--tools', '58'])
    assert release.main() == 1
    result = json.loads((output / 'result.json').read_text())
    assert result['status'] == 'NOT_COMPLETED' and result['temporary_task_removed'] is None


@pytest.mark.parametrize('message, expected', [
    ('OLD_SERVICE_NOT_VERIFIED', 'OLD_SERVICE_NOT_VERIFIED'),
    ('credential=private-value-must-not-be-published', 'RELEASE_SWITCH_FAILED'),
])
def test_switch_failure_explains_known_refusal_without_exposing_exception_text(tmp_path, monkeypatch, message, expected):
    import json
    import sys
    from scripts import switch_managed_release as release

    def fail(args):
        raise RuntimeError(message)

    monkeypatch.setattr(release, 'ManagedRelease', fail)
    output = tmp_path / 'output'
    monkeypatch.setattr(sys, 'argv', ['switch', '--run', '--config', str(tmp_path / 'config'),
        '--candidate', str(tmp_path / 'new'), '--old-bundle', str(tmp_path / 'old'),
        '--output', str(output), '--version', '0.1.13', '--old-version', '0.1.12', '--tools', '58'])
    assert release.main() == 1
    result = json.loads((output / 'result.json').read_text())
    assert result['error_code'] == expected
    assert 'private-value' not in json.dumps(result)


@pytest.mark.parametrize('rows', [[], [{'workflow_ref': 'wfr_' + 'a' * 64}]])
def test_strict_local_idle_rejects_every_workflow_without_an_allowed_reference(rows):
    from types import SimpleNamespace
    from scripts.switch_managed_release import ManagedRelease
    backend = object.__new__(ManagedRelease)
    backend.args = SimpleNamespace(allow_workflow_ref=None)
    usage = {name: 0 for name in ('inflight', 'commands', 'coding_job_active_processes',
        'browser_sessions', 'webview2_instances', 'search_sessions', 'resources')}
    usage['stale'] = False
    backend.rpc = lambda name, arguments: {'workflows': rows} if name == 'UnifiedTask' else {'resource_usage': usage}
    if rows:
        with pytest.raises(RuntimeError, match='OTHER_WORKFLOW_PRESENT'):
            backend.wait_idle()
    else:
        backend.wait_idle()


@pytest.mark.parametrize('reference', [None, '', 'fake-reference'])
def test_arm_rejects_missing_or_invalid_workflow_reference_before_any_task_action(tmp_path, monkeypatch, reference):
    import sys
    from scripts import switch_managed_release as release
    argv = ['switch', '--arm', '--config', str(tmp_path / 'config'), '--candidate', str(tmp_path / 'new'),
        '--old-bundle', str(tmp_path / 'old'), '--output', str(tmp_path / 'output'),
        '--version', '0.1.12', '--old-version', '0.1.11', '--tools', '58']
    if reference is not None:
        argv += ['--allow-workflow-ref', reference]
    monkeypatch.setattr(sys, 'argv', argv)
    monkeypatch.setattr(release, 'arm', lambda args: pytest.fail('invalid arm must not create task'))
    with pytest.raises(SystemExit) as failure:
        release.main()
    assert failure.value.code == 2


def test_rollback_preserves_external_update_during_stop_wait(monkeypatch):
    from types import SimpleNamespace
    from personal_mcp.autostart import WindowsTaskScheduler
    from scripts.switch_managed_release import ManagedRelease
    scheduler = WindowsTaskScheduler()
    current = {'xml': 'installed', 'bundle': 'old'}
    writes = []
    folder = SimpleNamespace(GetTask=lambda name: SimpleNamespace(Xml=current['xml']),
        RegisterTask=lambda *args: writes.append(args))
    @contextmanager
    def fake_folder():
        yield None, folder
    monkeypatch.setattr(scheduler, '_folder', fake_folder)
    monkeypatch.setattr(scheduler, '_sid', lambda: 'fixture-user')
    @contextmanager
    def gate():
        yield
    backend = object.__new__(ManagedRelease)
    backend.manager = SimpleNamespace(scheduler=scheduler, task_name='fixture',
        _service_status=lambda: {'running': True, 'running_version': '0.1.12'})
    backend.args = SimpleNamespace(old_version='0.1.11', old_bundle=Path('old'))
    backend.record = lambda *a, **kw: None
    backend.task = lambda: dict(current)
    backend.quiescent = gate
    backend.stop = lambda expected: None
    backend.wait_stopped = lambda: current.update(xml='external')
    backend.start = lambda expected: None
    backend.ready = lambda **kwargs: {'version': '0.1.11'}
    with pytest.raises(RuntimeError, match='CONCURRENT_TASK_CHANGE'):
        backend.rollback({'exists': True, 'xml': 'old'}, {'xml': 'installed'})
    assert not writes and current['xml'] == 'external'


def test_database_gate_rejects_other_workflows_and_blocks_new_reservations(tmp_path):
    import sqlite3
    from types import SimpleNamespace
    from scripts.switch_managed_release import ManagedRelease

    path = tmp_path / 'workflows.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE workflows(state TEXT NOT NULL)')
        db.execute("INSERT INTO workflows VALUES('ACTIVE')")
    backend = object.__new__(ManagedRelease)
    backend.manager = SimpleNamespace(config=SimpleNamespace(data_root=tmp_path))
    with pytest.raises(RuntimeError, match='WORKFLOW_APPEARED_BEFORE_STOP'):
        with backend.quiescent():
            pytest.fail('active workflows must not be stopped')
    with sqlite3.connect(path) as db:
        db.execute("UPDATE workflows SET state='ENDED'")
    with backend.quiescent():
        with sqlite3.connect(path, timeout=0.01) as other:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                other.execute("INSERT INTO workflows VALUES('RESERVED')")
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM workflows').fetchall() == [('ENDED',)]


@pytest.mark.parametrize('hresult,nested,missing', [
    (-2147024894, None, True),
    (-2147352567, (0, None, None, None, 0, -2147024894), True),
    (-2147352567, (0, None, None, None, 0, -2147024891), False),
    (-2147024891, None, False),
    (-2147216625, None, False),
])
def test_task_missing_classification_preserves_access_denied(hresult, nested, missing):
    from types import SimpleNamespace
    from personal_mcp import autostart

    classify = getattr(autostart, 'task_not_found', None)
    assert callable(classify), 'wrapped COM task-not-found is not classified'
    assert classify(SimpleNamespace(hresult=hresult, excepinfo=nested)) is missing


@pytest.mark.parametrize('changed_mode,operation_fails', [(True, False), (True, True),
                                                       (False, False), (False, True)])
def test_com_initialization_only_releases_its_own_successful_reference(monkeypatch, changed_mode, operation_fails):
    import pythoncom
    from personal_mcp.autostart import _com_operation

    released = []
    def initialize_ex(_):
        if changed_mode:
            raise pythoncom.error(-2147417850, 'RPC_E_CHANGED_MODE', None, None)
    # CoInitialize silently accepts CHANGED_MODE; this is the legacy behavior
    # that made an unconditional CoUninitialize release somebody else's COM.
    monkeypatch.setattr(pythoncom, 'CoInitialize', lambda: None)
    monkeypatch.setattr(pythoncom, 'CoInitializeEx', initialize_ex)
    monkeypatch.setattr(pythoncom, 'CoUninitialize', lambda: released.append(True))
    @_com_operation
    def operation():
        if operation_fails:
            raise ValueError('fixture-error')
        return 'ok'
    if operation_fails:
        with pytest.raises(RuntimeError, match='fixture-error'):
            operation()
    else:
        assert operation() == 'ok'
    assert released == ([] if changed_mode else [True])


def test_real_disposable_task_control_checks_xml_and_removes_its_fixture(tmp_path):
    import ctypes
    import json
    import subprocess
    import sys
    import time
    import uuid
    from types import SimpleNamespace

    from personal_mcp.autostart import TASK_DESCRIPTION, WindowsTaskScheduler, _com_operation
    from scripts.switch_managed_release import ManagedRelease

    name = 'UnifiedPersonalMCP-ReleaseFixture-' + uuid.uuid4().hex[:12]
    scheduler = WindowsTaskScheduler()
    backend = object.__new__(ManagedRelease)
    backend.manager = SimpleNamespace(scheduler=scheduler, task_name=name)
    @_com_operation
    def create():
        with scheduler._folder() as (service, folder):
            definition = service.NewTask(0)
            definition.RegistrationInfo.Description = TASK_DESCRIPTION
            sid = scheduler._sid()
            definition.Principal.UserId = sid
            definition.Principal.LogonType = 3
            definition.Principal.RunLevel = 0
            action = definition.Actions.Create(0)
            action.Path = str(Path(sys.executable).with_name('pythonw.exe'))
            action.Arguments = subprocess.list2cmdline(['-c', 'import time; time.sleep(30)'])
            action.WorkingDirectory = str(tmp_path)
            definition.Settings.Enabled = True
            definition.Settings.MultipleInstances = 2
            definition.Settings.ExecutionTimeLimit = 'PT1M'
            definition.Settings.DisallowStartIfOnBatteries = False
            definition.Settings.StopIfGoingOnBatteries = False
            task = folder.RegisterTaskDefinition(name, definition, 2, sid, None, 3)
            apartment, qualifier = ctypes.c_int(), ctypes.c_int()
            ctypes.windll.ole32.CoGetApartmentType(ctypes.byref(apartment), ctypes.byref(qualifier))
            (tmp_path / 'owned-task.json').write_text(json.dumps({
                'name': name, 'apartment': apartment.value, 'qualifier': qualifier.value}), encoding='utf-8')
            assert int(task.Definition.Settings.MultipleInstances) == 2
            return {'xml': str(task.Xml)}

    @_com_operation
    def cleanup():
        with scheduler._folder() as (_, folder):
            fixture = folder.GetTask(name)
            assert fixture.Definition.RegistrationInfo.Description == TASK_DESCRIPTION
            assert Path(fixture.Definition.Actions.Item(1).WorkingDirectory) == tmp_path
            fixture.Stop(0)
            folder.DeleteTask(name, 0)

    # Match production's operation boundaries: never retain a COM proxy across
    # another initialized/uninitialized operation; only JSON metadata crosses.
    expected = create()
    try:
        with pytest.raises(RuntimeError, match='CONCURRENT_TASK_CHANGE'):
            backend._control({'xml': 'wrong'}, 'start')
        assert scheduler.status(name)['state'] == 3
        backend._control(expected, 'start')
        deadline = time.monotonic() + 10
        while scheduler.status(name)['state'] != 4 and time.monotonic() < deadline:
            time.sleep(0.1)
        assert scheduler.status(name)['state'] == 4
        backend._control(expected, 'stop')
        deadline = time.monotonic() + 10
        while scheduler.status(name)['state'] != 3 and time.monotonic() < deadline:
            time.sleep(0.1)
        assert scheduler.status(name)['state'] == 3
    finally:
        cleanup()
    assert scheduler.status(name)['exists'] is False
