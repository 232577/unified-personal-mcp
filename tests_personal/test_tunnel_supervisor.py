import importlib
import threading
import time

import pytest


class FakeRunner:
    def __init__(self):
        self.alive = False
        self.available = False
        self.starts = self.closes = 0
        self.failure = None
        self.entered = threading.Event()
        self.release = None

    def start(self):
        self.starts += 1
        self.entered.set()
        if self.release is not None:
            assert self.release.wait(3)
        if self.failure:
            raise self.failure
        self.alive = True

    def is_alive(self):
        return self.alive

    def ready(self):
        return self.alive and self.available

    def credentials_changed(self):
        return False

    def close(self):
        self.closes += 1
        self.alive = False


def eventually(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


def supervisor(monkeypatch, runners, now=None):
    try:
        module = importlib.import_module('personal_mcp.tunnel_supervisor')
    except ModuleNotFoundError:
        pytest.fail('independent tunnel supervision is missing')
    iterator = iter(runners)
    monkeypatch.setattr(module, 'TunnelRunner', lambda *args: next(iterator))
    return module.TunnelSupervisor(None, None, 'private-backend-key',
        poll_interval=0.01, retry_base=0.02, jitter=lambda: 0,
        clock=(lambda: now[0]) if now is not None else time.time)


def test_alive_unready_is_preserved_then_recovers_without_new_process(monkeypatch):
    runner = FakeRunner()
    monitor = supervisor(monkeypatch, [runner])
    try:
        monitor.start()
        eventually(lambda: monitor.snapshot()['status'] == 'degraded')
        for _ in range(5):
            monitor.retry()
        assert runner.starts == 1
        assert runner.closes == 0
        runner.available = True
        eventually(lambda: monitor.snapshot()['status'] == 'healthy')
        assert monitor.snapshot()['last_success'] is not None
        assert 'private-backend-key' not in str(monitor.snapshot())
    finally:
        monitor.close()
    assert not monitor.worker.is_alive()
    assert runner.closes == 1


def test_dead_child_retries_after_deadline_without_resetting_other_resources(monkeypatch):
    now = [100.0]
    first, second = FakeRunner(), FakeRunner()
    first.available = second.available = True
    monitor = supervisor(monkeypatch, [first, second], now)
    try:
        monitor.start()
        eventually(lambda: monitor.snapshot()['status'] == 'healthy')
        first.alive = False
        eventually(lambda: monitor.snapshot()['next_retry'] is not None)
        assert first.closes == 1
        assert second.starts == 0
        now[0] = monitor.snapshot()['next_retry'] + 0.01
        eventually(lambda: second.starts == 1)
        eventually(lambda: monitor.snapshot()['status'] == 'healthy')
        assert monitor.snapshot()['recovery_attempts'] == 1
    finally:
        monitor.close()


def test_missing_key_waits_for_explicit_retry_and_never_leaks_exception(monkeypatch):
    first, second = FakeRunner(), FakeRunner()
    first.failure = ValueError('TUNNEL_KEY_UNAVAILABLE')
    second.available = True
    monitor = supervisor(monkeypatch, [first, second])
    try:
        monitor.start()
        eventually(lambda: monitor.snapshot()['error_code'] == 'TUNNEL_KEY_UNAVAILABLE')
        assert monitor.snapshot()['next_retry'] is None
        time.sleep(0.05)
        assert second.starts == 0
        monitor.retry()
        eventually(lambda: monitor.snapshot()['status'] == 'healthy')
    finally:
        monitor.close()


def test_close_during_startup_cleans_late_child_and_disables_restarts(monkeypatch):
    runner = FakeRunner()
    runner.release = threading.Event()
    monitor = supervisor(monkeypatch, [runner])
    monitor.start()
    assert runner.entered.wait(1)
    closer = threading.Thread(target=monitor.close)
    closer.start()
    eventually(lambda: monitor.snapshot()['status'] == 'stopped')
    with pytest.raises(RuntimeError, match='TUNNEL_SUPERVISOR_STOPPED'):
        monitor.retry()
    runner.release.set()
    closer.join(3)
    assert not closer.is_alive()
    assert not monitor.worker.is_alive()
    assert runner.closes == 1
    assert not runner.alive


def test_health_snapshot_does_not_wait_for_in_progress_start(monkeypatch):
    runner = FakeRunner()
    runner.release = threading.Event()
    monitor = supervisor(monkeypatch, [runner])
    try:
        monitor.start()
        assert runner.entered.wait(1)
        began = time.monotonic()
        assert monitor.snapshot()['status'] == 'recovering'
        assert time.monotonic() - began < 0.1
    finally:
        runner.release.set()
        monitor.close()


def test_repeated_dead_children_back_off_and_cap_at_thirty_seconds(monkeypatch):
    now = [100.0]
    runners = [FakeRunner() for _ in range(7)]
    for runner in runners:
        runner.failure = OSError('untrusted secret error text')
    monitor = supervisor(monkeypatch, runners, now)
    monitor._retry_base = 1
    try:
        monitor.start()
        for runner, delay in zip(runners, [1, 2, 4, 8, 16, 30, 30], strict=True):
            eventually(lambda: runner.closes == 1 and monitor.snapshot()['next_retry'] is not None)
            snapshot = monitor.snapshot()
            assert snapshot['next_retry'] == now[0] + delay
            assert snapshot['error_code'] == 'TUNNEL_START_FAILED'
            assert 'secret' not in str(snapshot)
            now[0] = snapshot['next_retry']
    finally:
        monitor.close()


def test_supervised_real_owned_children_from_pythonw_never_open_console(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    from tests_personal.test_config import installation

    root = Path(__file__).resolve().parents[1]
    cfg = installation(tmp_path)
    consoles = tmp_path / 'consoles.jsonl'
    child = (
        'import ctypes,time;from pathlib import Path;'
        f"p=Path({str(consoles)!r});"
        "f=p.open('a');f.write(str(ctypes.windll.kernel32.GetConsoleWindow())+'\\n');"
        'f.close();time.sleep(0.1)'
    )
    script = tmp_path / 'probe.py'
    result = tmp_path / 'result.json'
    script.write_text(
        'import json,sys,time\nfrom pathlib import Path\n'
        f'sys.path.insert(0, {str(root)!r})\n'
        'from personal_mcp import tunnel\n'
        'from personal_mcp.config import load_config\n'
        'from personal_mcp.tunnel_supervisor import TunnelSupervisor\n'
        'from personal_mcp.windows_jobs import OwnedJob\n'
        'class FixtureJob(OwnedJob):\n'
        ' def spawn(self,args,**kwargs):\n'
        f'  return super().spawn([sys.executable,"-c",{child!r}],**kwargs)\n'
        'tunnel.OwnedJob=FixtureJob\n'
        'tunnel.verify_client=lambda binary: binary\n'
        'tunnel.assert_tunnel_available=lambda tunnel_id: None\n'
        'tunnel.read_tunnel_key=lambda config: "fixture-key-not-a-credential"\n'
        f'monitor=TunnelSupervisor(load_config({str(cfg)!r}), "fixture.exe", "fixture-key",'
        'poll_interval=0.02,retry_base=0.02)\n'
        'try:\n'
        ' monitor.start()\n'
        ' deadline=time.monotonic()+10\n'
        ' while time.monotonic()<deadline:\n'
        f'  p=Path({str(consoles)!r})\n'
        '  if p.exists() and len(p.read_text().splitlines())>=2: break\n'
        '  time.sleep(0.02)\n'
        'finally:\n'
        ' monitor.close()\n'
        f'Path({str(result)!r}).write_text(json.dumps(monitor.snapshot()))\n', encoding='utf-8')
    with (tmp_path / 'probe.log').open('wb') as log:
        process = subprocess.Popen([str(Path(sys.executable).with_name('pythonw.exe')), str(script)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=subprocess.DETACHED_PROCESS)
        try:
            assert process.wait(timeout=20) == 0, (tmp_path / 'probe.log').read_text()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
    readings = consoles.read_text().splitlines()
    assert len(readings) >= 2
    assert set(readings) == {'0'}
    assert json.loads(result.read_text())['status'] == 'stopped'


@pytest.mark.parametrize('replacement', ['fixture-replacement-cloud-key-123456789', None, 'invalid'])
def test_explicit_retry_reloads_changed_key_without_replacing_unchanged_live_child(tmp_path, monkeypatch, replacement):
    from personal_mcp import tunnel
    from personal_mcp.config import load_config
    from personal_mcp.tunnel_supervisor import TunnelSupervisor
    from tests_personal.test_config import installation

    cfg = load_config(installation(tmp_path))
    cfg.data_root.mkdir()
    cfg.tunnel_key_file.write_text('fixture-original-cloud-key-123456789')
    jobs = []

    class Process:
        pid = 123
        alive = True

        def poll(self):
            return None if self.alive else 0

        def wait(self, timeout):
            assert not self.alive

    class Job:
        def __init__(self):
            self.process = Process()
            self.key = None
            jobs.append(self)

        def spawn(self, args, **kwargs):
            self.key = kwargs['env']['UPM_TUNNEL_RUN_KEY']
            return self.process

        def close(self):
            self.process.alive = False

    monkeypatch.setattr(tunnel, 'verify_client', lambda binary: binary)
    monkeypatch.setattr(tunnel, 'assert_tunnel_available', lambda ident: None)
    monkeypatch.setattr(tunnel, 'OwnedJob', Job)
    monitor = TunnelSupervisor(cfg, tmp_path / 'fixture.exe', 'fixture-backend-key', poll_interval=0.01)
    try:
        monitor.start()
        eventually(lambda: monitor.snapshot()['status'] == 'degraded')
        monitor.retry()
        time.sleep(0.05)
        assert len(jobs) == 1 and jobs[0].process.alive
        if replacement is None:
            cfg.tunnel_key_file.unlink()
        else:
            cfg.tunnel_key_file.write_text(replacement)
        monitor.retry()
        if replacement is None or replacement == 'invalid':
            code = 'TUNNEL_KEY_UNAVAILABLE' if replacement is None else 'TUNNEL_KEY_INVALID'
            eventually(lambda: monitor.snapshot()['error_code'] == code)
            assert len(jobs) == 1 and jobs[0].process.alive
        else:
            eventually(lambda: len(jobs) == 2)
            assert not jobs[0].process.alive
            assert jobs[1].key == 'fixture-replacement-cloud-key-123456789'
    finally:
        monitor.close()
    assert not any(job.process.alive for job in jobs)


@pytest.mark.parametrize('failure', [RuntimeError('TUNNEL_CLEANUP_INCOMPLETE'),
                                      OSError('private cleanup diagnostic')])
def test_key_rotation_cleanup_failure_stays_degraded_until_explicit_retry(monkeypatch, failure):
    class RotatedRunner(FakeRunner):
        changed = False
        cleanup_fails = True

        def credentials_changed(self):
            return self.changed

        def close(self):
            if self.cleanup_fails:
                self.closes += 1
                raise failure
            super().close()

    first, second = RotatedRunner(), FakeRunner()
    first.available = second.available = True
    monitor = supervisor(monkeypatch, [first, second])
    try:
        monitor.start()
        eventually(lambda: monitor.snapshot()['status'] == 'healthy')
        first.changed = True
        monitor.retry()
        eventually(lambda: monitor.snapshot()['error_code'] == 'TUNNEL_CLEANUP_INCOMPLETE')
        time.sleep(0.05)
        state = monitor.snapshot()
        assert state['status'] == 'degraded' and state['next_retry'] is None
        assert first.alive and first.closes == 1 and second.starts == 0
        first.cleanup_fails = False
        monitor.retry()
        eventually(lambda: second.starts == 1 and monitor.snapshot()['status'] == 'healthy')
        assert not first.alive and first.closes == 2
    finally:
        first.cleanup_fails = False
        monitor.close()
