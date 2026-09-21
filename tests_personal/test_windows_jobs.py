import subprocess
import sys
import time

import psutil

from personal_mcp.windows_jobs import OwnedJob


def test_job_close_kills_only_its_tree(tmp_path):
    flags = subprocess.CREATE_NO_WINDOW
    unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], creationflags=flags)
    job = None
    try:
        job = OwnedJob()
        process = job.spawn([sys.executable, "-c", "import time;print('ready',flush=True);time.sleep(60)"],
                            stdout=subprocess.PIPE)
        assert process.stdout.readline() == b"ready\r\n"
        assert process.pid in job.active_pids()
        job.close()
        process.wait(timeout=3)
        process.stdout.close()
        assert unrelated.poll() is None
    finally:
        if job:
            job.close()
        unrelated.kill()
        unrelated.wait(timeout=3)


def test_job_handle_closure_on_host_crash_releases_child(tmp_path):
    marker = tmp_path / "child.pid"
    script = (
        "import sys,time;from pathlib import Path;from personal_mcp.windows_jobs import OwnedJob;"
        "j=OwnedJob();p=j.spawn([sys.executable,'-c','import time;time.sleep(60)']);"
        "Path(sys.argv[1]).write_text(str(p.pid));time.sleep(60)"
    )
    host = subprocess.Popen([sys.executable, "-c", script, str(marker)], creationflags=subprocess.CREATE_NO_WINDOW)
    child = None
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        child = psutil.Process(int(marker.read_text()))
        host.kill()
        host.wait(timeout=3)
        child.wait(timeout=3)
        assert not child.is_running()
    finally:
        if host.poll() is None:
            host.kill()
            host.wait(timeout=3)
        if child and child.is_running():
            child.kill()
