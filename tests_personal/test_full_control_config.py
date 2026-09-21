import pytest

from bf_automation.task_store import TaskStore
from personal_mcp.config import load_config
from tests_personal.test_config import installation


def test_full_control_accepts_umbrella_and_external_project(tmp_path):
    cfg = load_config(installation(tmp_path, permission_mode="full_control"))
    external = tmp_path / "external project"
    external.mkdir()
    assert cfg.project(".") == cfg.workspace_root
    assert cfg.project(str(external)) == external
    assert cfg.full_control
    assert cfg.runtime_permission_mode == "dangerous"


def test_existing_modes_keep_project_restrictions(tmp_path):
    for mode in ("safe", "trusted"):
        cfg = load_config(installation(tmp_path, permission_mode=mode))
        assert not cfg.full_control
        assert cfg.runtime_permission_mode == mode
        with pytest.raises(ValueError):
            cfg.project(".")
        with pytest.raises(ValueError):
            cfg.project(str(tmp_path))


def test_external_bf_project_requires_explicit_full_control(tmp_path):
    root, external = tmp_path / "projects", tmp_path / "external"
    root.mkdir()
    external.mkdir()
    scoped = TaskStore(tmp_path / "scoped-state", allowed_root=root)
    with pytest.raises(ValueError, match="allowed root"):
        scoped.begin(external)
    full = TaskStore(tmp_path / "full-state", allowed_root=root, allow_external_projects=True)
    token = full.begin(external)["bf_task_id"]
    try:
        assert full.status(token)["project_path"] == str(external.resolve())
    finally:
        full.end(token)
