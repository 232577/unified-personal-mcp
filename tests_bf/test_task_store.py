from pathlib import Path

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
