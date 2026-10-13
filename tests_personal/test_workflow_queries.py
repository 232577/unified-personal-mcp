"""Dedicated query tools preserve ownership and do not mutate workflow state."""
# ruff: noqa: F811
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from jsonschema import Draft202012Validator

from personal_mcp.workflows import AuthPrincipal
from tests_personal.test_call_diagnostics import http_host, post
from tests_personal.test_host import host, workflow  # noqa: F401


def query(host, name, **arguments):
    assert name in host.catalog, f'dedicated read-only query {name} is missing'
    result = host.call_tool(name, arguments)
    Draft202012Validator(host.catalog[name]['outputSchema']).validate(result['structuredContent'])
    return result


def stored_state(host):
    with host.registry._db() as db:
        return {table: [dict(row) for row in db.execute(f'SELECT * FROM {table}')]
                for table in ('workflows', 'workflow_credentials')}


def test_served_query_contracts_are_narrow_and_explicitly_readonly(host):
    with http_host(host) as url:
        response = post(url, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, host.auth_token)
        catalog = {tool['name']: tool for tool in response['result']['tools']}
        assert {'workflow_list', 'workflow_status'} <= catalog.keys()
        for name, title in (('workflow_list', 'List owned workflows'),
                            ('workflow_status', 'Inspect an owned workflow')):
            tool = catalog[name]
            assert tool['annotations'] == {'readOnlyHint': True, 'destructiveHint': False,
                                           'idempotentHint': True, 'openWorldHint': False, 'title': title}
            assert tool['inputSchema']['additionalProperties'] is False
            Draft202012Validator.check_schema(tool['inputSchema'])
            Draft202012Validator.check_schema(tool['outputSchema'])
        assert catalog['workflow_list']['inputSchema']['properties'] == {}
        assert catalog['workflow_list']['inputSchema']['required'] == []
        assert set(catalog['workflow_status']['inputSchema']['properties']) == {'workflow_id'}
        assert catalog['workflow_status']['inputSchema']['required'] == ['workflow_id']
        assert catalog['UnifiedTask']['annotations']['readOnlyHint'] is False
        result = post(url, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
            'params': {'name': 'workflow_list', 'arguments': {}}}, host.auth_token)
        assert result['result']['structuredContent'] == {'ok': True, 'workflows': []}


def test_list_needs_no_workflow_and_creates_no_resource(host):
    before = stored_state(host)
    result = query(host, 'workflow_list')
    assert result['structuredContent'] == {'ok': True, 'workflows': []}
    assert stored_state(host) == before
    assert not host.registry.resources


def test_queries_match_legacy_results_without_mutating_or_cleaning_state(host, monkeypatch):
    token = workflow(host, access='read')
    initial = host.registry.status(host.principal, token)['last_activity']
    host.registry.clock = lambda: initial + 10
    before = stored_state(host)
    resources = dict(host.registry.resources)
    def forbidden(*args, **kwargs):
        pytest.fail('a read-only query attempted cleanup or lease renewal')
    monkeypatch.setattr(host.registry, '_touch', forbidden)
    monkeypatch.setattr(host.registry, 'expire', forbidden)
    monkeypatch.setattr(host.registry, '_end', forbidden)
    for _ in range(3):
        listing = query(host, 'workflow_list')['structuredContent']
        status = query(host, 'workflow_status', workflow_id=token)['structuredContent']
        assert listing == host.call_tool('UnifiedTask', {'action': 'list'})['structuredContent']
        assert status == host.call_tool('UnifiedTask', {'action': 'status', 'workflow_id': token})['structuredContent']
        assert status['last_activity'] == initial and status['idle_seconds'] == 10
        assert status['commands'] == []
        assert status['job_processes']['active_process_count'] == 0
        assert token not in json.dumps(listing) + json.dumps(status)
    assert stored_state(host) == before
    assert host.registry.resources == resources
    assert not host.registry.inflight
    monkeypatch.undo()  # The fixture teardown is allowed to end its own resources.


@pytest.mark.parametrize('name,args', [
    ('workflow_list', {'action': 'end'}),
    ('workflow_list', {'project_path': 'app'}),
    ('workflow_list', {'workflow_id': 'wf_' + 'x' * 43}),
    ('workflow_list', {'principal': 'other'}),
    ('workflow_status', {}),
    ('workflow_status', {'workflow_ref': 'wfr_' + 'a' * 64}),
    ('workflow_status', {'workflow_id': 'bad'}),
    ('workflow_status', {'workflow_id': 'wf_' + 'x' * 43, 'action': 'end'}),
])
def test_queries_reject_ambiguous_or_injected_arguments(host, name, args):
    before = stored_state(host)
    result = query(host, name, **args)
    assert result['isError'] and result['structuredContent']['error']['code'] == 'INVALID_ARGUMENT'
    assert stored_state(host) == before
    assert not host.registry.resources


def test_queries_preserve_cross_owner_denial_and_never_return_secrets(host):
    token = workflow(host, access='read')
    other = host.registry.begin(AuthPrincipal('other'), 'app', 'foreign', access='read')['workflow_id']
    listing = query(host, 'workflow_list')['structuredContent']
    assert len(listing['workflows']) == 1
    assert query(host, 'workflow_status', workflow_id=other)['structuredContent']['error']['code'] == 'WORKFLOW_DENIED'
    assert token not in json.dumps(listing) and other not in json.dumps(listing)


def test_status_preserves_reserved_and_ended_states_without_activation(host):
    result = host.call_tool('UnifiedTask', {'action': 'begin', 'project_path': 'app', 'request_id': 'reserve'})
    token = result['structuredContent']['workflow_id']
    assert query(host, 'workflow_status', workflow_id=token)['structuredContent']['state'] == 'RESERVED'
    assert not host.registry.resources
    host.call_tool('UnifiedTask', {'action': 'end', 'workflow_id': token})
    assert query(host, 'workflow_status', workflow_id=token)['structuredContent']['state'] == 'ENDED'
    assert query(host, 'workflow_list')['structuredContent']['workflows'] == []
    assert not host.registry.resources


def test_status_rejects_replaced_credentials_and_keeps_resource(host):
    old = workflow(host, access='read')
    before = host.registry.status(host.principal, old)
    resource = next(iter(host.registry.resources.values()))
    resumed = host.registry.resume(host.principal, before['workflow_ref'], 'resume-query-test',
                                   before['credential_generation'])
    denied = query(host, 'workflow_status', workflow_id=old)
    assert denied['structuredContent']['error']['code'] == 'WORKFLOW_CREDENTIAL_REPLACED'
    current = query(host, 'workflow_status', workflow_id=resumed['workflow_id'])['structuredContent']
    assert current['state'] == 'ACTIVE'
    assert current['credential_generation'] == before['credential_generation'] + 1
    assert next(iter(host.registry.resources.values())) is resource


def test_queries_do_not_wait_for_a_long_write_or_renew_its_lease(host):
    token = workflow(host)
    entered, release = threading.Event(), threading.Event()
    def writing():
        with host.registry.use(host.principal, token, write=True):
            entered.set()
            assert release.wait(5)
    with ThreadPoolExecutor(2) as pool:
        running = pool.submit(writing)
        assert entered.wait(2)
        before = stored_state(host)
        try:
            assert pool.submit(query, host, 'workflow_status', workflow_id=token).result(timeout=1)['structuredContent']['state'] == 'ACTIVE'
            assert len(pool.submit(query, host, 'workflow_list').result(timeout=1)['structuredContent']['workflows']) == 1
            assert stored_state(host) == before
        finally:
            release.set()
        running.result(timeout=2)


def test_status_reports_actual_owned_command_inventory(host):
    token = workflow(host)
    result = host.call_tool('exec_command', {'workflow_id': token, 'request_id': 'inventory-fixture',
        'cmd': 'echo query-fixture', 'yield_time_ms': 1000})
    assert not result['isError'], result
    command_id = result['structuredContent']['command_id']
    status = query(host, 'workflow_status', workflow_id=token)['structuredContent']
    assert any(command['command_id'] == command_id for command in status['commands'])
    assert status['job_processes']['diagnostic_only'] is True
    assert 'query-fixture' not in json.dumps(status)
