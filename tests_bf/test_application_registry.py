import json

import pytest

from bf_automation import launch_guard
from bf_automation.application_registry import ApplicationRegistry
from bf_automation.bf_tools import _collect_descendant_pids
from bf_automation.launch_guard import validate_launch_target


@pytest.mark.parametrize("name", ["README.txt", "index.html", "app.vue", "launcher.py",
                                  "D:/run/example", "https://localhost/app"])
def test_name_launch_never_accepts_a_document_or_path(name):
    assert callable(getattr(launch_guard, "validate_application_name", None))
    with pytest.raises(ValueError, match="application name"):
        launch_guard.validate_application_name(name)


@pytest.mark.parametrize("suffix", [".txt", ".md", ".json", ".html", ".py", ".vue"])
def test_launch_guard_rejects_document_and_source_targets(tmp_path, suffix):
    target = tmp_path / f"target{suffix}"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="not an application executable"):
        validate_launch_target(target)


def test_launch_guard_accepts_executable_file(tmp_path):
    target = tmp_path / "app.exe"
    target.write_bytes(b"MZ")
    assert validate_launch_target(target) == target.resolve()


def test_registry_loads_profile_and_validates_executable(tmp_path):
    apps = tmp_path / "apps"
    apps.mkdir()
    executable = tmp_path / "app.exe"
    executable.write_bytes(b"MZ")
    (apps / "sample.json").write_text(
        json.dumps(
            {
                "id": "sample",
                "kind": "desktop",
                "adapter": "window",
                "launcher": {"executable": str(executable), "args": ["--safe"]},
                "window": {"title_contains": "Sample"},
            }
        ),
        encoding="utf-8",
    )

    profile = ApplicationRegistry(apps).get("sample")

    assert profile.app_id == "sample"
    assert profile.executable == executable.resolve()
    assert profile.args == ["--safe"]
    assert profile.title_contains == "Sample"


def test_collect_descendant_pids_includes_root_and_recursive_children(monkeypatch):
    class FakeChild:
        def __init__(self, pid):
            self.pid = pid

    class FakeProcess:
        def __init__(self, pid):
            assert pid == 100

        def children(self, recursive=False):
            assert recursive is True
            return [FakeChild(101), FakeChild(102)]

    monkeypatch.setattr("bf_automation.bf_tools.psutil.Process", FakeProcess)

    assert _collect_descendant_pids(100) == {100, 101, 102}
