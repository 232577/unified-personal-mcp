import json
import os

import pytest

from bf_automation.application_registry import ApplicationRegistry, project_profiles
from bf_automation.browser.policy import load_web_profile
from bf_automation.task_store import TaskStore


def setup(tmp_path):
    project = tmp_path / "projects" / "app"
    profiles = project / ".bf" / "apps"
    profiles.mkdir(parents=True)
    store = TaskStore(tmp_path / "private", allowed_root=tmp_path / "projects")
    token = store.begin(project)["bf_task_id"]
    return project, profiles, store, token


def test_project_profile_is_discoverable_without_private_state_write(tmp_path):
    project, profiles, store, token = setup(tmp_path)
    (project / "app.exe").write_bytes(b"MZ")
    (profiles / "app.json").write_text(json.dumps({"id": "app", "launcher": {
        "executable": "../../app.exe", "args": [], "cwd": "../.."}}))
    selected = project_profiles(store, token, tmp_path / "private" / "apps", "app")
    assert selected == profiles
    loaded = ApplicationRegistry(selected).get("app")
    assert loaded.executable == project / "app.exe"
    assert loaded.cwd == project


def test_relative_web_profile_remains_valid_after_relocation(tmp_path):
    project, profiles, store, token = setup(tmp_path)
    (profiles / "web.json").write_text(json.dumps({"version": 1, "id": "web", "kind": "web",
        "project_root": "../..", "entry_url": "http://127.0.0.1:65533/",
        "navigation_origins": ["http://127.0.0.1:65533"], "resource_origins": ["http://127.0.0.1:65533"]}))
    assert load_web_profile(profiles, "web", store.allowed_root).project_root == project


def test_profile_symlink_cannot_leave_owning_project(tmp_path):
    project, profiles, store, token = setup(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text('{}')
    try:
        os.symlink(outside, profiles / "outside.json")
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(ValueError, match="PROFILE_PATH_OUTSIDE_PROJECT"):
        project_profiles(store, token, tmp_path, "outside")
