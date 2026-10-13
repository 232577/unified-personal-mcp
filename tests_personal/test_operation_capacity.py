"""Closed workflows cannot permanently exhaust the operation journal."""

import pytest

from tests_personal.test_host import call, host as host


def begin(host, request):
    result = call(host, 'workflow_begin', project_path='app', request_id=request)
    token = result['structuredContent']['workflow_id']
    assert not call(host, 'workflow_activate', workflow_id=token)['isError']
    return token


def failed_patch(host, token, request='patch'):
    return call(host, 'apply_patch', workflow_id=token, request_id=request,
                patch='*** Begin Patch\n*** Update File: absent.txt\n@@\n-old\n+new\n*** End Patch')


def test_completed_workflows_reclaim_capacity_without_reviving_old_credentials(host):
    host.operations.max_records = 1
    old = begin(host, 'first')
    original = failed_patch(host, old)
    assert original['isError']
    assert original['structuredContent']['error']['code'] != 'OPERATION_CAPACITY'
    assert call(host, 'workflow_end', workflow_id=old)['structuredContent']['state'] == 'ENDED'

    current = begin(host, 'second')
    result = failed_patch(host, current)
    assert result['structuredContent']['error']['code'] != 'OPERATION_CAPACITY'
    assert result['isError']
    assert failed_patch(host, old)['structuredContent']['error']['code'] == 'WORKFLOW_INACTIVE'
    assert failed_patch(host, current) == result


@pytest.mark.parametrize('state', ['ACTIVE', 'CLEANUP_BLOCKED', 'CLOSING'])
def test_nonterminal_workflow_receipts_remain_at_capacity(host, state):
    host.operations.max_records = 1
    token = begin(host, 'first')
    original = failed_patch(host, token)
    key = next(iter(host.registry.resources))
    host.registry._state(key, state)
    try:
        # Exercise the journal admission while the authority still belongs to
        # a nonterminal workflow; its deduplication receipt must not disappear.
        with pytest.raises(ValueError, match='OPERATION_CAPACITY'):
            host.operations.run('different-owner', 'new', 'fixture', {},
                                lambda: pytest.fail('must not dispatch'))
        assert host.operations.status(key, 'patch')['result'] == original
    finally:
        host.registry._state(key, 'ACTIVE')
