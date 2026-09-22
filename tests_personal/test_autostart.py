import hashlib
import json
from pathlib import Path

import pytest

from tests_personal.test_config import installation


def packaged(tmp_path, version="0.1.6"):
    bundle = tmp_path / "portable package"
    files = {
        "UnifiedPersonalMCP.exe": b"launcher fixture",
        "resources/python/pythonw.exe": b"python fixture",
        "resources/python/python.exe": b"python console fixture",
        "app/run.py": b"entry fixture",
        "app/personal_mcp/__init__.py": f'__version__ = "{version}"\n'.encode(),
        "package-metadata.json": json.dumps({"name": "unified-personal-mcp", "version": version}).encode(),
        "scripts/install-autostart.ps1": b"installer fixture",
    }
    manifest = {}
    for name, content in files.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        manifest[name] = hashlib.sha256(content).hexdigest()
    (bundle / "manifest-sha256.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle


class FakeScheduler:
    def __init__(self, current=None):
        self.current = current or {"exists": False, "enabled": False, "xml": None}
        self.calls = []
        self.fail_registration = False

    def status(self, name):
        return dict(self.current)

    def register(self, name, bundle, config):
        self.calls.append("register")
        self.current = {"exists": True, "enabled": True, "xml": "<Task>new</Task>",
                        "bundle": str(bundle), "config": str(config), "managed": True}
        if self.fail_registration:
            raise RuntimeError("fixture registration failure")
        return {'xml': self.current['xml']}

    def restore(self, name, previous):
        self.calls.append("restore")
        self.current = dict(previous)

    def disable(self, name):
        self.calls.append("disable")
        self.current["enabled"] = False


def manager(tmp_path, scheduler=None, smoke=None, probe=None):
    from personal_mcp.autostart import AutostartManager
    config = installation(tmp_path)
    return AutostartManager(config, scheduler=scheduler or FakeScheduler(),
        smoke=smoke or (lambda bundle, cfg, version: {"ok": True, "version": version, "tools": 50}),
        process_probe=probe or (lambda saved, cfg: {"running": False}))


@pytest.mark.parametrize("damage", ["missing", "changed", "extra", "escape", "version"])
def test_invalid_package_never_changes_existing_task(tmp_path, damage):
    bundle = packaged(tmp_path)
    if damage == "missing":
        (bundle / "app/run.py").unlink()
    elif damage == "changed":
        (bundle / "app/run.py").write_text("altered")
    elif damage == "extra":
        (bundle / "unexpected.py").write_text("unverified")
    elif damage == "escape":
        path = bundle / "manifest-sha256.json"
        manifest = json.loads(path.read_text())
        manifest["../outside.txt"] = "a" * 64
        path.write_text(json.dumps(manifest))
    scheduler = FakeScheduler({"exists": True, "enabled": True, "xml": "<Task>old</Task>", "managed": True})
    item = manager(tmp_path, scheduler=scheduler)
    with pytest.raises(ValueError):
        item.enable(bundle, expected_version="0.1.5" if damage == "version" else "0.1.6")
    assert scheduler.current["xml"] == "<Task>old</Task>"
    assert not scheduler.calls


def test_failed_isolated_start_preserves_previous_task(tmp_path):
    previous = {"exists": True, "enabled": True, "xml": "<Task>old</Task>", "managed": True}
    scheduler = FakeScheduler(previous.copy())
    def fail(bundle, config, version):
        raise RuntimeError("ISOLATED_START_FAILED")
    item = manager(tmp_path, scheduler=scheduler, smoke=fail)
    with pytest.raises(RuntimeError, match="ISOLATED_START_FAILED"):
        item.enable(packaged(tmp_path), expected_version="0.1.6")
    assert scheduler.current == previous
    assert not scheduler.calls


def test_registration_failure_restores_previous_definition_and_keeps_backup(tmp_path):
    previous = {"exists": True, "enabled": True, "xml": "<Task>old</Task>", "managed": True}
    class VerificationFailsOnce(FakeScheduler):
        fail_read = False
        def register(self, name, bundle, config):
            receipt = super().register(name, bundle, config)
            self.fail_read = True
            return receipt
        def status(self, name):
            if self.fail_read:
                self.fail_read = False
                raise RuntimeError('status temporarily unavailable')
            return super().status(name)
    scheduler = VerificationFailsOnce(previous.copy())
    item = manager(tmp_path, scheduler=scheduler)
    with pytest.raises(RuntimeError, match="AUTOSTART_UPDATE_FAILED"):
        item.enable(packaged(tmp_path), expected_version="0.1.6")
    assert scheduler.current == previous
    assert scheduler.calls == ['register', 'restore']
    backups = list((item.config.data_root / "autostart" / "backups").glob("*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text())["previous"]["xml"] == "<Task>old</Task>"


def test_validated_upgrade_is_next_login_only_and_disable_does_not_stop_service(tmp_path):
    scheduler = FakeScheduler()
    item = manager(tmp_path, scheduler=scheduler)
    bundle = packaged(tmp_path)
    result = item.enable(bundle, expected_version="0.1.6")
    assert result["enabled"] and result["pending_version"] == "0.1.6"
    assert result["started_now"] is False
    assert scheduler.calls == ["register"]
    item.disable()
    assert scheduler.calls == ["register", "disable"]
    assert scheduler.current["exists"] and not scheduler.current["enabled"]


def test_unrelated_existing_task_is_never_overwritten(tmp_path):
    scheduler = FakeScheduler({"exists": True, "enabled": True, "xml": "<Task>unrelated</Task>", "managed": False})
    item = manager(tmp_path, scheduler=scheduler)
    with pytest.raises(RuntimeError, match="AUTOSTART_TASK_NOT_OWNED"):
        item.enable(packaged(tmp_path), expected_version="0.1.6")
    with pytest.raises(RuntimeError, match="AUTOSTART_TASK_NOT_OWNED"):
        item.disable()
    assert not scheduler.calls


def test_status_distinguishes_live_external_version_from_next_login_target(tmp_path):
    bundle = packaged(tmp_path)
    scheduler = FakeScheduler({"exists": True, "enabled": True, "xml": "<Task />", "managed": True,
                              "bundle": str(bundle)})
    item = manager(tmp_path, scheduler=scheduler,
        probe=lambda saved, cfg: {"running": True, "running_version": "0.1.2", "pid": 1234})
    item.config.data_root.mkdir()
    (item.config.data_root / "service-status.json").write_text(json.dumps(
        {"running": True, "pid": 1234, "tools": 49, "tunnel_connected": True}))
    result = item.status()
    assert result["running_version"] == "0.1.2"
    assert result["pending_version"] == "0.1.6"
    assert result["running"] and result["tunnel_connected"]


def test_dead_saved_pid_does_not_report_a_running_service(tmp_path):
    item = manager(tmp_path)
    item.config.data_root.mkdir()
    (item.config.data_root / "service-status.json").write_text(json.dumps(
        {"running": True, "pid": 999999, "version": "0.1.2", "tunnel_connected": True}))
    result = item.status()
    assert result["running"] is False
    assert result["running_version"] is None
    assert result["tunnel_connected"] is False


def test_install_smoke_uses_isolated_workspace_and_no_tunnel(tmp_path, monkeypatch):
    from personal_mcp import autostart
    bundle = packaged(tmp_path)
    item = manager(tmp_path)
    observed = {}
    class Job:
        def spawn(self, argv, **kwargs):
            import subprocess
            observed.update(argv=argv, kwargs=kwargs)
            fixture = Path(argv[argv.index("--config") + 1])
            isolated = json.loads(fixture.read_text())
            assert isolated["data_root"] != str(item.config.data_root)
            assert isolated["workspace_root"] != str(item.config.workspace_root)
            assert "--connect" not in argv
            output = Path(argv[argv.index("--result") + 1])
            output.write_text(json.dumps({"ok": True, "version": "0.1.6", "tools": 50}))
            assert kwargs["stdout"] == subprocess.DEVNULL
            return type("Child", (), {"wait": lambda self, timeout: 0})()
        def close(self):
            observed["closed"] = True
    monkeypatch.setattr(autostart, "OwnedJob", Job)
    result = autostart.isolated_startup_check(bundle, item.config, "0.1.6")
    assert result["version"] == "0.1.6" and observed["closed"]
    assert observed["argv"][0] == str(bundle / "resources/python/pythonw.exe")
    assert not any((item.config.data_root / "autostart" / "validation").iterdir())


def test_disabled_task_for_another_configuration_is_not_taken_over(tmp_path):
    scheduler = FakeScheduler({"exists": True, "enabled": False, "xml": "<Task>other install</Task>",
        "managed": True, "config": str(tmp_path / "another-installation/settings.local.json")})
    item = manager(tmp_path, scheduler=scheduler)
    with pytest.raises(RuntimeError, match="AUTOSTART_CONFIGURATION_CONFLICT"):
        item.enable(packaged(tmp_path), expected_version="0.1.6")
    assert not scheduler.calls


def test_package_changed_by_validation_is_rejected_before_registration(tmp_path):
    bundle = packaged(tmp_path)
    def changed(bundle, config, version):
        (bundle / "app/run.py").write_text("changed while checking")
        return {"ok": True, "version": version, "tools": 50}
    item = manager(tmp_path, smoke=changed)
    with pytest.raises(ValueError, match="PACKAGE_HASH_MISMATCH"):
        item.enable(bundle, expected_version="0.1.6")
    assert not item.scheduler.calls


def test_stale_status_does_not_claim_the_tunnel_is_currently_connected(tmp_path):
    item = manager(tmp_path, probe=lambda saved, cfg: {
        "running": True, "running_version": "0.1.2", "status_fresh": False})
    item.config.data_root.mkdir()
    (item.config.data_root / "service-status.json").write_text(json.dumps({
        "running": True, "pid": 1234, "tools": 49, "tunnel_connected": True}))
    result = item.status()
    assert result["running"] and result["tunnel_connected"] is None


def test_installation_metadata_and_script_are_written_inside_package(tmp_path):
    from scripts.build_portable import installation_files
    installation_files(tmp_path)
    metadata = json.loads((tmp_path / "package-metadata.json").read_text())
    assert metadata["name"] == "unified-personal-mcp" and metadata["version"]
    assert (tmp_path / "scripts/install-autostart.ps1").is_file()


def test_windows_command_line_preserves_unicode_and_space_paths():
    import subprocess
    from personal_mcp.autostart import _argv
    expected = ["-B", "-s", "D:\\路径 空格\\app\\run.py", "serve", "--config", "C:\\用户\\配置 文件.json"]
    assert _argv(subprocess.list2cmdline(expected)) == expected


def test_normalized_current_windows_account_is_recognized_as_task_owner():
    import win32api
    from personal_mcp.autostart import WindowsTaskScheduler
    scheduler = WindowsTaskScheduler()
    assert scheduler._is_current_user(win32api.GetUserName())
    assert scheduler._is_current_user(scheduler._sid())
    assert not scheduler._is_current_user("S-1-5-18")  # LocalSystem is not this interactive account.


def test_scheduler_com_objects_are_released_before_apartment_uninitializes(monkeypatch):
    import gc
    import weakref
    import pythoncom
    import win32com.client
    from personal_mcp.autostart import WindowsTaskScheduler
    live = weakref.WeakSet()
    class Tracked:
        def __init__(self):
            live.add(self)
    class Definition(Tracked):
        def __init__(self):
            super().__init__()
            self.RegistrationInfo = type("Info", (), {"Description": "not managed"})()
            self.Principal = type("Principal", (), {"UserId": "nobody"})()
    class Task(Tracked):
        Xml, Enabled, State = "<Task />", True, 3
        def __init__(self):
            super().__init__()
            self.Definition = Definition()
    class Folder(Tracked):
        def GetTask(self, name):
            return Task()
    class Service(Tracked):
        def Connect(self):
            pass
        def GetFolder(self, name):
            return Folder()
    released = []
    def uninitialize():
        gc.collect()
        released.append(not live)
    monkeypatch.setattr(pythoncom, "CoInitialize", lambda: None)
    monkeypatch.setattr(pythoncom, "CoUninitialize", uninitialize)
    monkeypatch.setattr(win32com.client, "Dispatch", lambda name: Service())
    result = WindowsTaskScheduler().status("UnifiedPersonalMCP-Test")
    assert not result["managed"] and released == [True]


def test_failed_smoke_payload_cannot_enable_login(tmp_path):
    item = manager(tmp_path, smoke=lambda bundle, cfg, version: {"ok": False, "version": version, "tools": 50})
    with pytest.raises(RuntimeError, match="ISOLATED_START_FAILED"):
        item.enable(packaged(tmp_path), expected_version="0.1.6")
    assert not item.scheduler.calls


def test_task_changed_during_validation_is_preserved(tmp_path):
    scheduler = FakeScheduler({"exists": True, "enabled": True, "managed": True, "xml": "<Task>before</Task>"})
    def changed(bundle, config, version):
        scheduler.current["xml"] = "<Task>concurrently changed</Task>"
        return {"ok": True, "version": version, "tools": 50}
    item = manager(tmp_path, scheduler=scheduler, smoke=changed)
    with pytest.raises(RuntimeError, match="AUTOSTART_CHANGED_DURING_VALIDATION"):
        item.enable(packaged(tmp_path), expected_version="0.1.6")
    assert scheduler.current["xml"] == "<Task>concurrently changed</Task>"
    assert not scheduler.calls


def test_real_isolated_cli_startup_exits_without_starting_a_tunnel(tmp_path):
    import os
    import socket
    import subprocess
    import sys
    from personal_mcp import __version__
    from personal_mcp.windows_jobs import OwnedJob
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    config = installation(tmp_path, port=port)
    output = config.parent / "smoke-result.json"
    job = OwnedJob()
    try:
        child = job.spawn([sys.executable, "-B", "-m", "personal_mcp", "install-smoke", "--config", str(config),
            "--assets", str(Path(__file__).resolve().parents[1] / "resources"), "--result", str(output),
            "--expected-version", __version__], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        _, stderr = child.communicate(timeout=30)
        assert child.returncode == 0, stderr.decode(errors="replace")
        result = json.loads(output.read_text())
        assert result["ok"] and result["version"] == __version__ and result["tools"] >= 49
        assert not result["tunnel_connected"]
        assert not (config.parent / "private" / "tunnel.pid").exists()
    finally:
        job.close()


def test_task_definition_uses_hidden_bundled_interpreter_and_interactive_current_user(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import win32com.client
    from xml.etree import ElementTree
    from personal_mcp.autostart import WindowsTaskScheduler, _argv
    # Build a real COM definition within the manager's apartment, but intercept
    # registration: no task is created, updated or started by this test.
    captured = {}
    class Folder:
        def RegisterTaskDefinition(self, name, definition, flags, sid, password, logon):
            captured.update(xml=definition.XmlText, sid=sid, password=password, logon=logon)
            return type('Registered', (), {'Xml': definition.XmlText})()
    @contextmanager
    def isolated_folder():
        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        yield service, Folder()
    scheduler = WindowsTaskScheduler()
    monkeypatch.setattr(scheduler, "_folder", isolated_folder)
    bundle = tmp_path / "中文 portable"
    scheduler.register("UnifiedPersonalMCP-OnlyDefinition", bundle, tmp_path / "settings.local.json")
    root = ElementTree.fromstring(captured["xml"])
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    command = root.findtext("t:Actions/t:Exec/t:Command", namespaces=ns)
    argv = _argv(root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns))
    assert command == str(bundle / "resources/python/pythonw.exe")
    assert "--connect" in argv and "serve" in argv
    assert root.findtext("t:Principals/t:Principal/t:LogonType", namespaces=ns) == "InteractiveToken"
    assert root.findtext("t:Settings/t:MultipleInstancesPolicy", namespaces=ns) == "IgnoreNew"
    assert captured["sid"] == scheduler._sid() and captured["password"] is None


def test_status_does_not_claim_another_configuration_will_start(tmp_path):
    scheduler = FakeScheduler({'exists': True, 'enabled': True, 'managed': True,
        'xml': '<Task>other</Task>', 'bundle': str(packaged(tmp_path)),
        'config': str(tmp_path / 'other-settings.local.json')})
    item = manager(tmp_path, scheduler=scheduler)
    result = item.status()
    assert not result['enabled'] and result['pending_version'] is None
    assert result['error'] == 'AUTOSTART_CONFIGURATION_CONFLICT'
    assert not scheduler.calls


def test_registration_verification_preserves_external_change_and_backup(tmp_path):
    original = {'exists': True, 'enabled': True, 'managed': True, 'xml': '<Task>original</Task>'}
    external = {'exists': True, 'enabled': True, 'managed': False, 'xml': '<Task>external</Task>'}
    class InterleavedScheduler(FakeScheduler):
        def register(self, name, bundle, config):
            receipt = super().register(name, bundle, config)
            self.current = dict(external)
            return receipt
    scheduler = InterleavedScheduler(dict(original))
    item = manager(tmp_path, scheduler=scheduler)
    with pytest.raises(RuntimeError, match='AUTOSTART_UPDATE_CONFLICT'):
        item.enable(packaged(tmp_path), expected_version='0.1.6')
    assert scheduler.current == external and scheduler.calls == ['register']
    assert len(list((item.config.data_root / 'autostart/backups').glob('*.json'))) == 1


def test_ambiguous_registration_does_not_guess_ownership_for_rollback(tmp_path):
    scheduler = FakeScheduler({'exists': True, 'enabled': True, 'managed': True, 'xml': '<Task>old</Task>'})
    scheduler.fail_registration = True
    item = manager(tmp_path, scheduler=scheduler)
    with pytest.raises(RuntimeError, match='AUTOSTART_UPDATE_CONFLICT'):
        item.enable(packaged(tmp_path), expected_version='0.1.6')
    assert scheduler.calls == ['register']
    assert scheduler.current['xml'] == '<Task>new</Task>'


def retry_fixture(tmp_path, probe=None):
    item = manager(tmp_path, probe=probe or (lambda saved, cfg: {'running': True, 'status_fresh': True}))
    item.config.data_root.mkdir()
    (item.config.data_root / 'service-status.json').write_text(json.dumps({'running': True}))
    (item.config.data_root / 'backend.key').write_text('b' * 48)
    (item.config.data_root / 'control.key').write_text('c' * 48)
    return item


def test_local_retry_uses_both_credentials_no_proxy_and_bounded_loopback_request(tmp_path, monkeypatch):
    import io
    import urllib.request
    item = retry_fixture(tmp_path)
    observed = {}
    class Opener:
        def open(self, request, timeout):
            observed.update(request=request, timeout=timeout)
            rpc = json.loads(request.data)
            assert rpc['method'] == 'unified/tunnel/retry' and rpc['params'] == {}
            return io.BytesIO(json.dumps({'jsonrpc': '2.0', 'id': rpc['id'], 'result': {
                'ok': True, 'tunnel': {'status': 'recovering', 'error_code': None}}}).encode())
    def build(*handlers):
        observed['handlers'] = handlers
        return Opener()
    monkeypatch.setattr(urllib.request, 'build_opener', build)
    result = item.retry_tunnel()
    assert result['ok'] and result['tunnel']['status'] == 'recovering'
    request = observed['request']
    assert request.full_url == f'http://127.0.0.1:{item.config.port}/mcp'
    assert request.get_header('Authorization') == 'Bearer ' + 'b' * 48
    assert request.get_header('X-unified-control-key') == 'c' * 48
    assert 0 < observed['timeout'] <= 10
    assert any(isinstance(h, urllib.request.ProxyHandler) and h.proxies == {} for h in observed['handlers'])
    redirect = next(h for h in observed['handlers'] if isinstance(h, urllib.request.HTTPRedirectHandler))
    assert redirect.redirect_request(request, None, 302, 'redirect', {}, 'https://example.com') is None


@pytest.mark.parametrize('case,code', [('not_running', 'LOCAL_SERVICE_NOT_RUNNING'),
    ('unknown', 'LOCAL_SERVICE_NOT_RUNNING'), ('old_service', 'LOCAL_RETRY_UNAVAILABLE'),
    ('bad_key', 'LOCAL_CONTROL_KEY_INVALID')])
def test_local_retry_refuses_unverified_service_or_unavailable_credentials(tmp_path, monkeypatch, case, code):
    import urllib.request
    item = retry_fixture(tmp_path)
    if case == 'not_running':
        item.process_probe = lambda saved, cfg: {'running': False}
    elif case == 'unknown':
        item.process_probe = lambda saved, cfg: {'running': True, 'running_unknown': True}
    elif case == 'old_service':
        (item.config.data_root / 'control.key').unlink()
    else:
        (item.config.data_root / 'control.key').write_text('private\ninvalid')
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a: pytest.fail('must not access the network'))
    with pytest.raises(RuntimeError, match=code):
        item.retry_tunnel()


def test_cli_exposes_local_tunnel_retry(tmp_path, monkeypatch, capsys):
    from personal_mcp import __main__, autostart
    monkeypatch.setattr(autostart, 'AutostartManager', lambda *a, **kw: type('Manager', (), {
        'retry_tunnel': lambda self: {'ok': True, 'tunnel': {'status': 'recovering'}}})())
    assert __main__.main(['tunnel-retry', '--config', str(tmp_path / 'config.json')]) == 0
    assert json.loads(capsys.readouterr().out)['tunnel']['status'] == 'recovering'


@pytest.mark.parametrize('failure', ['timeout', 'bad_id', 'denied', 'oversize'])
def test_local_retry_reports_bounded_sanitized_failure(tmp_path, monkeypatch, failure):
    import io
    import urllib.request
    item = retry_fixture(tmp_path)
    class Opener:
        def open(self, request, timeout):
            if failure == 'timeout':
                raise TimeoutError('private diagnostic must not be exposed')
            rpc = json.loads(request.data)
            if failure == 'bad_id':
                return io.BytesIO(json.dumps({'jsonrpc': '2.0', 'id': 'other-request',
                    'result': {'ok': True, 'tunnel': {'status': 'healthy'}}}).encode())
            if failure == 'denied':
                return io.BytesIO(json.dumps({'jsonrpc': '2.0', 'id': rpc['id'], 'error': {
                    'message': 'private diagnostic must not be exposed'}}).encode())
            return io.BytesIO(b' ' * 65537)
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *handlers: Opener())
    with pytest.raises(RuntimeError) as error:
        item.retry_tunnel()
    assert str(error.value) == 'LOCAL_RETRY_FAILED' and error.value.__suppress_context__


def test_retry_manager_and_real_http_endpoint_share_control_contract(tmp_path, monkeypatch):
    from personal_mcp.autostart import AutostartManager
    from personal_mcp.service import LocalService
    from tests_personal.test_local_control import IsolatedSupervisor
    from tests_personal.test_service import ROOT, config
    monkeypatch.setattr('personal_mcp.service.TunnelSupervisor', IsolatedSupervisor)
    cfg = config(tmp_path)
    instance = LocalService(cfg, assets_root=ROOT / 'resources')
    try:
        instance.start(connect_tunnel=True)
        item = AutostartManager(cfg.path, scheduler=FakeScheduler(),
            process_probe=lambda saved, cfg: {'running': True, 'status_fresh': True})
        result = item.retry_tunnel()
        assert result['tunnel'] == instance.tunnel.snapshot()
        assert result['tunnel']['status'] == 'degraded' and instance.tunnel.retries == 1
        assert instance.status()['running']
    finally:
        instance.stop()


def test_service_identity_check_rejects_another_configuration_before_retry(tmp_path, monkeypatch):
    from personal_mcp import autostart
    item = retry_fixture(tmp_path)
    class Process:
        def create_time(self):
            return 10
        def cmdline(self):
            return ['pythonw.exe', 'run.py', 'serve', '--config', str(tmp_path / 'other-settings.json')]
    monkeypatch.setattr(autostart.psutil, 'Process', lambda pid: Process())
    result = autostart.service_process_status({'running': True, 'pid': 1234, 'updated': 20}, item.config)
    assert result == {'running': False}
