"""Client-visible contracts; these tests do not assert platform approval."""

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from mcp.types import Tool

from personal_mcp.catalog import unified_catalog, validate_catalog_contract
from tests_personal.test_call_diagnostics import http_host, post
from tests_personal.test_host import call, host as host, workflow
from tests_personal.test_tool_failure_isolation import full_enabled_host as full_enabled_host


def test_actual_http_catalog_has_consistent_human_readable_titles(host):
    with http_host(host) as url:
        result = post(url, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, host.auth_token)
    tools = result['result']['tools']
    assert len(tools) == 58
    for descriptor in tools:
        name = descriptor['name']
        assert descriptor.get('title', '').strip(), name
        assert ' ' in descriptor['title'], name
        assert descriptor['annotations'].get('title') == descriptor['title'], name
        parsed = Tool.model_validate(descriptor)
        assert parsed.name == name and parsed.title == descriptor['title']
        assert parsed.model_dump(by_alias=True)['inputSchema'] == descriptor['inputSchema']
    assert host.catalog['Notification']['title'] == 'Send Windows notification'
    assert host.catalog['request_permissions']['title'] == 'Inspect local permission status'


@pytest.mark.parametrize('name', ['read_output', 'write_stdin', 'kill_command',
                                 'request_permissions', 'check_exec_environment', 'git_log'])
def test_non_path_operations_do_not_claim_arbitrary_paths_or_command_directories(full_enabled_host, name):
    description = full_enabled_host.catalog[name]['description']
    assert 'absolute paths' not in description
    assert 'Set workdir' not in description
    assert 'owner-authorized for full local control' not in description


def test_file_scope_description_matches_the_selected_installation(full_enabled_host):
    bounded_catalog = unified_catalog(full_enabled_host.bf_server, full_control=False)
    bounded = bounded_catalog['read_file']['description']
    unrestricted = full_enabled_host.catalog['read_file']['description']
    assert 'workflow project' in bounded
    assert 'absolute paths outside the workflow project' in unrestricted
    assert 'inside the configured workspace' not in unrestricted
    assert 'Windows user' in unrestricted
    # Descriptions must not be used to change permissions or effects.
    assert bounded_catalog['read_file']['annotations']['readOnlyHint'] is True
    assert full_enabled_host.catalog['read_file']['annotations']['openWorldHint'] is True
    assert full_enabled_host.catalog['exec_command']['annotations']['destructiveHint'] is True


def test_action_enum_has_explicit_type_without_changing_accepted_actions(host):
    schema = host.catalog['UnifiedTask']['inputSchema']
    assert schema['properties']['action'].get('type') == 'string'
    for branch in schema['oneOf']:
        assert branch['properties']['action'].get('type') == 'string'
    validator = Draft202012Validator(schema)
    assert validator.is_valid({'action': 'list'})
    assert not validator.is_valid({'action': True})
    assert not validator.is_valid({'action': 'anything-else'})
    assert not validator.is_valid({'action': 'list', 'project_path': 'app'})


def test_credential_and_receipt_inputs_explain_their_distinct_sources(host):
    for tool in host.catalog.values():
        inputs = tool['inputSchema']
        for field in ('workflow_id', 'workflow_ref', 'request_id'):
            if field in inputs['properties']:
                assert inputs['properties'][field].get('description', '').strip(), (tool['name'], field)
        for branch in inputs.get('oneOf', []):
            for field in ('workflow_id', 'workflow_ref', 'request_id'):
                if field in branch.get('properties', {}):
                    assert branch['properties'][field].get('description', '').strip(), (tool['name'], field)
    lookup = host.catalog['OperationStatus']['inputSchema']['properties']['request_id']['description']
    mutation = host.catalog['exec_command']['inputSchema']['properties']['request_id']['description']
    assert 'original' in lookup and 'new operation' in mutation
    generation = host.catalog['UnifiedTask']['inputSchema']['properties']['expected_generation']
    assert 'credential_generation' in generation.get('description', '')


def test_local_permission_report_and_toast_do_not_claim_platform_approval(host):
    permission = host.catalog['request_permissions']['description']
    toast = host.catalog['Notification']['description']
    assert 'does not display an approval dialog' in permission
    assert 'platform safety' in permission
    assert 'not an approval dialog' in toast
    token = workflow(host, access='read')
    result = call(host, 'request_permissions', workflow_id=token, tool_name='exec_command',
                  permission='network', reason='fixture', arguments={'cmd': 'echo fixture'})
    assert result['isError']
    assert result['structuredContent']['error']['code'] == 'ELICITATION_UNSUPPORTED'
    assert next(iter(host.registry.resources.values())).coding.command_inventory() == []


@pytest.mark.parametrize('value', [None, '', '   ', 7])
def test_gate_rejects_invalid_display_titles(host, value):
    catalog = deepcopy(host.catalog)
    catalog['read_file']['title'] = value
    with pytest.raises(ValueError, match='CATALOG_CONTRACT_INVALID'):
        validate_catalog_contract(catalog)


def test_gate_rejects_conflicting_title_representations(host):
    catalog = deepcopy(host.catalog)
    catalog['read_file']['title'] = 'Read file text'
    catalog['read_file']['annotations']['title'] = 'Execute commands'
    with pytest.raises(ValueError, match='CATALOG_CONTRACT_INVALID'):
        validate_catalog_contract(catalog)


def test_field_help_does_not_break_real_command_or_input_validation(host):
    token = workflow(host)
    result = call(host, 'exec_command', workflow_id=token, request_id='presentation-command',
                  cmd='echo presentation-fixture', yield_time_ms=1000)
    assert result['structuredContent']['exit_code'] == 0
    assert 'presentation-fixture' in result['structuredContent']['stdout']
    Draft202012Validator(host.catalog['exec_command']['outputSchema']).validate(result['structuredContent'])
    invalid = call(host, 'workflow_list', approved=True)
    assert invalid['isError'] and invalid['structuredContent']['error']['code'] == 'INVALID_ARGUMENT'
