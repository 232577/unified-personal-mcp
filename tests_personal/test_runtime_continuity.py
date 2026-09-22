import threading

from personal_mcp.catalog import catalog_revision
from tests_personal.test_host import call, host as host, workflow


def test_health_and_catalog_are_stable_and_do_not_wait_for_workflow_gate(host):
    token = workflow(host)
    gate = host.registry.workflow_locks[host.registry._hash(token)]
    acquired, finish = threading.Event(), threading.Event()

    def hold():
        with gate:
            acquired.set()
            finish.wait(3)

    thread = threading.Thread(target=hold)
    thread.start()
    assert acquired.wait(2)
    try:
        info = call(host, 'server_info')['structuredContent']
        assert info['health']['bf']['status'] == 'healthy'
        assert info['catalog_revision'] == catalog_revision(host.catalog)
        assert info['resource_usage']['operation_results']['max_retained_bytes'] > 0
    finally:
        finish.set()
        thread.join(3)


def test_operation_status_is_owner_scoped_and_coding_result_is_recoverable(host):
    token = workflow(host)
    result = call(host, 'exec_command', workflow_id=token, request_id='run',
                  cmd='echo recovered', yield_time_ms=0)
    assert not result.get('isError'), result
    status = call(host, 'OperationStatus', workflow_id=token, request_id='run')['structuredContent']
    assert status['state'] == 'completed'
    assert status['result'] == result
    assert call(host, 'OperationStatus', workflow_id=token, request_id='absent')['isError']


def test_catalog_fingerprint_excludes_descriptions_but_detects_schema_change(host):
    before = catalog_revision(host.catalog)
    host.catalog['server_info']['description'] += ' reworded'
    assert catalog_revision(host.catalog) == before
    host.catalog['server_info']['inputSchema']['properties']['new_field'] = {'type': 'string'}
    assert catalog_revision(host.catalog) != before


def test_workflow_status_inventory_does_not_renew_idle_lease(host):
    token = workflow(host)
    initial = host.registry.status(host.principal, token)['last_activity']
    host.registry.clock = lambda: initial + 10
    current = call(host, 'UnifiedTask', action='status', workflow_id=token)['structuredContent']
    assert current['last_activity'] == initial and current['idle_seconds'] == 10
    assert host.registry.status(host.principal, token)['last_activity'] == initial


def test_readonly_timeout_does_not_direct_to_nonexistent_operation_receipt(host, monkeypatch):
    token = workflow(host)

    def timeout(*args, **kwargs):
        raise TimeoutError('wait ended')

    monkeypatch.setattr(host.bf, 'call', timeout)
    result = call(host, 'Wait', workflow_id=token, duration=0)['structuredContent']
    assert 'OperationStatus' not in result['error']['next_action']
    assert 'server_info' in result['error']['next_action']
