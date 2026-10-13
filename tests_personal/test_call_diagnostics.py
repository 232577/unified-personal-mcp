"""Observe the real HTTP/dispatch boundary without logging request contents."""
# ruff: noqa: F811
import json
import threading
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from coding_tools_mcp.errors import JsonRpcError
from coding_tools_mcp.server import MCPHandler
from personal_mcp.diagnostics import CallDiagnostics, trace_scope
from personal_mcp.service import PersonalMCPHandler, ServiceHTTPServer
from tests_personal.test_host import host, workflow  # noqa: F401


def events(host):
    path = host.config.data_root / 'diagnostics' / 'calls.jsonl'
    assert path.is_file(), 'local structured call diagnostics are missing'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


@contextmanager
def http_host(host):
    server = ServiceHTTPServer(('127.0.0.1', 0), host,
        control_key='fixture-control-' + 'x' * 32, tunnel_retry=lambda: {})
    server.daemon_threads = False  # server_close joins handlers before reading logs.
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/mcp'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        assert not thread.is_alive()


def post(url, body, credential):
    request = urllib.request.Request(url, json.dumps(body).encode('utf-8'),
        {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + credential})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
        return json.load(response)


def test_catalog_snapshot_matches_served_tools_without_runtime_secrets(host):
    with http_host(host) as url:
        result = post(url, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, host.auth_token)
    path = host.config.data_root / 'diagnostics' / 'catalog.json'
    assert path.is_file(), 'published catalog snapshot is missing'
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    assert snapshot['tools'] == result['result']['tools']
    assert snapshot['catalog_revision'] == host.catalog_revision
    assert host.auth_token not in path.read_text(encoding='utf-8')


def test_http_and_tool_stages_share_trace_without_exposing_payloads(host, capsys):
    secret = 'do-not-log-this-marker'
    with http_host(host) as url:
        result = post(url + '?credential=' + secret, {
            'jsonrpc': '2.0', 'id': secret, 'method': 'tools/call',
            'params': {'name': 'UnifiedTask', 'arguments': {'action': 'list'}}}, host.auth_token)
        assert result['result']['structuredContent'] == {'ok': True, 'workflows': []}
    rows = events(host)
    assert [row['stage'] for row in rows] == [
        'http_received', 'tool_received', 'tool_validated', 'tool_returned', 'http_finished']
    assert len({row['trace_id'] for row in rows}) == 1
    assert rows[-1]['http_status'] == 200
    assert rows[-1]['duration_ms'] >= 0
    assert rows[3]['outcome'] == 'ok'
    assert rows[1]['action'] == 'list'
    assert all(row['catalog_revision'] == host.catalog_revision for row in rows)
    raw = json.dumps(rows) + capsys.readouterr().err
    assert secret not in raw
    assert host.auth_token not in raw
    assert 'arguments' not in raw and 'structuredContent' not in raw


def test_unauthorized_http_request_is_recorded_without_tool_dispatch(host):
    with http_host(host) as url:
        with pytest.raises(urllib.error.HTTPError) as caught:
            post(url, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': 'UnifiedTask', 'arguments': {'action': 'list'}}}, 'wrong-secret')
        assert caught.value.code == 401
    rows = events(host)
    assert [row['stage'] for row in rows] == ['http_received', 'http_finished']
    assert rows[-1]['http_status'] == 401
    assert not host.registry.resources
    assert 'wrong-secret' not in json.dumps(rows)


def test_invalid_arguments_record_validation_failure_without_dispatch(host):
    result = host.call_tool('UnifiedTask', {'action': 'begin', 'workflow_id': 'secret-value'})
    assert result['isError']
    rows = events(host)
    assert [row['stage'] for row in rows] == ['tool_received', 'tool_returned']
    assert rows[-1]['outcome'] == 'error'
    assert rows[-1]['error_code'] == 'INVALID_ARGUMENT'
    assert 'secret-value' not in json.dumps(rows)
    assert not host.registry.resources


def test_direct_calls_have_distinct_trace_ids_and_hide_workflow_credentials(host):
    token = workflow(host, access='read')
    result = host.call_tool('read_file', {'workflow_id': token, 'path': 'note.txt'})
    assert result['structuredContent']['content'] == 'hello unified'
    rows = events(host)
    groups = defaultdict(list)
    for row in rows:
        groups[row['trace_id']].append(row['stage'])
    assert len(groups) == 3
    assert all(stages == ['tool_received', 'tool_validated', 'tool_returned']
               for stages in groups.values())
    assert token not in json.dumps(rows)
    assert 'hello unified' not in json.dumps(rows)


def test_diagnostic_write_failure_does_not_repeat_or_change_business_result(host, monkeypatch):
    assert hasattr(host, 'diagnostics'), 'local diagnostics are missing'
    def fail_write(_):
        raise OSError('secret-path-or-disk-error')
    monkeypatch.setattr(host.diagnostics, '_append', fail_write)
    result = host.call_tool('UnifiedTask', {'action': 'begin', 'project_path': 'app',
        'request_id': 'once-only'})
    assert result['structuredContent']['state'] == 'RESERVED'
    assert len(host.registry.list(host.principal)['workflows']) == 1
    health = host.diagnostics.snapshot()
    assert health['status'] == 'degraded' and health['dropped_events'] >= 1
    assert 'secret-path' not in json.dumps(health)


def test_unknown_tool_or_action_never_copies_untrusted_names_to_logs(host):
    secret = 'untrusted-name-with-secret'
    with pytest.raises(JsonRpcError):
        host.call_tool(secret, {})
    assert host.call_tool('UnifiedTask', {'action': secret})['isError']
    rows = events(host)
    assert rows[0]['tool'] == '(unknown)'
    assert secret not in json.dumps(rows)
    assert rows[1]['stage'] == 'tool_raised'


def test_http_failure_is_unknown_and_does_not_replay_the_handler(host, monkeypatch):
    dispatched = []
    def broken_transport(_):
        dispatched.append(True)
        raise ConnectionResetError('private-transport-detail')
    monkeypatch.setattr(MCPHandler, 'do_POST', broken_transport)
    handler = object.__new__(PersonalMCPHandler)
    handler.server = SimpleNamespace(runtime=host)
    with pytest.raises(ConnectionResetError):
        handler.do_POST()
    rows = events(host)
    assert dispatched == [True]
    assert [row['stage'] for row in rows] == ['http_received', 'http_failed']
    assert rows[-1]['outcome'] == 'unknown'
    assert 'private-transport-detail' not in json.dumps(rows)


def test_bounded_rotation_keeps_complete_json_lines_and_reports_retention(tmp_path):
    diagnostics = CallDiagnostics(tmp_path, {}, 'a' * 64, 'test', max_bytes=1024, backups=2)
    for _ in range(30):
        with trace_scope() as trace_id:
            diagnostics.emit('http_received', trace_id)
    paths = list(diagnostics.root.glob('calls*.jsonl'))
    assert len(paths) == 3
    assert all(path.stat().st_size <= 1024 for path in paths)
    retained = [json.loads(line) for path in paths
                for line in path.read_text(encoding='utf-8').splitlines()]
    assert max(row['sequence'] for row in retained) == 30
    assert len({row['sequence'] for row in retained}) == len(retained)
    health = diagnostics.snapshot()
    assert health['rotations'] > 0 and health['dropped_events'] == 0


def test_concurrent_local_diagnostics_have_unique_complete_records(tmp_path):
    diagnostics = CallDiagnostics(tmp_path, {}, 'b' * 64, 'test')
    def record(_):
        with trace_scope() as trace_id:
            diagnostics.emit('http_received', trace_id)
            diagnostics.emit('http_finished', trace_id, http_status=200)
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(record, range(24)))
    rows = [json.loads(line) for line in diagnostics.path.read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 48
    assert {row['sequence'] for row in rows} == set(range(1, 49))
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['trace_id']].append(row['stage'])
    assert len(grouped) == 24
    assert all(stages == ['http_received', 'http_finished'] for stages in grouped.values())


def test_catalog_write_failure_is_exposed_without_blocking_calls(tmp_path):
    (tmp_path / 'diagnostics').write_text('blocked directory', encoding='utf-8')
    diagnostics = CallDiagnostics(tmp_path, {}, 'c' * 64, 'test')
    with trace_scope() as trace_id:
        diagnostics.emit('http_received', trace_id)
    assert diagnostics.snapshot()['status'] == 'degraded'
    assert diagnostics.snapshot()['catalog_available'] is False
    assert diagnostics.snapshot()['dropped_events'] == 1
