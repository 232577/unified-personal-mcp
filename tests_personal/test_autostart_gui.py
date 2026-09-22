import queue
import threading
from pathlib import Path

import pytest


def test_external_service_status_shows_actual_version_and_disables_local_start():
    from personal_mcp.gui import service_presentation
    result = service_presentation({"running": True, "running_version": "0.1.2", "tools": 49,
        "tunnel_connected": True, "enabled": True, "pending_version": "0.1.6"}, owned=False)
    assert "0.1.2" in result["text"] and "49" in result["text"]
    assert "0.1.6" in result["startup_text"]
    assert not result["start"] and not result["stop"] and not result["editable"]


def test_status_refresh_runs_off_ui_thread(tmp_path):
    from personal_mcp.gui import SetupWindow
    entered, release = threading.Event(), threading.Event()
    class Manager:
        def status(self):
            entered.set()
            assert release.wait(3)
            return {"running": True, "running_version": "0.1.2"}
    window = SetupWindow.__new__(SetupWindow)
    window.path = Path(tmp_path / "settings.local.json")
    window.status_events = queue.Queue()
    window.status_inflight = False
    window.status_factory = lambda path: Manager()
    window.refresh_status()
    assert entered.wait(1)
    assert window.status_inflight
    release.set()
    result = window.status_events.get(timeout=2)
    assert result["running_version"] == "0.1.2"


def test_gui_owned_service_uses_its_actual_connection_status(tmp_path):
    from personal_mcp.gui import SetupWindow
    window = SetupWindow.__new__(SetupWindow)
    window.path = Path(tmp_path / "settings.local.json")
    window.status_events = queue.Queue()
    window.status_inflight = False
    window.status_factory = lambda path: type("Manager", (), {"status": lambda self: {"running": False}})()
    window.service = type("Service", (), {"status": lambda self: {
        "running": True, "tools": 50, "tunnel_connected": True}})()
    window.refresh_status()
    result = window.status_events.get(timeout=2)
    assert result["running"] and result["tunnel_connected"] and result["tools"] == 50


@pytest.mark.parametrize("connected", [False, True])
def test_connect_feedback_waits_for_confirmed_tunnel_health(connected):
    from personal_mcp.gui import SetupWindow
    window = SetupWindow.__new__(SetupWindow)
    window.service = type("Service", (), {
        "connect_tunnel": lambda self: {"status": "recovering"},
        "status": lambda self: {"tunnel_connected": connected}})()
    messages = []
    window.submit = lambda action, label: messages.append(action())
    window.connect()
    assert ("已就绪" in messages[0]) is connected
    if not connected:
        assert "开始连接" in messages[0]


@pytest.mark.parametrize('owned,available,state,expected', [
    (True, False, 'degraded', True), (True, False, 'recovering', False),
    (True, False, 'healthy', False), (True, False, 'disabled', True),
    (False, True, 'degraded', True), (False, False, 'degraded', False)])
def test_connect_availability_uses_actual_health(owned, available, state, expected):
    from personal_mcp.gui import service_presentation
    result = service_presentation({'running': True, 'retry_available': available,
        'health': {'tunnel': {'status': state, 'error_code': 'TUNNEL_KEY_INVALID' if state == 'degraded' else None}}},
        owned=owned)
    assert result['connect'] is expected
    if state == 'degraded':
        assert '运行密钥' in result['text']


def test_external_retry_reports_submission_and_uses_returned_actual_health(tmp_path):
    from personal_mcp.gui import SetupWindow
    window = SetupWindow.__new__(SetupWindow)
    window.path, window.service = tmp_path / 'settings.json', None
    window.status_events = queue.Queue()
    window.observed = {'running': True, 'running_version': '0.1.6'}
    window.status_factory = lambda path: type('Manager', (), {'retry_tunnel': lambda self: {
        'ok': True, 'tunnel': {'status': 'recovering', 'error_code': None}}})()
    messages = []
    window.submit = lambda action, label: messages.append(action())
    window.connect()
    assert '重试已提交' in messages[0] and '已就绪' not in messages[0]
    result = window.status_events.get_nowait()
    assert not result['tunnel_connected'] and result['health']['tunnel']['status'] == 'recovering'


def test_startup_configuration_conflict_is_visible_in_gui():
    from personal_mcp.gui import service_presentation
    result = service_presentation({'enabled': False, 'error': 'AUTOSTART_CONFIGURATION_CONFLICT'}, owned=False)
    assert '另一份配置' in result['startup_text']
