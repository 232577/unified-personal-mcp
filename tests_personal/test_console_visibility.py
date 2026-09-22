"""Exercise real Windows children; no global process or desktop manipulation."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from personal_mcp.bf.bootstrap import build_mcp
from personal_mcp.coding import build_coding
from personal_mcp.config import load_config
from tests_personal.test_config import installation

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows console behavior")

CONSOLE_PROBE = (
    "Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; "
    "public class ConsoleProbe { [DllImport(\"kernel32.dll\")] "
    "public static extern IntPtr GetConsoleWindow(); }'; "
    "[ConsoleProbe]::GetConsoleWindow().ToInt64()"
)


def test_bf_helpers_from_detached_pythonw_do_not_open_console(tmp_path):
    # The service runs via pythonw at logon. A test runner's inherited hidden
    # console can mask the missing flags, so reproduce that exact parent type.
    result_path = tmp_path / "console.json"
    script = tmp_path / "probe.py"
    script.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from personal_mcp.bf.bootstrap import build_mcp\n"
        f"build_mcp(state_root={str(tmp_path / 'state')!r}, allowed_root={str(tmp_path)!r})\n"
        "from windows_mcp.powershell import PowerShellExecutor\n"
        f"output, code = PowerShellExecutor.execute_command({CONSOLE_PROBE!r}, shell='powershell')\n"
        f"Path({str(result_path)!r}).write_text(json.dumps({{'output':output,'code':code}}))\n",
        encoding="utf-8",
    )
    with (tmp_path / "probe.log").open("wb") as log:
        process = subprocess.Popen(
            [str(Path(sys.executable).with_name("pythonw.exe")), str(script)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.DETACHED_PROCESS,
        )
        try:
            assert process.wait(timeout=30) == 0, (tmp_path / "probe.log").read_text()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
    result = json.loads(result_path.read_text())
    assert result["code"] == 0, result
    assert result["output"].strip() == "0", "pythonw-hosted BF helper opened a console"


def test_bf_powershell_helper_has_no_console_and_returns_output(tmp_path):
    build_mcp(state_root=tmp_path / "state", allowed_root=tmp_path)
    from windows_mcp.powershell import PowerShellExecutor

    output, code = PowerShellExecutor.execute_command(CONSOLE_PROBE, shell="powershell")
    assert code == 0, output
    assert output.strip() == "0", "BF helper allocated or inherited a console window"


def test_bf_nested_console_child_and_exit_code_are_preserved(tmp_path):
    build_mcp(state_root=tmp_path / "state", allowed_root=tmp_path)
    from windows_mcp.powershell.utils import run_with_graceful_timeout

    probe = "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow()); raise SystemExit(7)"
    result = run_with_graceful_timeout(
        [sys.executable, "-c", probe], capture_output=True, timeout=10,
    )
    assert result.returncode == 7
    assert result.stdout.strip() == b"0", "BF subprocess still owns a console window"


def test_unified_coding_shell_and_child_remain_quiet_with_output(tmp_path):
    cfg = load_config(installation(tmp_path))
    runtime = build_coding(cfg, cfg.project("app"))
    try:
        probe = "import ctypes,json; print(json.dumps({'console':ctypes.windll.kernel32.GetConsoleWindow()}))"
        result = runtime.exec_command({
            "cmd": subprocess.list2cmdline([sys.executable, "-c", probe]),
            "yield_time_ms": 1000,
        })
        assert result["exit_code"] == 0, result
        assert json.loads(result["stdout"]) == {"console": 0}
    finally:
        runtime.close()
