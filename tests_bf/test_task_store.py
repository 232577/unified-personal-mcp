from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from bf_automation.task_store import TaskStore


def test_task_tokens_are_persisted_only_as_hashes(tmp_path):
    allowed = tmp_path / "run"
    project = allowed / "project-a"
    project.mkdir(parents=True)
    store = TaskStore(tmp_path / "state", allowed_root=allowed)

    created = store.begin(project)
    task_file = Path(created["task_directory"]) / "task.json"
    text = task_file.read_text(encoding="utf-8")

    assert created["bf_task_id"].startswith("bf_")
    assert created["bf_task_id"] not in text
    assert "token_sha256" in text


def test_window_binding_is_scoped_to_owning_task(tmp_path):
    allowed = tmp_path / "run"
    project = allowed / "project-a"
    project.mkdir(parents=True)
    store = TaskStore(tmp_path / "state", allowed_root=allowed)
    task_a = store.begin(project)["bf_task_id"]
    task_b = store.begin(project)["bf_task_id"]

    window_id = store.bind_window(task_a, hwnd=1234, pid=4321, title="Same title")

    assert store.resolve_window(task_a, window_id)["hwnd"] == 1234
    with pytest.raises(PermissionError, match="window_id"):
        store.resolve_window(task_b, window_id)


def test_project_path_must_stay_inside_allowed_root(tmp_path):
    allowed = tmp_path / "run"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    store = TaskStore(tmp_path / "state", allowed_root=allowed)

    with pytest.raises(ValueError, match="allowed root"):
        store.begin(outside)


def test_end_hook_can_join_inflight_binding_without_holding_the_state_lock(tmp_path):
    store = TaskStore(tmp_path / 'state', allowed_root=tmp_path)
    token = store.begin(tmp_path)['bf_task_id']
    completed = threading.Event()
    outcome = []
    workers = []

    def finish_binding():
        try:
            store.bind_window(token, hwnd=123, pid=456, title='owned fixture')
            outcome.append('bound')
        except PermissionError as error:
            outcome.append(str(error))
        finally:
            completed.set()

    def cleanup(_):
        worker = threading.Thread(target=finish_binding, daemon=True)
        workers.append(worker)
        worker.start()
        if not completed.wait(1):
            raise TimeoutError('inflight binding cannot finish while cleanup holds state lock')

    store.add_end_hook(cleanup)
    try:
        assert store.end(token)['status'] == 'ended'
    finally:
        for worker in workers:
            worker.join(timeout=2)
    assert outcome == ['TASK_ENDING']


def test_failed_task_cleanup_blocks_new_bindings_but_can_be_retried(tmp_path):
    store = TaskStore(tmp_path / 'state', allowed_root=tmp_path)
    token = store.begin(tmp_path)['bf_task_id']
    attempts = []

    def cleanup(_):
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError('fixture cleanup not finished')

    store.add_end_hook(cleanup)
    with pytest.raises(RuntimeError, match='TASK_CLEANUP_INCOMPLETE'):
        store.end(token)
    assert store.status(token)['status'] == 'active'
    with pytest.raises(PermissionError, match='TASK_ENDING'):
        store.bind_window(token, hwnd=123, pid=456, title='must not bind')
    assert store.end(token)['status'] == 'ended'
    assert len(attempts) == 2


def test_concurrent_task_end_runs_cleanup_only_once(tmp_path):
    store = TaskStore(tmp_path / 'state', allowed_root=tmp_path)
    token = store.begin(tmp_path)['bf_task_id']
    entered, release = threading.Event(), threading.Event()
    attempts = []

    def cleanup(_):
        attempts.append(True)
        entered.set()
        assert release.wait(3)

    store.add_end_hook(cleanup)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(store.end, token)
        try:
            assert entered.wait(1)
            second = pool.submit(store.end, token)
        finally:
            release.set()
        assert first.result(3)['status'] == 'ended'
        with pytest.raises(PermissionError, match='not active'):
            second.result(3)
    assert attempts == [True]


def test_claim_cleanup_failure_keeps_task_available_for_cleanup_retry(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / 'state', allowed_root=tmp_path)
    token = store.begin(tmp_path)['bf_task_id']
    claims = store._claims

    def unavailable():
        raise OSError('fixture claims database unavailable')

    monkeypatch.setattr(store, '_claims', unavailable)
    with pytest.raises(OSError):
        store.end(token)
    assert store.status(token)['status'] == 'active'
    with pytest.raises(PermissionError, match='TASK_ENDING'):
        store.bind_window(token, hwnd=123, pid=456, title='must not bind')
    monkeypatch.setattr(store, '_claims', claims)
    assert store.end(token)['status'] == 'ended'
