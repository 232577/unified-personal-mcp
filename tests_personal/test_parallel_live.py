"""Real owned processes exercise project isolation and safe idle reclamation."""

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests_personal import test_host

call = test_host.call
host = test_host.host


WORKER = """import json
import os
import time
from pathlib import Path
Path('observed.json').write_text(json.dumps({'cwd': str(Path.cwd()), 'pid': os.getpid()}), encoding='utf-8')
print(str(Path.cwd()), flush=True)
while not Path('finish.txt').exists():
    time.sleep(0.02)
print('finished', flush=True)
"""


def wait_until(check, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    pytest.fail("isolated process condition did not settle")


def start_project(runtime, name):
    project = runtime.config.workspace_root / name
    project.mkdir()
    (project / "worker.py").write_text(WORKER, encoding="utf-8")
    started = call(runtime, "UnifiedTask", action="begin", project_path=str(project), request_id="begin-" + name)
    assert not started["isError"], started
    token = started["structuredContent"]["workflow_id"]
    activated = call(runtime, "UnifiedTask", action="activate", workflow_id=token)
    assert not activated["isError"], activated
    resource = runtime.registry.resources[runtime.registry._hash(token)]
    return project, token, resource


def launch_worker(runtime, token):
    result = call(runtime, "exec_command", workflow_id=token, request_id="launch",
                  cmd=subprocess.list2cmdline([sys.executable, "worker.py"]), workdir=".",
                  timeout_ms=30000, yield_time_ms=100, verbosity="full")
    assert not result["isError"], result
    assert result["structuredContent"]["status"] == "running", result
    return result["structuredContent"]["command_id"]


def test_eight_real_projects_run_together_and_end_only_their_own_processes(host):
    projects = [start_project(host, f"parallel-{index}") for index in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        commands = list(pool.map(lambda row: launch_worker(host, row[1]), projects))
    assert len(set(commands)) == 8
    wait_until(lambda: all((project / "observed.json").exists() for project, _, _ in projects))
    for project, token, resource in projects:
        observed = call(host, "read_file", workflow_id=token, path="observed.json")
        assert not observed["isError"], observed
        record = json.loads(observed["structuredContent"]["content"])
        assert Path(record["cwd"]) == project
        assert record["pid"] in resource.coding.owned_job.active_pids()

    ended = call(host, "UnifiedTask", action="end", workflow_id=projects[0][1])
    assert ended["structuredContent"]["state"] == "ENDED"
    assert not projects[0][2].coding.owned_job.active_pids()
    for project, token, resource in projects[1:]:
        assert resource.coding.owned_job.active_pids(), "ending the first workflow stopped another project"
        assert not call(host, "read_file", workflow_id=token, path="observed.json")["isError"]
        (project / "finish.txt").write_text("finish", encoding="utf-8")
    wait_until(lambda: all(not resource.coding.owned_job.active_pids() for _, _, resource in projects))


def test_real_command_blocks_idle_reclamation_then_completed_project_can_reopen(host):
    project, token, resource = start_project(host, "long-running")
    launch_worker(host, token)
    wait_until(lambda: (project / "observed.json").exists())
    listed = call(host, "UnifiedTask", action="list")["structuredContent"]["workflows"]
    reference = next(row["workflow_ref"] for row in listed if row["project"] == str(project))
    now = [time.time() + host.config.workflow_idle_seconds + 10]
    host.registry.clock = lambda: now[0]
    refused = call(host, "UnifiedTask", action="release_idle", workflow_ref=reference)
    error = refused["structuredContent"]["error"]
    assert error["code"] == "WORKFLOW_NOT_IDLE"
    assert error["details"]["reason"] == "ACTIVE_RESOURCES"
    assert "running_commands" in error["details"]["blockers"]
    assert resource.coding.owned_job.active_pids()

    busy = call(host, "UnifiedTask", action="begin", project_path=str(project), request_id="reopen-while-busy")
    error = busy["structuredContent"]["error"]
    assert error["code"] == "PROJECT_BUSY"
    blocker = error["details"]["blockers"][0]
    assert blocker["relation"] == "same"
    assert blocker["project"] == str(project)
    assert blocker["workflow_ref"] == reference
    assert token not in json.dumps(error), "diagnostics exposed the bearer workflow token"

    (project / "finish.txt").write_text("finish", encoding="utf-8")
    wait_until(lambda: not resource.coding.owned_job.active_pids())
    host.registry.expire()
    assert resource.coding.owned_job.handle is None
    reopened = call(host, "UnifiedTask", action="begin", project_path=str(project), request_id="reopen-after-finish")
    assert not reopened["isError"], reopened
    replacement = reopened["structuredContent"]["workflow_id"]
    assert replacement != token
    assert not call(host, "UnifiedTask", action="activate", workflow_id=replacement)["isError"]
    assert not call(host, "read_file", workflow_id=replacement, path="observed.json")["isError"]
    assert call(host, "read_file", workflow_id=token, path="observed.json")["isError"]
