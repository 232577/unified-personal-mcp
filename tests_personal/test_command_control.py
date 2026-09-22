import threading
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from coding_tools_mcp.processes import CommandRun
from tests_personal.test_host import call, host as host, workflow


def test_parallel_output_snapshots_consume_each_byte_once():
    command = CommandRun('command', SimpleNamespace(poll=lambda: 0))
    command.append_stdout(b'one contiguous payload\n')
    with ThreadPoolExecutor(4) as pool:
        snapshots = list(pool.map(lambda _: command.snapshot_since_cursor(65536), range(4)))
    assert ''.join(item['stdout'] for item in snapshots) == 'one contiguous payload\n'


def test_status_poll_and_cancel_bypass_busy_write_gate(host):
    token = workflow(host)
    command = subprocess.list2cmdline([sys.executable, '-c', 'import time; time.sleep(20)'])
    launched = call(host, 'exec_command', workflow_id=token, request_id='launch',
                    cmd=command, timeout_ms=30000, yield_time_ms=0)
    assert not launched.get('isError'), launched
    assert launched['structuredContent']['status'] == 'running'
    command_id = launched['structuredContent']['command_id']
    entered, release = threading.Event(), threading.Event()

    def busy():
        with host.registry.use(host.principal, token, write=True):
            entered.set()
            release.wait(10)

    with ThreadPoolExecutor(2) as pool:
        blocked = pool.submit(busy)
        assert entered.wait(2)
        try:
            status = pool.submit(call, host, 'UnifiedTask', action='status', workflow_id=token).result(2)
            assert command_id in {item['command_id'] for item in status['structuredContent']['commands']}
            polled = pool.submit(call, host, 'write_stdin', workflow_id=token, request_id='poll',
                                 command_id=command_id, chars='', yield_time_ms=0).result(2)
            assert not polled.get('isError'), polled
            assert polled['structuredContent']['status'] == 'running'
            cancelled = pool.submit(call, host, 'kill_command', workflow_id=token,
                                    request_id='cancel', command_id=command_id).result(5)
            assert not cancelled.get('isError'), cancelled
            repeated = call(host, 'kill_command', workflow_id=token, request_id='cancel', command_id=command_id)
            assert repeated == cancelled
        finally:
            release.set()
            blocked.result(2)
