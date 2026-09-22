# Imported pytest fixtures intentionally share names with test parameters.
# ruff: noqa: F811
import pytest

from tests_personal.test_full_control_host import full_host  # noqa: F401
from tests_personal.test_host import host  # noqa: F401


def task(host, **args):
    return host.call_tool('UnifiedTask', args)['structuredContent']


def test_busy_project_identifies_owner_and_lost_token_can_be_reclaimed(full_host):
    now = [10000.0]
    full_host.registry.clock = lambda: now[0]
    started = task(full_host, action='begin', project_path='app', request_id='first')
    token = started['workflow_id']
    assert task(full_host, action='activate', workflow_id=token)['ok']
    busy = task(full_host, action='begin', project_path='app', request_id='second')
    blocker = busy['error']['details']['blockers'][0]
    assert blocker['project'] == str(full_host.config.project('app'))
    assert blocker['relation'] == 'same'
    rows = task(full_host, action='list')['workflows']
    assert rows[0]['workflow_ref'] == blocker['workflow_ref']
    assert 'workflow_id' not in rows[0]
    denied = task(full_host, action='release_idle', workflow_ref=blocker['workflow_ref'])
    assert denied['error']['code'] == 'WORKFLOW_NOT_IDLE'
    now[0] += full_host.config.workflow_idle_seconds + 1
    ended = task(full_host, action='release_idle', workflow_ref=blocker['workflow_ref'])
    assert ended['ok'] and ended['state'] == 'ENDED'
    assert task(full_host, action='activate', workflow_id=token)['error']['code'] == 'WORKFLOW_INACTIVE'
    assert task(full_host, action='begin', project_path='app', request_id='retry')['ok']


@pytest.mark.parametrize('args', [
    {'action': 'list', 'project_path': 'app'},
    {'action': 'list', 'workflow_id': 'wf_' + 'x' * 43},
    {'action': 'release_idle'},
    {'action': 'release_idle', 'workflow_ref': 'wfr_' + 'a' * 64, 'ttl': 30},
    {'action': 'status', 'workflow_ref': 'wfr_' + 'a' * 64},
    {'action': 'begin', 'project_path': 'app', 'request_id': 'x', 'workflow_ref': 'wfr_' + 'a' * 64},
])
def test_management_actions_reject_ambiguous_arguments(full_host, args):
    assert task(full_host, **args)['error']['code'] == 'INVALID_ARGUMENT'


def test_discovery_reports_actual_resource_limits(host):
    info = host.call_tool('server_info', {})['structuredContent']
    capacity = info['concurrency']
    assert capacity['project_limit'] is None
    assert capacity['commands_per_workflow'] == 16
    assert capacity['search_sessions'] == host.config.search_sessions
    assert capacity['browser_sessions'] == 4
    assert capacity['foreground_input'] == 1
    assert capacity['workflow_idle_seconds'] == host.config.workflow_idle_seconds
    assert len(host.list_tools()['tools']) == 49
