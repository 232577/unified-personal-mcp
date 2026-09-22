import json
import socket
import urllib.request
from pathlib import Path

import pytest

from personal_mcp.config import load_config
from personal_mcp.service import LocalService
from tests_personal.test_config import installation

ROOT = Path(__file__).resolve().parents[1]


def config(tmp_path):
    with socket.socket() as port:
        port.bind(("127.0.0.1", 0))
        number = port.getsockname()[1]
    return load_config(installation(tmp_path, port=number))


def test_same_program_starts_authenticated_catalog_and_stops(tmp_path):
    cfg = config(tmp_path)
    service = LocalService(cfg, assets_root=ROOT / "resources")
    try:
        status = service.start()
        assert status["running"] and status["tools"] == 50
        assert not status["tunnel_connected"]
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
        key = (cfg.data_root / "backend.key").read_text()
        req = urllib.request.Request(f"http://127.0.0.1:{cfg.port}/mcp", body,
            {"Content-Type": "application/json", "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req) as response:
            assert len(json.load(response)["result"]["tools"]) == 50
        second = LocalService(cfg, assets_root=ROOT / "resources")
        with pytest.raises(RuntimeError, match="INSTANCE_ALREADY_RUNNING"):
            second.start()
        assert service.status()["running"]
    finally:
        service.stop()
    assert not service.status()["running"]
    service.stop()


def test_occupied_port_does_not_stop_another_listener(tmp_path):
    cfg = config(tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", cfg.port))
        listener.listen()
        service = LocalService(cfg, assets_root=ROOT / "resources")
        with pytest.raises(OSError):
            service.start()
        assert not service.status()["running"]
        assert listener.getsockname()[1] == cfg.port
    service.start()
    service.stop()


def test_offline_login_keeps_local_http_and_health_queries_are_cached(tmp_path, monkeypatch):
    import personal_mcp.tunnel as tunnel
    from tests_personal.test_tunnel_supervisor import FakeRunner, eventually
    # Replace the process boundary only; actual service and supervisor stay live.
    runner = FakeRunner()
    monkeypatch.setattr(tunnel, 'TunnelRunner', lambda *args: runner)
    try:
        import personal_mcp.tunnel_supervisor as supervisor
        monkeypatch.setattr(supervisor, 'TunnelRunner', lambda *args: runner)
    except ImportError:
        monkeypatch.setattr('personal_mcp.service.TunnelRunner', lambda *args: runner)
    cfg = config(tmp_path)
    service = LocalService(cfg, assets_root=ROOT / 'resources')
    try:
        status = service.start(connect_tunnel=True)
        assert status['running']
        assert hasattr(service, 'health_snapshot'), 'service health must be cached'
        eventually(lambda: service.health_snapshot()['tunnel']['status'] == 'degraded')
        assert service.runtime.health_provider == service.health_snapshot
        assert service.status()['version']
        runtime = service.runtime
        runner.available = True
        eventually(lambda: service.status()['tunnel_connected'])
        assert service.runtime is runtime
        assert runner.starts == 1
    finally:
        service.stop()


def test_tunnel_recovers_while_registry_maintenance_is_blocked(tmp_path, monkeypatch):
    import threading
    import personal_mcp.tunnel_supervisor as supervisor
    from tests_personal.test_tunnel_supervisor import FakeRunner, eventually

    first, second = FakeRunner(), FakeRunner()
    first.available = second.available = True
    runners = iter([first, second])
    monkeypatch.setattr(supervisor, 'TunnelRunner', lambda *args: next(runners))
    entered, release = threading.Event(), threading.Event()
    service = LocalService(config(tmp_path), assets_root=ROOT / 'resources')

    def blocked_expiration():
        entered.set()
        assert release.wait(10)

    try:
        service.start(connect_tunnel=True)
        service.tunnel._retry_base = 0.01
        service.tunnel._poll_interval = 0.01
        monkeypatch.setattr(service.runtime.registry, 'expire', blocked_expiration)
        assert entered.wait(3)
        eventually(lambda: service.status()['tunnel_connected'])
        first.alive = False
        eventually(lambda: second.starts == 1)
        eventually(lambda: service.status()['tunnel_connected'])
        assert not release.is_set()
    finally:
        release.set()
        service.stop()
