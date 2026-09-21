"""Explicit full-control access uses disposable files and owned processes only."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from coding_tools_mcp.server import ToolFailure
from personal_mcp.coding import build_coding
from personal_mcp.config import load_config
from tests_personal.test_config import installation


@pytest.fixture
def full_runtime(tmp_path):
    cfg = replace(load_config(installation(tmp_path)), permission_mode="full_control")
    external = tmp_path / "shared tools"
    external.mkdir()
    subprocess.run(["git", "init", str(external)], capture_output=True, check=True)
    runtime = build_coding(cfg, cfg.project("app"))
    try:
        yield runtime, cfg, external
    finally:
        runtime.close()


def test_external_files_patch_listing_search_and_image(full_runtime):
    runtime, _, external = full_runtime
    target = external / "note.txt"
    runtime.apply_patch({"patch": f"*** Begin Patch\n*** Add File: {target.as_posix()}\n+shared marker\n*** End Patch"})
    assert runtime.read_file({"path": str(target)})["content"] == "shared marker\n"
    relative = os.path.relpath(target, runtime.workspace.root)
    assert runtime.read_file({"path": relative})["content"] == "shared marker\n"
    assert any(row["name"] == "note.txt" for row in runtime.list_dir({"path": str(external)})["entries"])
    assert any(row["path"].endswith("note.txt") for row in runtime.list_files({"path": str(external)})["files"])
    assert runtime.search_text({"path": str(external), "query": "shared marker"})["total_matches"] == 1
    runtime.apply_patch({"patch": f"*** Begin Patch\n*** Update File: {relative}\n@@\n-shared marker\n+repaired marker\n*** End Patch"})
    assert target.read_text(encoding="utf-8") == "repaired marker\n"
    image = external / "tiny.png"
    Image.new("RGB", (2, 2), "white").save(image)
    assert runtime.view_image({"path": str(image)})
    moved = external / "nested" / "moved.txt"
    runtime.apply_patch({"patch": f"*** Begin Patch\n*** Update File: {target.as_posix()}\n*** Move to: {moved.as_posix()}\n@@\n-repaired marker\n+moved marker\n*** End Patch"})
    assert not target.exists() and moved.read_text(encoding="utf-8") == "moved marker\n"
    runtime.apply_patch({"patch": f"*** Begin Patch\n*** Delete File: {moved.as_posix()}\n*** End Patch"})
    assert not moved.exists()


def test_external_script_cwd_and_other_workflow_stay_independent(full_runtime):
    runtime, cfg, external = full_runtime
    other_project = cfg.workspace_root / "second"
    other_project.mkdir()
    other = build_coding(cfg, other_project)
    script = external / "inspect.py"
    script.write_text("import json, pathlib\nprint(json.dumps(str(pathlib.Path.cwd())))\n", encoding="utf-8")
    before = os.getcwd()
    try:
        result = runtime.exec_command({"cmd": f'"{sys.executable}" "{script}"',
                                       "workdir": str(external), "yield_time_ms": 1000})
        assert result["exit_code"] == 0, result
        assert json.loads(result["stdout"].strip()) == str(external)
        assert os.getcwd() == before
        assert runtime.workspace.root == cfg.project("app")
        assert other.workspace.root == other_project
        cmd = f'"{sys.executable}" -c "import time; time.sleep(30)"'
        a, b = [r.exec_command({"cmd": cmd, "yield_time_ms": 10}) for r in (runtime, other)]
        assert other.call_tool("write_stdin", {"command_id": a["command_id"], "chars": ""})["isError"]
        runtime.close()
        assert other.write_stdin({"command_id": b["command_id"], "chars": "", "yield_time_ms": 1})["status"] == "running"
        assert runtime.owned_job.active_pids() == []
    finally:
        other.close()


def test_full_control_filters_host_and_explicit_secret_environment(full_runtime, monkeypatch):
    runtime, _, _ = full_runtime
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-value")
    monkeypatch.setenv("PYTHONSTARTUP", "fixture-startup")
    env = runtime._command_env({"CUSTOM_TOKEN": "fixture-value", "BUILD_MODE": "release"})
    assert not {"OPENAI_API_KEY", "PYTHONSTARTUP", "CUSTOM_TOKEN"} & env.keys()
    assert env["BUILD_MODE"] == "release"
    assert runtime.shell_env_policy.inherit == "core"
    assert runtime.secret_env_filter_policy() == "enabled"


def test_full_control_preserves_explicit_shared_tool_environment(full_runtime, monkeypatch):
    runtime, _, external = full_runtime
    explicit = {name: str(external / name.lower()) for name in ("RUSTUP_HOME", "CARGO_HOME", "PIP_CACHE_DIR")}
    for name, value in explicit.items():
        Path(value).mkdir()
        monkeypatch.setenv(name, str(external / "unrequested-host-value"))
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-secret")
    assert not explicit.keys() & runtime._command_env({}).keys()
    script = external / "tool_env.py"
    script.write_text("import json, os\nprint(json.dumps({name: os.environ.get(name) for name in "
                      f"{[*explicit, 'OPENAI_API_KEY', 'BUILD_AUTH_TOKEN']!r}" + "}))\n", encoding="utf-8")
    result = runtime.exec_command({"cmd": f'"{sys.executable}" "{script}"', "workdir": str(external),
                                   "env": {**explicit, "BUILD_AUTH_TOKEN": "fixture-secret"}, "yield_time_ms": 1000})
    assert result["exit_code"] == 0, result
    observed = json.loads(result["stdout"].strip())
    assert observed == {**explicit, "OPENAI_API_KEY": None, "BUILD_AUTH_TOKEN": None}


@pytest.mark.parametrize("path", ["bad\x00name", "C:relative.txt", "bad*name", "nul.txt"])
def test_full_control_rejects_invalid_paths(full_runtime, path):
    runtime, _, _ = full_runtime
    with pytest.raises(ToolFailure):
        runtime.workspace.resolve_for_write(path)


@pytest.mark.parametrize("mode", ["safe", "trusted"])
def test_existing_modes_still_reject_external_paths(tmp_path, mode):
    cfg = replace(load_config(installation(tmp_path)), permission_mode=mode)
    runtime = build_coding(cfg, cfg.project("app"))
    try:
        with pytest.raises(ToolFailure):
            runtime.read_file({"path": str(tmp_path)})
        with pytest.raises(ToolFailure):
            runtime.exec_command({"cmd": "echo blocked", "workdir": str(tmp_path)})
    finally:
        runtime.close()


def test_external_project_home_and_ancestor_context_terminate(tmp_path, monkeypatch):
    import personal_mcp.coding as coding
    from coding_tools_mcp.project_context import LoadedContextFile, ProjectContext

    cfg = replace(load_config(installation(tmp_path)), permission_mode="full_control")
    external = tmp_path / "external-home"
    external.mkdir()
    (external / "note.txt").write_text("external home", encoding="utf-8")
    visited = []

    def context(root):
        visited.append(root)
        assert len(visited) <= len(external.parents) + 1
        files = (LoadedContextFile("AGENTS.md", "target rule", False),) if root == external else ()
        return ProjectContext(files, (), ())

    monkeypatch.setattr(coding, "load_workspace_context", context)
    monkeypatch.setattr(Path, "home", lambda: external)
    runtime = build_coding(cfg, external)
    try:
        assert visited[0] == Path(external.anchor)
        assert visited[-1] == external
        assert runtime.workspace.root == runtime.command_manager.workspace == external
        assert runtime.read_file({"path": "note.txt"})["content"] == "external home"
        instructions = runtime.project_context.server_instructions()
        assert "target rule" in instructions
        assert "not a filesystem boundary" in instructions
        assert "only for coding operations inside the configured workspace" not in instructions
    finally:
        runtime.close()
