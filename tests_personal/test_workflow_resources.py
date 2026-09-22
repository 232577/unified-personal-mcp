import threading
from concurrent.futures import Future
from types import SimpleNamespace as NS

import pytest

from personal_mcp.host import WorkflowResources


def resource():
    item = WorkflowResources.__new__(WorkflowResources)
    item.key, item.bf_token = 'workflow', 'bf-token'
    store = NS(_lock=threading.RLock(), _owned_jobs={},
               require=lambda _: (None, {'task_key': 'task'}))
    browser = NS(lock=threading.RLock(), sessions={})
    hybrid = NS(_lock=threading.RLock(), instances={}, _pending={}, _failed_launches={})
    item.coding = NS(owned_job=NS(active_pids=lambda: []))
    item.host = NS(
        bf=NS(lock=threading.RLock(), pending={}, closed=False, failure=None),
        bf_server=NS(_bf_store=store, _bf_browser_manager=browser, _bf_hybrid_manager=hybrid),
        searches=NS(idle_blockers=lambda _: []))
    return item


def test_empty_and_other_owner_resources_do_not_block_idle_cleanup():
    item = resource()
    item.host.bf.pending['other'] = {Future()}
    item.host.bf_server._bf_store._owned_jobs['other'] = [NS(active_pids=lambda: [99])]
    item.host.bf_server._bf_browser_manager.sessions['other'] = NS(owner='other')
    item.host.bf_server._bf_hybrid_manager.instances['other'] = NS(owner='other')
    assert item.idle_blockers() == []


@pytest.mark.parametrize('kind', ['command', 'desktop', 'native', 'browser', 'hybrid',
                                  'pending_hybrid', 'failed_hybrid', 'search'])
def test_owned_live_resources_prevent_idle_cleanup(kind):
    item = resource()
    server = item.host.bf_server
    if kind == 'command':
        item.coding.owned_job.active_pids = lambda: [7]
    elif kind == 'desktop':
        item.host.bf.pending[item.bf_token] = {Future()}
    elif kind == 'native':
        server._bf_store._owned_jobs['task'] = [NS(active_pids=lambda: [7])]
    elif kind == 'browser':
        server._bf_browser_manager.sessions['session'] = NS(owner='task')
    elif kind == 'hybrid':
        server._bf_hybrid_manager.instances['instance'] = NS(owner='task')
    elif kind == 'pending_hybrid':
        server._bf_hybrid_manager._pending['instance'] = 'task'
    elif kind == 'failed_hybrid':
        server._bf_hybrid_manager._failed_launches['instance'] = ('task', object())
    else:
        item.host.searches.idle_blockers = lambda _: ['running_search']
    assert item.idle_blockers()


def test_failed_resource_inspection_is_not_reported_as_idle():
    item = resource()
    item.host.bf.failure = RuntimeError('unavailable')
    with pytest.raises(RuntimeError):
        item.idle_blockers()


def test_finished_operations_and_commands_allow_idle_cleanup():
    item = resource()
    done = Future()
    done.set_result(None)
    item.host.bf.pending[item.bf_token] = {done}
    item.host.bf_server._bf_store._owned_jobs['task'] = [NS(active_pids=lambda: [])]
    assert item.idle_blockers() == []
