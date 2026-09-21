from pathlib import Path

import pytest

from bf_automation.browser.models import WebProfile
from bf_automation.hybrid.models import HybridLaunchProfile, HybridProfile, HybridSettings
from bf_automation.task_store import TaskStore


class FakePlatform:
    def __init__(self, executable):
        from bf_automation.hybrid.process import ProcessIdentity

        self.ProcessIdentity = ProcessIdentity
        self.executable = executable.resolve()
        self.shell = ProcessIdentity(2200, 20.0, self.executable)
        self.webview = ProcessIdentity(2201, 21.0, Path('C:/WebView/msedgewebview2.exe'))
        self.launches = []
        self.terminated = []

    def reserve_loopback_port(self):
        return 53123

    def launch(self, *, executable, args, cwd, env, stdout_path, stderr_path, owner, store):
        self.launches.append({
            'executable': Path(executable), 'args': tuple(args), 'cwd': cwd,
            'env': dict(env), 'stdout_path': Path(stdout_path), 'stderr_path': Path(stderr_path),
        })
        return self.shell

    def wait_ready(self, ready_file, timeout_seconds):
        return {
            'hybrid_instance_id': self.launches[-1]['env']['BF_HYBRID_INSTANCE'],
            'shell_pid': self.shell.pid, 'shell_created': self.shell.create_time,
            'hwnd': 500, 'title': 'U4A Fixture',
            'user_data_dir': self.launches[-1]['env']['WEBVIEW2_USER_DATA_FOLDER'],
            'debug_port': 53123, 'webview_pid': self.webview.pid,
            'webview_created': self.webview.create_time,
            'engine_version': '153.0.4234.32',
        }

    def process_identity(self, pid):
        if pid == self.shell.pid:
            return self.shell
        if pid == self.webview.pid:
            return self.webview
        raise ProcessLookupError(pid)

    def descendants(self, pid):
        assert pid == self.shell.pid
        return (self.webview,)

    def window_pid(self, hwnd):
        assert hwnd == 500
        return self.shell.pid

    def listener_pids(self, port):
        assert port == 53123
        return {self.webview.pid}

    def terminate_tree(self, identity):
        self.terminated.append(identity)
        return {'terminated': [identity.pid, self.webview.pid], 'remaining': []}


class LauncherShimPlatform(FakePlatform):
    def __init__(self, executable):
        super().__init__(executable)
        self.launch_root = self.ProcessIdentity(2199, 19.0, self.executable)

    def launch(self, **kwargs):
        super().launch(**kwargs)
        return self.launch_root

    def process_identity(self, pid):
        if pid == self.launch_root.pid:
            return self.launch_root
        return super().process_identity(pid)

    def descendants(self, pid):
        if pid == self.launch_root.pid:
            return (self.shell, self.webview)
        return super().descendants(pid)


class Resolver:
    def __init__(self, proof=None, window=None):
        self.proof = proof
        self.window = window

    def find_window(self, profile):
        return self.window

    def resolve(self, identity, profile, window_record):
        return self.proof


def make_profile(project, executable, *, ownership='managed'):
    return HybridProfile(
        application_id='u4a-fixture', kind='hybrid-webview2', project_root=project.resolve(),
        launcher=HybridLaunchProfile(
            executable.resolve(), ('--fixture',), project.resolve(), ownership == 'attached',
        ),
        title_contains='U4A Fixture',
        hybrid=HybridSettings(
            'webview2', ownership,
            'managed-loopback' if ownership == 'managed' else 'existing-proven', 2,
        ),
        web=WebProfile(
            'u4a-fixture', 'http://127.0.0.1:65533/',
            ('http://127.0.0.1:65533',), ('http://127.0.0.1:65533',),
            project.resolve(), (),
        ),
    )


def manager_for(tmp_path, *, attached_proof=None):
    from bf_automation.hybrid.manager import HybridManager

    project = tmp_path / 'project'
    project.mkdir()
    executable = project / 'fixture.exe'
    executable.write_bytes(b'fixture')
    store = TaskStore(tmp_path / 'state', allowed_root=tmp_path)
    platform = FakePlatform(executable)
    manager = HybridManager(store, platform=platform, attach_resolver=Resolver(attached_proof))
    return project, executable, store, platform, manager


def test_managed_instance_uses_task_private_environment_and_owner_binding(tmp_path):
    project, executable, store, platform, manager = manager_for(tmp_path)
    token = store.begin(project)['bf_task_id']
    profile = make_profile(project, executable)
    instance = manager.prepare_managed(token, profile)
    task_dir, task = store.require(token)
    assert instance.owner == task['task_key']
    assert instance.directory.parent == task_dir / 'hybrid'
    assert instance.user_data_dir == instance.directory / 'user-data'
    assert instance.debug_port == 53123
    assert instance.shell.matches(platform.shell)
    assert instance.webview.matches(platform.webview)
    assert instance.shell_window_id.startswith('bfw_')
    launch = platform.launches[0]
    assert launch['env']['BF_HYBRID_INSTANCE'] == instance.hybrid_instance_id
    assert launch['env']['BF_HYBRID_READY_FILE'] == str(instance.ready_file)
    assert launch['env']['WEBVIEW2_USER_DATA_FOLDER'] == str(instance.user_data_dir)
    assert launch['env']['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] == '--remote-debugging-port=53123'
    assert set(store.status(token)['owned_pids']) == {2200, 2201}


def test_foreign_task_cannot_read_connection_spec(tmp_path):
    project, executable, store, _, manager = manager_for(tmp_path)
    a = store.begin(project)['bf_task_id']
    b = store.begin(project)['bf_task_id']
    instance = manager.prepare_managed(a, make_profile(project, executable))
    with pytest.raises(PermissionError, match='HYBRID_INSTANCE_NOT_OWNED'):
        manager.connection_spec(b, instance.hybrid_instance_id)
    spec = manager.connection_spec(a, instance.hybrid_instance_id)
    assert spec == {
        'endpoint': 'http://127.0.0.1:53123',
        'hybrid_instance_id': instance.hybrid_instance_id,
        'engine_version': '153.0.4234.32',
        'ownership': 'managed',
    }


def test_third_managed_instance_is_refused_before_launch(tmp_path):
    project, executable, store, platform, manager = manager_for(tmp_path)
    tokens = [store.begin(project)['bf_task_id'] for _ in range(3)]
    profile = make_profile(project, executable)
    manager.prepare_managed(tokens[0], profile)
    manager.prepare_managed(tokens[1], profile)
    with pytest.raises(ValueError, match='HYBRID_INSTANCE_LIMIT_REACHED'):
        manager.prepare_managed(tokens[2], profile)
    assert len(platform.launches) == 2


def test_managed_release_terminates_only_owned_matching_shell(tmp_path):
    project, executable, store, platform, manager = manager_for(tmp_path)
    token = store.begin(project)['bf_task_id']
    instance = manager.prepare_managed(token, make_profile(project, executable))
    result = manager.release(token, instance.hybrid_instance_id)
    assert result['ownership'] == 'managed'
    assert result['process_terminated'] is True
    assert result['remaining'] == []
    assert platform.terminated == [platform.shell]


def test_attached_requires_proven_endpoint_and_release_never_terminates(tmp_path):
    project, executable, store, platform, manager = manager_for(tmp_path)
    token = store.begin(project)['bf_task_id']
    profile = make_profile(project, executable, ownership='attached')
    window_id = store.bind_window(token, hwnd=500, pid=platform.shell.pid, title='U4A Fixture')
    window = {**store.resolve_window(token, window_id), 'window_id': window_id}
    with pytest.raises(RuntimeError, match='DEBUG_CHANNEL_NOT_AVAILABLE'):
        manager.attach_existing(token, profile, window)

    proof = {
        'endpoint': 'http://127.0.0.1:53123',
        'webview_pid': platform.webview.pid,
        'webview_created': platform.webview.create_time,
        'engine_version': '153.0.4234.32',
        'attestation': None,
    }
    manager.attach_resolver = Resolver(proof)
    instance = manager.attach_existing(token, profile, window)
    result = manager.release(token, instance.hybrid_instance_id)
    assert result['ownership'] == 'attached'
    assert result['process_terminated'] is False
    assert platform.terminated == []


def test_prepare_attached_uses_resolver_window_and_does_not_claim_process_ownership(tmp_path):
    project, executable, store, platform, manager = manager_for(tmp_path)
    token = store.begin(project)['bf_task_id']
    profile = make_profile(project, executable, ownership='attached')
    window = {'hwnd': 500, 'pid': platform.shell.pid, 'title': 'U4A Fixture'}
    proof = {
        'endpoint': 'http://127.0.0.1:53123',
        'webview_pid': platform.webview.pid,
        'webview_created': platform.webview.create_time,
        'engine_version': '153.0.4234.32',
        'attestation': None,
    }
    manager.attach_resolver = Resolver(proof, window)

    instance = manager.prepare_attached(token, profile)

    assert instance.ownership == 'attached'
    assert store.status(token)['owned_pids'] == []
    bound = store.resolve_window(token, instance.shell_window_id)
    assert bound['hwnd'] == 500 and bound['pid'] == platform.shell.pid
    result = manager.release(token, instance.hybrid_instance_id)
    assert result['process_terminated'] is False
    assert platform.terminated == []


def test_prepare_attached_without_resolver_window_is_unavailable(tmp_path):
    project, executable, store, _, manager = manager_for(tmp_path)
    token = store.begin(project)['bf_task_id']
    profile = make_profile(project, executable, ownership='attached')
    with pytest.raises(RuntimeError, match='DEBUG_CHANNEL_NOT_AVAILABLE'):
        manager.prepare_attached(token, profile)


def test_task_end_releases_managed_instance(tmp_path):
    project, executable, store, platform, manager = manager_for(tmp_path)
    token = store.begin(project)['bf_task_id']
    manager.prepare_managed(token, make_profile(project, executable))
    store.end(token)
    assert platform.terminated == [platform.shell]
    assert manager.instances == {}


def test_packaged_launcher_may_spawn_the_real_gui_shell(tmp_path):
    from bf_automation.hybrid.manager import HybridManager

    project = tmp_path / 'project'
    project.mkdir()
    executable = project / 'fixture.exe'
    executable.write_bytes(b'fixture')
    store = TaskStore(tmp_path / 'state', allowed_root=tmp_path)
    platform = LauncherShimPlatform(executable)
    manager = HybridManager(store, platform=platform, attach_resolver=Resolver(None))
    token = store.begin(project)['bf_task_id']
    instance = manager.prepare_managed(token, make_profile(project, executable))
    assert instance.launch_root.matches(platform.launch_root)
    assert instance.shell.matches(platform.shell)
    assert instance.shell.pid != instance.launch_root.pid
    assert set(store.status(token)['owned_pids']) == {2199, 2200, 2201}
    result = manager.release(token, instance.hybrid_instance_id)
    assert result['process_terminated'] is True
    assert platform.terminated == [platform.launch_root]
