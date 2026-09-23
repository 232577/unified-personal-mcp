"""Job diagnostics include living descendants after their command roots exit."""

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests_personal.test_host import call, host as host, workflow


@pytest.fixture
def orphan(host):
    token = workflow(host)
    resource = next(iter(host.registry.resources.values()))
    marker = host.config.workspace_root / 'orphan.pid'
    code = ('import subprocess,sys;from pathlib import Path;'
            'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],'
            'creationflags=subprocess.CREATE_NO_WINDOW,stdin=subprocess.DEVNULL,'
            'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);'
            'Path(sys.argv[1]).write_text(str(p.pid))')
    result = call(host, 'exec_command', workflow_id=token, request_id='spawn-orphan',
                  cmd=subprocess.list2cmdline([sys.executable, '-c', code, str(marker)]),
                  yield_time_ms=0)['structuredContent']
    deadline = time.monotonic() + 10
    while result['exit_code'] is None and time.monotonic() < deadline:
        result = call(host, 'write_stdin', workflow_id=token, request_id='poll',
                      command_id=result['command_id'], chars='', yield_time_ms=100)['structuredContent']
    assert result['exit_code'] == 0, result
    child = int(marker.read_text())
    assert child in resource.coding.owned_job.active_pids()
    return token, resource, child


def status(host, token):
    result = call(host, 'UnifiedTask', action='status', workflow_id=token)
    assert not result.get('isError'), result
    return result['structuredContent']


def usage(host):
    return call(host, 'server_info')['structuredContent']['resource_usage']


def test_exited_roots_do_not_hide_live_job_descendants(host, orphan):
    token, resource, child = orphan
    observed = status(host, token)
    assert all(item['status'] == 'exited' for item in observed['commands'])
    summary = observed['job_processes']
    assert summary['status'] == 'available'
    assert summary['running_command_pids'] == []
    assert child in summary['descendant_pids']
    assert summary['active_process_count'] == len(summary['descendant_pids']) > 0
    assert summary['descendants_after_command_exit'] is True
    assert summary['diagnostic_only'] is True
    assert set(summary) == {'status', 'active_process_count', 'running_command_pids',
                            'descendant_pids', 'descendants_after_command_exit', 'diagnostic_only'}
    counters = usage(host)
    assert counters['commands'] == 0
    assert counters['coding_job_active_processes'] == summary['active_process_count']
    assert not counters['stale']
    assert 'running_commands' in resource.idle_blockers()


def test_process_diagnostics_are_workflow_scoped_and_classify_live_roots(host, orphan):
    first, _, child = orphan
    (host.config.workspace_root / 'second').mkdir()
    token = call(host, 'UnifiedTask', action='begin', project_path='second',
                 request_id='second')['structuredContent']['workflow_id']
    call(host, 'UnifiedTask', action='activate', workflow_id=token)
    ready = host.config.workspace_root / 'second-ready'
    launched = call(host, 'exec_command', workflow_id=token, request_id='running-root',
                    cmd=subprocess.list2cmdline([sys.executable, '-c',
                        'import sys,time;from pathlib import Path;Path(sys.argv[1]).touch();time.sleep(60)', str(ready)]),
                    yield_time_ms=0)['structuredContent']
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert ready.exists()
    observed = status(host, token)
    root = next(item['pid'] for item in observed['commands'] if item['command_id'] == launched['command_id'])
    summary = observed['job_processes']
    assert root in summary['running_command_pids']
    assert root not in summary['descendant_pids']
    assert child not in summary['descendant_pids'] + summary['running_command_pids']
    assert not summary['descendants_after_command_exit']
    original = status(host, first)['job_processes']
    assert root not in original['descendant_pids'] + original['running_command_pids']
    assert usage(host)['coding_job_active_processes'] == (
        original['active_process_count'] + summary['active_process_count'])


def test_job_query_failure_is_unknown_and_keeps_last_usage(host, orphan, monkeypatch):
    token, resource, _ = orphan
    cached = usage(host)

    def unavailable():
        raise OSError('fixture-private-diagnostic')

    with monkeypatch.context() as patch:
        patch.setattr(resource.coding.owned_job, 'active_pids', unavailable)
        summary = status(host, token)['job_processes']
        assert summary['status'] == 'unavailable'
        assert summary['active_process_count'] is None
        assert summary['running_command_pids'] is None and summary['descendant_pids'] is None
        assert summary['descendants_after_command_exit'] is None
        assert summary['error_code'] == 'JOB_PROCESS_STATE_UNAVAILABLE'
        assert 'fixture-private-diagnostic' not in json.dumps(summary)
        counters = usage(host)
        assert counters['stale']
        assert counters['coding_job_active_processes'] == cached['coding_job_active_processes'] > 0
    assert not usage(host)['stale']


def test_busy_job_does_not_block_cached_server_info(host, orphan):
    _, resource, _ = orphan
    cached = usage(host)
    entered, release = threading.Event(), threading.Event()

    def hold_job():
        with resource.coding.owned_job.lock:
            entered.set()
            assert release.wait(5)

    worker = threading.Thread(target=hold_job)
    worker.start()
    try:
        assert entered.wait(2)
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(usage, host)
            counters = result.result(timeout=2)
        assert counters['stale']
        assert counters['coding_job_active_processes'] == cached['coding_job_active_processes'] > 0
    finally:
        release.set()
        worker.join(5)


def test_starting_command_is_not_reported_as_descendant_after_root_exit(host, monkeypatch):
    token = workflow(host)
    coding = next(iter(host.registry.resources.values())).coding
    cached = usage(host)
    entered, release = threading.Event(), threading.Event()
    processes = []
    original = coding._make_command

    def before_registration(process, **kwargs):
        processes.append(process)
        entered.set()
        assert release.wait(5)
        return original(process, **kwargs)

    monkeypatch.setattr(coding, '_make_command', before_registration)
    executor = ThreadPoolExecutor(max_workers=1)
    launched = executor.submit(call, host, 'exec_command', workflow_id=token, request_id='starting',
        cmd=subprocess.list2cmdline([sys.executable, '-c', 'import time;time.sleep(60)']), yield_time_ms=0)
    try:
        assert entered.wait(2)
        root = processes[0].pid
        assert processes[0].poll() is None and root in coding.owned_job.active_pids()
        with coding.commands_lock:
            assert coding.starting_commands == 1 and not coding.commands
        observed = status(host, token)['job_processes']
        assert observed['status'] == 'unavailable', observed
        assert observed['error_code'] == 'JOB_PROCESS_STATE_BUSY'
        assert observed['active_process_count'] is None and observed['descendant_pids'] is None
        assert observed['descendants_after_command_exit'] is None
        counters = usage(host)
        assert counters['stale']
        assert counters['coding_job_active_processes'] == cached['coding_job_active_processes'] == 0
    finally:
        release.set()
        executor.shutdown(wait=True)
    result = launched.result(timeout=2)
    assert not result.get('isError'), result
    observed = status(host, token)['job_processes']
    assert observed['status'] == 'available'
    assert root in observed['running_command_pids']
    assert not observed['descendants_after_command_exit']
    assert not usage(host)['stale']
