"""Reject malformed tool metadata before it can be published to a client."""

from copy import deepcopy

import pytest

from personal_mcp import catalog as catalog_module
from tests_personal.test_host import host as host


def validate(tools):
    validator = getattr(catalog_module, 'validate_catalog_contract', None)
    assert callable(validator), 'the published catalog has no contract validation gate'
    return validator(tools)


def test_actual_published_catalog_passes_gate_without_mutation(host):
    before = deepcopy(host.catalog)
    validate(host.catalog)
    assert host.catalog == before


@pytest.mark.parametrize('hint', ['readOnlyHint', 'destructiveHint', 'openWorldHint'])
@pytest.mark.parametrize('value', [None, 'false', 0])
def test_gate_rejects_non_boolean_required_annotations(host, hint, value):
    tools = deepcopy(host.catalog)
    tools['read_file']['annotations'][hint] = value
    with pytest.raises(ValueError, match='CATALOG_CONTRACT_INVALID'):
        validate(tools)


@pytest.mark.parametrize('key', ['inputSchema', 'outputSchema'])
def test_gate_rejects_invalid_schema(host, key):
    tools = deepcopy(host.catalog)
    tools['read_file'][key] = {'type': 'not-a-json-schema-type'}
    with pytest.raises(ValueError, match='CATALOG_CONTRACT_INVALID'):
        validate(tools)


@pytest.mark.parametrize('change', ['missing-hint', 'wrong-name', 'empty-description',
                                   'write-marked-readonly', 'unknown-tool'])
def test_gate_rejects_incomplete_or_inconsistent_contracts(host, change):
    tools = deepcopy(host.catalog)
    if change == 'missing-hint':
        del tools['read_file']['annotations']['openWorldHint']
    elif change == 'wrong-name':
        tools['read_file']['name'] = 'not_read_file'
    elif change == 'empty-description':
        tools['read_file']['description'] = '  '
    elif change == 'write-marked-readonly':
        tools['exec_command']['annotations']['readOnlyHint'] = True
        tools['exec_command']['annotations']['destructiveHint'] = False
    else:
        tools['unreviewed'] = deepcopy(tools['read_file'])
        tools['unreviewed']['name'] = 'unreviewed'
    with pytest.raises(ValueError, match='CATALOG_CONTRACT_INVALID'):
        validate(tools)


def test_actual_catalog_builder_rejects_broken_provider_metadata(host, monkeypatch):
    original = catalog_module.tool_definition

    def broken_definition(name):
        tool = deepcopy(original(name))
        if name == 'read_file':
            tool['annotations']['openWorldHint'] = None
        return tool

    monkeypatch.setattr(catalog_module, 'tool_definition', broken_definition)
    with pytest.raises(ValueError, match='CATALOG_CONTRACT_INVALID'):
        catalog_module.unified_catalog(host.bf_server)


def test_stdin_declares_possible_command_side_effects(host):
    hints = host.catalog['write_stdin']['annotations']
    assert hints['readOnlyHint'] is False
    assert hints['destructiveHint'] is True
    assert hints['openWorldHint'] is True


def test_shared_clipboard_replacement_declares_overwrite(host):
    assert host.catalog['Clipboard']['annotations']['destructiveHint'] is True


@pytest.mark.parametrize('name', ['read_file', 'list_dir', 'list_files', 'search_text',
                                  'apply_patch', 'view_image'])
def test_full_control_file_scope_is_not_advertised_as_a_bounded_project(host, name):
    bounded = catalog_module.unified_catalog(host.bf_server, full_control=False)
    unrestricted = catalog_module.unified_catalog(host.bf_server, full_control=True)
    assert bounded[name]['annotations']['openWorldHint'] is False
    assert unrestricted[name]['annotations']['openWorldHint'] is True
    assert bounded[name]['annotations']['readOnlyHint'] == unrestricted[name]['annotations']['readOnlyHint']


@pytest.mark.parametrize('name,hint', [('write_stdin', 'destructiveHint'),
                                     ('write_stdin', 'openWorldHint'),
                                     ('Clipboard', 'destructiveHint')])
def test_gate_rejects_understated_reviewed_effects(host, name, hint):
    tools = deepcopy(host.catalog)
    tools[name]['annotations'][hint] = False
    with pytest.raises(ValueError, match='CATALOG_CONTRACT_INVALID'):
        validate(tools)


def test_invalid_catalog_is_rejected_before_runtime_resource_initialization(tmp_path, monkeypatch):
    from personal_mcp import host as host_module
    from personal_mcp.config import load_config
    from tests_personal.test_config import installation

    def rejected_catalog(*args, **kwargs):
        raise ValueError('CATALOG_CONTRACT_INVALID')

    def unexpected_initialization(*args, **kwargs):
        pytest.fail('runtime resources were initialized before the catalogue gate')

    monkeypatch.setattr(host_module, 'unified_catalog', rejected_catalog)
    monkeypatch.setattr(host_module.Runtime, '__init__', unexpected_initialization)
    config = load_config(installation(tmp_path))
    with pytest.raises(ValueError, match='CATALOG_CONTRACT_INVALID'):
        host_module.UnifiedRuntime(config, auth_token='fixture-only-' + 'x' * 32, bf_server=object())


def test_full_control_gate_rejects_downgraded_file_scope(host):
    tools = catalog_module.unified_catalog(host.bf_server, full_control=True)
    tools['apply_patch']['annotations']['openWorldHint'] = False
    with pytest.raises(ValueError, match='CATALOG_CONTRACT_INVALID'):
        catalog_module.validate_catalog_contract(tools, full_control=True)
