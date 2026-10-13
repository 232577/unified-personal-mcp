"""Explicit lifecycle contracts with the same authorization and receipts."""

import pytest
from jsonschema import Draft202012Validator

from personal_mcp.workflows import AuthPrincipal
from tests_personal.test_call_diagnostics import http_host, post
from tests_personal.test_host import call, host as host

NAMES = {'workflow_begin', 'workflow_activate', 'workflow_resume', 'workflow_end', 'workflow_release_idle'}


def named(host, name, **arguments):
    assert name in host.catalog, f'explicit action {name} is missing'
    result = call(host, name, **arguments)
    Draft202012Validator(host.catalog[name]['outputSchema']).validate(result['structuredContent'])
    return result['structuredContent']


def test_named_lifecycle_contracts_do_not_expose_an_action_selector(host):
    assert NAMES <= host.catalog.keys()
    for name in NAMES:
        descriptor = host.catalog[name]
        inputs = descriptor['inputSchema']
        assert inputs['additionalProperties'] is False
        assert 'action' not in inputs['properties']
        assert 'oneOf' not in inputs
        assert descriptor['annotations']['readOnlyHint'] is False
        assert descriptor['annotations']['destructiveHint'] is True
    assert host.catalog['UnifiedTask']['title'] == 'Manage workflow (legacy compatibility)'


def test_explicit_actions_work_through_real_authenticated_http(host):
    assert NAMES <= host.catalog.keys()
    with http_host(host) as url:
        def invoke(name, **arguments):
            response = post(url, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': name, 'arguments': arguments}}, host.auth_token)
            result = response['result']['structuredContent']
            Draft202012Validator(host.catalog[name]['outputSchema']).validate(result)
            return result
        started = invoke('workflow_begin', project_path='app', request_id='http-begin')
        assert started['state'] == 'RESERVED'
        token = started['workflow_id']
        assert invoke('workflow_activate', workflow_id=token)['state'] == 'ACTIVE'
        listed = invoke('workflow_list')['workflows'][0]
        resumed = invoke('workflow_resume', workflow_ref=listed['workflow_ref'],
                         expected_generation=listed['credential_generation'], request_id='http-resume')
        assert resumed['credential_generation'] == listed['credential_generation'] + 1
        assert invoke('workflow_status', workflow_id=token)['error']['code'] == 'WORKFLOW_CREDENTIAL_REPLACED'
        assert invoke('workflow_end', workflow_id=resumed['workflow_id'])['state'] == 'ENDED'
        assert invoke('workflow_list')['workflows'] == []


def test_legacy_and_named_actions_share_deduplication_and_live_resources(host):
    started = named(host, 'workflow_begin', project_path='app', request_id='same-begin')
    token = started['workflow_id']
    duplicate = call(host, 'UnifiedTask', action='begin', project_path='app', request_id='same-begin')['structuredContent']
    assert duplicate['code'] == 'BEGIN_ALREADY_ACCEPTED' and 'workflow_id' not in duplicate
    assert call(host, 'UnifiedTask', action='activate', workflow_id=token)['structuredContent']['state'] == 'ACTIVE'
    resource = next(iter(host.registry.resources.values()))
    row = call(host, 'workflow_list')['structuredContent']['workflows'][0]
    args = dict(workflow_ref=row['workflow_ref'], expected_generation=row['credential_generation'], request_id='same-resume')
    resumed = named(host, 'workflow_resume', **args)
    assert call(host, 'UnifiedTask', action='resume', **args)['structuredContent'] == resumed
    assert next(iter(host.registry.resources.values())) is resource
    assert named(host, 'workflow_end', workflow_id=resumed['workflow_id'])['state'] == 'ENDED'


@pytest.mark.parametrize('name', sorted(NAMES))
def test_named_actions_reject_injected_action_and_authority(host, name):
    rejected = named(host, name, action='list', principal='local-owner')
    assert rejected['error']['code'] == 'INVALID_ARGUMENT'
    assert host.registry.list(host.principal)['workflows'] == []


def test_named_read_workflow_still_denies_execution(host):
    token = named(host, 'workflow_begin', project_path='app', request_id='read', access='read')['workflow_id']
    assert named(host, 'workflow_activate', workflow_id=token)['state'] == 'ACTIVE'
    result = call(host, 'exec_command', workflow_id=token, request_id='denied', cmd='echo must-not-run')
    assert result['structuredContent']['error']['code'] == 'READ_ONLY_WORKFLOW'
    assert next(iter(host.registry.resources.values())).coding.command_inventory() == []


def test_named_release_preserves_idle_and_owner_guards(host):
    token = named(host, 'workflow_begin', project_path='app', request_id='idle')['workflow_id']
    row = named(host, 'workflow_activate', workflow_id=token)
    assert named(host, 'workflow_release_idle', workflow_ref=row['workflow_ref'])['error']['code'] == 'WORKFLOW_NOT_IDLE'
    owner = host.principal
    try:
        host.principal = AuthPrincipal('different-owner')
        assert named(host, 'workflow_end', workflow_id=token)['error']['code'] == 'WORKFLOW_DENIED'
        assert named(host, 'workflow_release_idle', workflow_ref=row['workflow_ref'])['error']['code'] == 'WORKFLOW_DENIED'
    finally:
        host.principal = owner
    host.registry.clock = lambda: row['last_activity'] + host.config.workflow_idle_seconds + 1
    assert named(host, 'workflow_release_idle', workflow_ref=row['workflow_ref'])['state'] == 'ENDED'
