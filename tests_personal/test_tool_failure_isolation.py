"""Local contract/fault tests; they do not simulate platform safety approval."""

import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from personal_mcp.bf.bootstrap import build_mcp
from personal_mcp.config import load_config
from personal_mcp.host import UnifiedRuntime
from tests_personal.test_config import installation
from tests_personal.test_host import call, host as host, workflow
from tests_personal.test_receipt_isolation import pending_native_work


@pytest.fixture
def full_enabled_host(tmp_path):
    """Match the browser-enabled catalog, rather than the 43-tool minimal fixture."""
    cfg = load_config(installation(tmp_path, permission_mode='full_control'))
    cfg.data_root.mkdir()
    browsers = cfg.data_root / 'browsers'
    browsers.mkdir()
    browser_config = cfg.data_root / 'browser.json'
    browser_config.write_text(json.dumps({'enabled': True, 'version': 1, 'mode': 'headless',
        'max_sessions': 4, 'max_pages_per_session': 4, 'python': sys.executable,
        'browsers_path': str(browsers), 'actions_enabled': True, 'transfers_enabled': True}), encoding='utf-8')
    bf = build_mcp(state_root=cfg.data_root / 'bf', allowed_root=cfg.workspace_root,
                   apps_dir=cfg.data_root / 'apps', full_control=True, browser_config_path=browser_config)
    runtime = UnifiedRuntime(cfg, auth_token='fixture-backend-' + 'x' * 32, bf_server=bf)
    try:
        yield runtime
    finally:
        runtime.close()


def exercise_invalid_input_isolation(host):
    token = workflow(host)
    resource = next(iter(host.registry.resources.values()))
    now = host.registry.status(host.principal, token)['last_activity'] + 1
    host.registry.clock = lambda: now
    before = host.registry.status(host.principal, token)
    names = {tool['name'] for tool in host.list_tools()['tools']}
    assert len(names) == 58 and {'BrowserAction', 'workflow_begin', 'workflow_end'} <= names
    observed = []
    for name in sorted(names):
        rejected = call(host, name, unexpected_field='fixture-must-not-be-executed')
        assert rejected['isError'] is True, name
        assert rejected['structuredContent']['error']['code'] == 'INVALID_ARGUMENT', name
        Draft202012Validator(host.catalog[name]['outputSchema']).validate(rejected['structuredContent'])
        # No valid input is sent to desktop, screenshot or browser operations.
        assert host.registry.status(host.principal, token) == before, name
        assert resource.coding.command_inventory() == [], name
        with host.operations._db() as db:
            assert db.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == 0, name
        assert call(host, 'workflow_status', workflow_id=token)['structuredContent']['state'] == 'ACTIVE', name
        observed.append({'tool': name, 'invalid_input_rejected': True,
                         'output_schema_valid': True, 'workflow_preserved': True})
    # Evidence comes from the exercised published names, not a hand-written count.
    target = host.config.data_root / 'failure-contract-report.json'
    target.write_text(json.dumps({'catalog_revision': host.catalog_revision,
                                 'permission_mode': host.config.permission_mode,
                                 'checks': observed}, indent=2), encoding='utf-8')
    assert len(observed) == len(host.catalog)
    assert call(host, 'read_file', workflow_id=token, path='note.txt')['structuredContent']['content'] == 'hello unified'


def test_all_published_tools_reject_invalid_input_without_poisoning_workflow(host):
    exercise_invalid_input_isolation(host)


def test_full_control_tools_also_have_valid_isolated_failures(full_enabled_host):
    (full_enabled_host.config.workspace_root / 'app/note.txt').write_text('hello unified', encoding='utf-8')
    exercise_invalid_input_isolation(full_enabled_host)


def test_all_tool_error_payloads_match_their_published_output_schema(host):
    invalid = []
    for tool in host.list_tools()['tools']:
        result = call(host, tool['name'], unexpected_field=True)
        assert result['isError'], tool['name']
        failures = list(Draft202012Validator(tool['outputSchema']).iter_errors(result['structuredContent']))
        if failures:
            invalid.append(tool['name'])
    assert invalid == [], f'Published output contracts reject their own error payloads: {invalid}'


def test_host_error_branch_does_not_fabricate_or_relax_successful_window_results(host):
    validator = Draft202012Validator(host.catalog['WindowInventory']['outputSchema'])
    assert validator.is_valid({'count': 0, 'windows': []})
    assert validator.is_valid({'ok': False, 'error': {'code': 'WORKFLOW_DENIED'}})
    assert not validator.is_valid({'ok': True, 'error': {'code': 'WORKFLOW_DENIED'}})
    assert not validator.is_valid({'ok': True})
    assert not validator.is_valid({'count': 0})
    assert not validator.is_valid({'ok': False, 'error': {'code': ''}})
    assert not validator.is_valid({'ok': False, 'error': {'code': 'WORKFLOW_DENIED'}, 'windows': []})


def test_expired_workflow_errors_are_also_valid_without_faking_backend_data(host):
    token = workflow(host)
    assert not call(host, 'UnifiedTask', action='end', workflow_id=token)['isError']
    result = call(host, 'read_file', workflow_id=token, path='note.txt')
    assert result['isError']
    assert result['structuredContent']['error']['code'] == 'WORKFLOW_INACTIVE'
    assert 'content' not in result['structuredContent']
    Draft202012Validator(host.catalog['read_file']['outputSchema']).validate(result['structuredContent'])


def test_pending_desktop_work_in_one_workflow_does_not_block_another_projects_code(host):
    first = workflow(host)
    other_path = host.config.workspace_root / 'other'
    other_path.mkdir()
    begin = call(host, 'UnifiedTask', action='begin', project_path='other', request_id='other-project')
    second = begin['structuredContent']['workflow_id']
    assert not call(host, 'UnifiedTask', action='activate', workflow_id=second)['isError']
    with pending_native_work(host):
        rejected = call(host, 'apply_patch', workflow_id=first, request_id='fenced',
                        patch='*** Begin Patch\n*** Add File: blocked.txt\n+must-not-write\n*** End Patch')
        assert rejected['structuredContent']['error']['code'] == 'WORKFLOW_WRITE_PENDING'
        accepted = call(host, 'apply_patch', workflow_id=second, request_id='independent',
                        patch='*** Begin Patch\n*** Add File: result.txt\n+independent-project\n*** End Patch')
        assert not accepted['isError'], accepted
        assert (other_path / 'result.txt').read_text().strip() == 'independent-project'
        assert not (Path(host.config.workspace_root) / 'app/blocked.txt').exists()
