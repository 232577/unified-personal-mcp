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
        assert status["running"] and status["tools"] == 49
        assert not status["tunnel_connected"]
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
        key = (cfg.data_root / "backend.key").read_text()
        req = urllib.request.Request(f"http://127.0.0.1:{cfg.port}/mcp", body,
            {"Content-Type": "application/json", "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req) as response:
            assert len(json.load(response)["result"]["tools"]) == 49
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
