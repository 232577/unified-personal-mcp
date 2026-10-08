import json
from contextlib import contextmanager
from types import SimpleNamespace

from personal_mcp.config import load_config
from personal_mcp.tunnel import TunnelRunner
from tests_personal.test_config import installation

from personal_mcp.tunnel_diagnostics import latest_poller_state


def event(message, timestamp='2026-10-08T10:00:00+00:00', **extra):
    return json.dumps({'time': timestamp, 'msg': message, **extra}) + '\n'


def test_ready_endpoint_does_not_hide_a_recorded_poll_failure(tmp_path):
    log = tmp_path / 'tunnel.log'
    log.write_text(event('poll failed; backing off', error='private error payload'), encoding='utf-8')
    state = latest_poller_state(log, since=0)
    assert state['connected'] is False
    assert set(state) == {'connected', 'observed_at'}


def test_recovery_supersedes_previous_failure(tmp_path):
    log = tmp_path / 'tunnel.log'
    log.write_text(event('poll failed; backing off') +
                   event('poller recovered; polling operational', '2026-10-08T10:01:00+00:00'), encoding='utf-8')
    assert latest_poller_state(log, since=0)['connected'] is True


def test_old_instance_and_partial_lines_do_not_create_false_failures(tmp_path):
    log = tmp_path / 'tunnel.log'
    log.write_text(event('poll failed; backing off') + '{"time":', encoding='utf-8')
    assert latest_poller_state(log, since=9999999999) is None
    log.write_text(event('poller recovered; polling operational') + '{"time":', encoding='utf-8')
    assert latest_poller_state(log, since=0)['connected'] is True


def test_unavailable_or_unrecognized_evidence_is_unknown(tmp_path):
    log = tmp_path / 'tunnel.log'
    assert latest_poller_state(log, since=0) is None


def test_runner_reports_poll_failure_even_when_readyz_is_successful(tmp_path, monkeypatch):
    cfg = load_config(installation(tmp_path))
    cfg.data_root.mkdir()
    (cfg.data_root / 'tunnel-health.url').write_text('http://127.0.0.1:12345')
    log = cfg.data_root / 'tunnel.log'
    log.write_text(event('poll failed; backing off'), encoding='utf-8')
    @contextmanager
    def open_response(*args, **kwargs):
        yield SimpleNamespace(status=200)
    monkeypatch.setattr('urllib.request.build_opener',
                        lambda *a: SimpleNamespace(open=open_response))
    runner = TunnelRunner(cfg, tmp_path / 'fixture.exe', 'fixture-backend-key')
    runner.process = SimpleNamespace(poll=lambda: None)
    assert not runner.ready()
    assert runner.readiness_error == 'TUNNEL_POLL_FAILED'
    # Rotation/loss of a log line is not evidence that polling recovered.
    log.write_text('unrelated diagnostic output\n' * 4000, encoding='utf-8')
    assert not runner.ready() and runner.readiness_error == 'TUNNEL_POLL_FAILED'
    log.write_text(event('poller recovered; polling operational',
                         '2026-10-08T10:01:00+00:00'), encoding='utf-8')
    assert runner.ready() and runner.readiness_error is None
    log.write_text(event('tunnel-client started') + event('poll failed; backing off', 'invalid'), encoding='utf-8')
    assert latest_poller_state(log, since=0) is None
