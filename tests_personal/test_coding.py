import os
import sys
import time

from personal_mcp.coding import build_coding
from personal_mcp.config import load_config
from tests_personal.test_config import installation


def test_filtered_environment_is_repaired_without_reintroducing_secrets(tmp_path, monkeypatch):
    cfg = load_config(installation(tmp_path))
    monkeypatch.setenv("PATHEXT", ".CPL")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    runtime = build_coding(cfg, cfg.project("app"))
    try:
        env = runtime._command_env({})
        assert ".EXE" in env["PATHEXT"].split(";")
        assert "OPENAI_API_KEY" not in env
        assert os.environ["PATHEXT"] == ".CPL"
        assert env["USERPROFILE"] == str(runtime.command_home_dir())
        result = runtime.exec_command({"cmd": "where cmd.exe", "yield_time_ms": 0})
        deadline = time.monotonic() + 15
        while result["exit_code"] is None and time.monotonic() < deadline:
            result = runtime.write_stdin({"command_id": result["command_id"], "chars": "",
                                          "yield_time_ms": 1000})
        assert result["exit_code"] == 0, result
    finally:
        runtime.close()


def test_context_reads_ancestors_and_project_but_not_siblings(tmp_path):
    cfg = load_config(installation(tmp_path))
    (cfg.workspace_root / "AGENTS.md").write_text("umbrella rule", encoding="utf-8")
    project = cfg.project("app")
    (project / "CLAUDE.md").write_text("project rule", encoding="utf-8")
    other = cfg.workspace_root / "other"
    other.mkdir()
    (other / "AGENTS.md").write_text("unrelated rule", encoding="utf-8")
    runtime = build_coding(cfg, project)
    try:
        rules = runtime.project_context.server_instructions()
        assert "umbrella rule" in rules and "project rule" in rules
        assert "unrelated rule" not in rules
    finally:
        runtime.close()


def test_two_runtimes_reject_foreign_command_and_preserve_other(tmp_path):
    cfg = load_config(installation(tmp_path))
    (cfg.workspace_root / "second").mkdir()
    a, b = [build_coding(cfg, cfg.project(p)) for p in ("app", "second")]
    cmd = f'"{sys.executable}" -c "import time; print(42, flush=True); time.sleep(10)"'
    try:
        ra, rb = [r.exec_command({"cmd": cmd, "yield_time_ms": 1000}) for r in (a, b)]
        assert ra["status"] == rb["status"] == "running"
        denied = b.call_tool("write_stdin", {"command_id": ra["command_id"], "chars": ""})
        assert denied["isError"]
        a.close()
        live = b.write_stdin({"command_id": rb["command_id"], "chars": "", "yield_time_ms": 1})
        assert live["status"] == "running"
    finally:
        a.close()
        b.close()
