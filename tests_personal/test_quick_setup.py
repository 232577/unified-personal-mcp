import time
import tkinter as tk
from pathlib import Path

import pytest

from personal_mcp.quick_setup import QuickSetupWindow


@pytest.fixture(scope='module')
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def screen(tmp_path, tk_root):
    root = tk.Toplevel(tk_root)
    root.withdraw()
    completed = []
    view = QuickSetupWindow(root, tmp_path / 'settings.local.json', tmp_path / 'resources',
                           on_complete=completed.append)
    yield root, view, completed
    view.dispose()
    root.destroy()


def drain(root, condition):
    limit = time.monotonic() + 4
    while not condition() and time.monotonic() < limit:
        root.update()
        time.sleep(0.01)
    assert condition()


def test_two_fields_are_required_and_key_is_masked(screen, monkeypatch):
    _, view, _ = screen
    assert view.key_entry.cget('show')
    monkeypatch.setattr('personal_mcp.quick_setup.configure_new_device',
                        lambda *a, **kw: pytest.fail('empty form persisted'))
    view.submit()
    assert '隧道 ID' in view.feedback.get() and not view.busy
    view.tunnel.set('tunnel_' + '1' * 32)
    view.submit()
    assert '运行 key' in view.feedback.get() and not view.busy


def test_success_saves_enables_login_and_launches_background(screen, monkeypatch):
    root, view, completed = screen
    events = []
    config = object()
    def configure(path, tunnel, key, *, full_control):
        assert tunnel == 'tunnel_' + '1' * 32
        assert key == 'fixture-running-key-123456789'
        assert full_control
        events.append('save')
        return config
    monkeypatch.setattr('personal_mcp.quick_setup.configure_new_device', configure)
    class Manager:
        def __init__(self, path):
            pass
        def enable(self, bundle, *, expected_version):
            assert expected_version
            events.append('login')
    monkeypatch.setattr('personal_mcp.quick_setup.AutostartManager', Manager)
    monkeypatch.setattr('personal_mcp.quick_setup.bundle_directory', lambda assets: Path(assets).parent)
    monkeypatch.setattr('personal_mcp.quick_setup.launch_background_service',
                        lambda cfg, assets: events.append('background') if cfg is config else None)
    view.tunnel.set('tunnel_' + '1' * 32)
    view.key.set('fixture-running-key-123456789')
    view.submit()
    view.submit()
    drain(root, lambda: bool(completed))
    assert events == ['save', 'login', 'background']
    assert view.key.get() == ''
    assert '已连接' not in completed[0]


def test_key_error_is_safe_and_allows_retry(screen, monkeypatch):
    root, view, completed = screen
    secret = 'fixture-running-key-123456789'
    def fail(*args, **kwargs):
        raise RuntimeError(secret)
    monkeypatch.setattr('personal_mcp.quick_setup.configure_new_device', fail)
    view.tunnel.set('tunnel_' + '1' * 32)
    view.key.set(secret)
    view.submit()
    drain(root, lambda: not view.busy)
    assert secret not in view.feedback.get()
    assert not completed
    assert view.save_button.instate(['!disabled'])


def test_login_failure_does_not_claim_saved_configuration_failed(screen, monkeypatch):
    root, view, completed = screen
    monkeypatch.setattr('personal_mcp.quick_setup.configure_new_device', lambda *a, **kw: object())
    class Manager:
        def __init__(self, path):
            pass
        def enable(self, *args, **kwargs):
            raise RuntimeError('AUTOSTART_CONFIGURATION_CONFLICT')
    monkeypatch.setattr('personal_mcp.quick_setup.AutostartManager', Manager)
    monkeypatch.setattr('personal_mcp.quick_setup.bundle_directory', lambda assets: assets)
    launched = []
    monkeypatch.setattr('personal_mcp.quick_setup.launch_background_service', lambda *a: launched.append(True))
    view.tunnel.set('tunnel_' + '1' * 32)
    view.key.set('fixture-running-key-123456789')
    view.submit()
    drain(root, lambda: bool(completed))
    assert '配置已保存' in completed[0] and '自动连接未启用' in completed[0]
    assert launched
