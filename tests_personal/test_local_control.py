import http.client
import json
import subprocess
import sys

import pytest

from personal_mcp.service import LocalService
from personal_mcp.tunnel import tunnel_environment
from tests_personal.test_service import ROOT, config


class IsolatedSupervisor:
    def __init__(self, config, binary, backend_key):
        self.backend_key = backend_key
        self.retries = 0

    def start(self):
        return self.snapshot()

    def retry(self):
        self.retries += 1
        return self.snapshot()

    def snapshot(self):
        return {'status': 'degraded', 'last_success': None, 'error_code': 'TUNNEL_KEY_UNAVAILABLE',
                'recovery_attempts': self.retries, 'next_retry': None}

    def close(self):
        pass


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr('personal_mcp.service.TunnelSupervisor', IsolatedSupervisor)
    instance = LocalService(config(tmp_path), assets_root=ROOT / 'resources')
    instance.start(connect_tunnel=True)
    yield instance
    instance.stop()


def post(service, payload=None, *, backend=True, control=None, headers=None):
    payload = payload if payload is not None else {
        'jsonrpc': '2.0', 'id': 1, 'method': 'unified/tunnel/retry', 'params': {}}
    options = {'Content-Type': 'application/json'}
    if backend:
        options['Authorization'] = 'Bearer ' + service.runtime.auth_token
    if control is not None:
        options['X-Unified-Control-Key'] = control
    options.update(headers or {})
    connection = http.client.HTTPConnection('127.0.0.1', service.config.port, timeout=3)
    try:
        connection.request('POST', '/mcp', json.dumps(payload).encode(), options)
        response = connection.getresponse()
        data = response.read()
        return response.status, json.loads(data) if data else None
    finally:
        connection.close()


def control_key(service):
    path = service.config.data_root / 'control.key'
    assert path.is_file(), 'local-only control credential must be provisioned'
    return path.read_text(encoding='utf-8').strip()


def test_retry_requires_both_backend_and_local_control_credentials(service):
    status, _ = post(service, backend=False, control='not-a-valid-control-key')
    assert status == 401
    for key in (None, 'wrong-control-key'):
        status, response = post(service, control=key)
        assert response['error']['code'] == -32001
        assert response['error']['message'] == 'LOCAL_CONTROL_DENIED'
    assert service.tunnel.retries == 0
    status, _ = post(service, backend=False, control=control_key(service))
    assert status == 401 and service.tunnel.retries == 0
    status, response = post(service, control=control_key(service))
    assert status == 200
    assert response == {'jsonrpc': '2.0', 'id': 1,
        'result': {'ok': True, 'tunnel': service.tunnel.snapshot()}}
    assert service.tunnel.retries == 1


@pytest.mark.parametrize('params', [None, [], {'force': True}, 'invalid'])
def test_retry_rejects_nonempty_or_nonobject_parameters(service, params):
    status, response = post(service, {'jsonrpc': '2.0', 'id': 'bad',
        'method': 'unified/tunnel/retry', 'params': params}, control=control_key(service))
    assert response['error']['code'] == -32602
    assert service.tunnel.retries == 0


def test_notification_and_missing_params_never_request_recovery(service):
    key = control_key(service)
    status, response = post(service, {'jsonrpc': '2.0', 'method': 'unified/tunnel/retry',
                                     'params': {}}, control=key)
    assert status == 202 and response is None
    _, response = post(service, {'jsonrpc': '2.0', 'id': 2, 'method': 'unified/tunnel/retry'}, control=key)
    assert response['error']['code'] == -32602
    assert service.tunnel.retries == 0


@pytest.mark.parametrize('headers,status', [
    ({'Origin': 'https://untrusted.example'}, 403),
    ({'Content-Type': 'text/plain'}, 415),
    ({'MCP-Protocol-Version': 'unsupported'}, 400),
    ({'Content-Length': '209715200'}, 413),
])
def test_retry_inherits_http_origin_media_protocol_and_size_guards(service, headers, status):
    actual, _ = post(service, control=control_key(service), headers=headers)
    assert actual == status
    assert service.tunnel.retries == 0


def test_control_key_is_private_stable_and_never_forwarded_to_tunnel(service):
    key = control_key(service)
    assert len(key) >= 32 and key != service.runtime.auth_token
    assert key not in json.dumps(service.status())
    _, response = post(service, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
        'params': {'name': 'server_info', 'arguments': {}}})
    assert key not in json.dumps(response)
    _, response = post(service, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list', 'params': {}})
    assert len(response['result']['tools']) == 50
    assert key not in json.dumps(response)
    assert service.tunnel.backend_key == service.runtime.auth_token
    assert key not in json.dumps(tunnel_environment('fixture-cloud-key', service.runtime.auth_token))
    service.stop()
    service.start(connect_tunnel=True)
    assert control_key(service) == key


def test_retry_preserves_active_workflow_resource_and_owned_process(service):
    registry, owner = service.runtime.registry, service.runtime.principal
    token = registry.begin(owner, 'app', 'begin')['workflow_id']
    registry.activate(owner, token)
    row = registry._row(owner, token)
    resource = registry.resources[row['token_hash']]
    process = resource.coding.owned_job.spawn(
        [sys.executable, '-c', 'import time;time.sleep(30)'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert process.poll() is None
        _, response = post(service, control=control_key(service))
        assert response['result']['ok']
        assert registry.status(owner, token)['state'] == 'ACTIVE'
        assert registry.resources[row['token_hash']] is resource
        assert process.poll() is None
    finally:
        registry.end(owner, token)
        process.wait(timeout=3)


def test_retry_when_stopping_never_creates_another_connection(service):
    key = control_key(service)
    with service._tunnel_lock:
        service.stopping.set()
    _, response = post(service, control=key)
    assert response['error']['message'] == 'LOCAL_SERVICE_NOT_RUNNING'
    assert service.tunnel.retries == 0


def test_duplicate_control_headers_are_rejected(service):
    connection = http.client.HTTPConnection('127.0.0.1', service.config.port, timeout=3)
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'unified/tunnel/retry', 'params': {}})
    try:
        connection.putrequest('POST', '/mcp')
        connection.putheader('Content-Type', 'application/json')
        connection.putheader('Content-Length', str(len(body)))
        connection.putheader('Authorization', 'Bearer ' + service.runtime.auth_token)
        connection.putheader('X-Unified-Control-Key', control_key(service))
        connection.putheader('X-Unified-Control-Key', control_key(service))
        connection.endheaders(body.encode())
        response = json.loads(connection.getresponse().read())
        assert response['error']['message'] == 'LOCAL_CONTROL_DENIED'
        assert service.tunnel.retries == 0
    finally:
        connection.close()


def test_control_request_unblocks_real_supervisor_without_restarting_http(tmp_path, monkeypatch):
    from personal_mcp.tunnel_supervisor import TunnelSupervisor
    from tests_personal.test_tunnel_supervisor import FakeRunner, eventually

    first, second = FakeRunner(), FakeRunner()
    first.failure = ValueError('TUNNEL_KEY_UNAVAILABLE')
    second.available = True
    runners = iter([first, second])
    monkeypatch.setattr('personal_mcp.tunnel_supervisor.TunnelRunner', lambda *args: next(runners))
    instance = LocalService(config(tmp_path), assets_root=ROOT / 'resources')
    try:
        instance.start(connect_tunnel=True)
        assert isinstance(instance.tunnel, TunnelSupervisor)
        eventually(lambda: instance.tunnel.snapshot()['error_code'] == 'TUNNEL_KEY_UNAVAILABLE')
        runtime, http_thread = instance.runtime, instance.http_thread
        status, response = post(instance, control=control_key(instance))
        assert status == 200 and response['result']['ok']
        eventually(lambda: instance.tunnel.snapshot()['status'] == 'healthy')
        assert instance.runtime is runtime and instance.http_thread is http_thread
        assert first.starts == second.starts == 1
        assert instance.status()['running']
    finally:
        instance.stop()
