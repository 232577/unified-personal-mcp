import sys
from types import SimpleNamespace

import pytest
import yaml

from personal_mcp import tunnel
from personal_mcp.config import load_config
from tests_personal.test_config import installation


def registry(monkeypatch, enabled, server, *, unavailable=False):
    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def open_key(root, path):
        assert path == r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
        if unavailable:
            raise OSError('unavailable')
        return Key()

    values = {'ProxyEnable': (enabled, 4), 'ProxyServer': (server, 1)}
    fake = SimpleNamespace(HKEY_CURRENT_USER=1, REG_DWORD=4, REG_SZ=1,
                           OpenKey=open_key, QueryValueEx=lambda key, name: values[name])
    monkeypatch.setitem(sys.modules, 'winreg', fake)
    monkeypatch.setattr(sys, 'platform', 'win32')


@pytest.mark.parametrize('server, expected', [
    ('127.0.0.1:7897', 'http://127.0.0.1:7897'),
    ('http://localhost:8080', 'http://localhost:8080'),
    ('https://proxy.example:8443', 'https://proxy.example:8443'),
    ('[::1]:7897', 'http://[::1]:7897'),
    ('http=localhost:8080;https=127.0.0.1:7897;socks=localhost:1080', 'http://127.0.0.1:7897'),
    ('socks=localhost:1080;http=localhost:8080', 'http://localhost:8080'),
    ('HTTPS=LOCALHOST:08080;http=localhost:8081', 'http://localhost:8080'),
])
def test_enabled_explicit_wininet_proxy(monkeypatch, server, expected):
    registry(monkeypatch, 1, server)
    assert tunnel.system_http_proxy() == expected


@pytest.mark.parametrize('server', [
    '', 'socks=localhost:1080', 'socks5://localhost:1080',
    'http://user:password@localhost:8080', 'localhost:0', 'localhost:65536',
    'localhost:bad', 'localhost', 'http://localhost:8080/path',
    'http://localhost:8080/', 'http://localhost:8080?secret=x',
    'http://localhost:8080#fragment', 'http://local host:8080',
    'http://localhost:8080?', 'http://localhost:8080#',
    'http://.:8080', 'http://-proxy:8080', 'http://proxy..example:8080',
    'http://localhost:8080\n', 'http://localhost:8080\\secret',
    'http=localhost:8080;https=socks5://localhost:1080',
    'https=localhost:8080;https=localhost:8081',
])
def test_unsupported_or_malformed_proxy_is_ignored(monkeypatch, server):
    registry(monkeypatch, 1, server)
    assert tunnel.system_http_proxy() is None


@pytest.mark.parametrize('enabled', [0, 2, '1', None])
def test_disabled_or_invalid_enable_flag_ignores_proxy(monkeypatch, enabled):
    registry(monkeypatch, enabled, '127.0.0.1:7897')
    assert tunnel.system_http_proxy() is None


def test_unavailable_registry_and_non_windows_do_not_use_environment(monkeypatch):
    registry(monkeypatch, 1, '127.0.0.1:7897', unavailable=True)
    monkeypatch.setenv('HTTPS_PROXY', 'http://unexpected.example:8080')
    assert tunnel.system_http_proxy() is None
    monkeypatch.setattr(sys, 'platform', 'linux')
    assert tunnel.system_http_proxy() is None


@pytest.fixture
def started_runner(tmp_path, monkeypatch):
    cfg = load_config(installation(tmp_path))
    state = {'key': 'fixture-cloud-key', 'proxy': 'http://127.0.0.1:7897', 'samples': 0}

    def proxy():
        state['samples'] += 1
        return state['proxy']

    process = SimpleNamespace(pid=123, poll=lambda: None, wait=lambda timeout: None)
    class Job:
        def spawn(self, args, **kwargs):
            state['env'] = kwargs['env']
            return process

        def close(self):
            pass

    monkeypatch.setattr(tunnel, 'verify_client', lambda binary: binary)
    monkeypatch.setattr(tunnel, 'assert_tunnel_available', lambda ident: None)
    monkeypatch.setattr(tunnel, 'read_tunnel_key', lambda config: state['key'])
    monkeypatch.setattr(tunnel, 'system_http_proxy', proxy, raising=False)
    monkeypatch.setattr(tunnel, 'OwnedJob', Job)
    runner = tunnel.TunnelRunner(cfg, tmp_path / 'fixture.exe', 'fixture-backend-key')
    runner.start()
    try:
        yield runner, state
    finally:
        runner.close()


def test_proxy_is_sampled_once_for_control_plane_and_not_backend(started_runner):
    runner, state = started_runner
    assert state['samples'] == 1
    profile = yaml.safe_load((runner.config.data_root / 'tunnel.local.yaml').read_text())
    assert profile['control_plane']['http_proxy'] == 'http://127.0.0.1:7897'
    assert profile['mcp']['server_urls'] == [{'channel': 'main', 'url': 'http://127.0.0.1:28776/mcp'}]
    assert 'http_proxy' not in profile['mcp']
    assert not any(name.lower() in {'http_proxy', 'https_proxy', 'all_proxy'} for name in state['env'])
    assert not runner.credentials_changed()


@pytest.mark.parametrize('field, value', [
    ('key', 'fixture-new-cloud-key'), ('proxy', 'http://127.0.0.1:8080'), ('proxy', None),
])
def test_explicit_retry_detects_key_or_effective_proxy_change(started_runner, field, value):
    runner, state = started_runner
    state[field] = value
    assert runner.credentials_changed()


def test_unchanged_connection_settings_are_stable(started_runner):
    runner, state = started_runner
    assert not runner.credentials_changed()
    assert not runner.credentials_changed()


def test_equivalent_system_proxy_does_not_trigger_replacement(started_runner, monkeypatch):
    runner, state = started_runner
    monkeypatch.setattr(tunnel, 'system_http_proxy', _SYSTEM_HTTP_PROXY)
    registry(monkeypatch, 1, 'https=127.0.0.1:07897;http=localhost:8080')
    assert not runner.credentials_changed()


_SYSTEM_HTTP_PROXY = getattr(tunnel, 'system_http_proxy', None)


def test_direct_profile_has_no_proxy_field(tmp_path):
    cfg = load_config(installation(tmp_path))
    assert 'http_proxy' not in tunnel.tunnel_profile(cfg, http_proxy=None)['control_plane']
