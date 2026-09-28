"""Exercise the portable Windows launcher against an isolated managed child."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "verification" / "launcher-tests"
CSC = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"


@pytest.fixture
def bundle():
    FIXTURES.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fixture-", dir=FIXTURES) as directory:
        path = Path(directory) / "包 含空格"
        path.mkdir()
        yield path


def compile_cs(source: Path, output: Path, *, windowed=False):
    subprocess.run(
        [str(CSC), "/nologo", "/target:winexe" if windowed else "/target:exe",
         "/reference:System.Windows.Forms.dll", "/out:" + str(output), str(source)],
        check=True, timeout=30, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )


def prepare(bundle: Path):
    python = bundle / "resources" / "python"
    python.mkdir(parents=True)
    (bundle / "app").mkdir()
    (bundle / "app" / "run.py").write_text("# fixture\n", encoding="utf-8")
    (bundle / "app" / "startup_diagnostics.py").write_text("# fixture\n", encoding="utf-8")
    for resource in ("python3.dll", "python313.dll"):
        (python / resource).write_bytes(b"fixture")
    (python / "Lib").mkdir()
    child_source = bundle / "FakeChild.cs"
    child_source.write_text(
        "using System; using System.IO; using System.Runtime.InteropServices;\n"
        "class FakeChild {\n"
        " [DllImport(\"kernel32.dll\")] static extern IntPtr GetConsoleWindow();\n"
        " static int Main(string[] args) {\n"
        "  Console.OutputEncoding = System.Text.Encoding.UTF8;\n"
        "  var output = Environment.GetEnvironmentVariable(\"FIXTURE_OBSERVATION\");\n"
        "  File.WriteAllLines(output, new [] {\n"
        "   string.Join(\"|\", args),\n"
        "   GetConsoleWindow().ToInt64().ToString(),\n"
        "   Environment.GetEnvironmentVariable(\"TCL_LIBRARY\") ?? \"<unset>\",\n"
        "   Environment.GetEnvironmentVariable(\"TK_LIBRARY\") ?? \"<unset>\",\n"
        "   Environment.GetEnvironmentVariable(\"PYTHONHOME\") ?? \"<unset>\",\n"
        "   Environment.GetEnvironmentVariable(\"PYTHONPATH\") ?? \"<unset>\",\n"
        "   Environment.GetEnvironmentVariable(\"PRESERVE_FIXTURE\") ?? \"<unset>\" });\n"
        "  bool fail = Array.IndexOf(args, \"--fail-fixture\") >= 0;\n"
        "  if (fail) { Console.Out.Write(new string('O', 120000));\n"
        "    Console.Error.Write(new string('E', 120000) + \"\\nfixture traceback 中文故障\\n\"); return 7; }\n"
        "  Console.Out.WriteLine(\"fixture diagnostic OK 中文诊断\"); return 0;\n"
        " }\n} \n",
        encoding="utf-8",
    )
    compile_cs(child_source, python / "python.exe")
    shutil.copy2(python / "python.exe", python / "pythonw.exe")
    compile_cs(ROOT / "packaging" / "Launcher.cs", bundle / "UnifiedPersonalMCP.exe", windowed=True)


def invoke(bundle: Path, *args):
    observation = bundle / "observed.txt"
    local = bundle / "private-local"
    local.mkdir(exist_ok=True)
    environment = {**os.environ, "LOCALAPPDATA": str(local),
                   "FIXTURE_OBSERVATION": str(observation), "TCL_LIBRARY": "bad-tcl",
                   "TK_LIBRARY": "bad-tk", "PYTHONHOME": "bad-home",
                   "PYTHONPATH": "bad-path", "PRESERVE_FIXTURE": "keep"}
    completed = subprocess.run(
        [str(bundle / "UnifiedPersonalMCP.exe"), "--no-dialog", *args],
        cwd=bundle, env=environment, timeout=20, capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    logs = list((local / "UnifiedPersonalMCP" / "diagnostics").glob("*.log"))
    return completed, observation, logs


def test_child_failure_is_visible_in_private_bounded_log(bundle):
    prepare(bundle)
    result, observation, logs = invoke(bundle, "--fail-fixture")
    assert result.returncode == 7
    assert observation.exists()
    assert len(logs) == 1, "a silent child exit leaves no diagnostic trail"
    content = logs[0].read_text(encoding="utf-8")
    assert "7" in content
    assert "fixture traceback" in content
    assert "中文故障" in content
    assert len(content) < 50000, "large single-line output must remain bounded"
    assert "bad-home" not in content
    assert "--fail-fixture" not in content, "do not log the command line"


def test_success_preserves_args_isolates_environment_and_writes_no_log(bundle):
    prepare(bundle)
    result, observation, logs = invoke(bundle, "路径 含 空格\\末尾\\", "汉字", 'quote"value')
    assert result.returncode == 0
    lines = observation.read_text(encoding="utf-8").splitlines()
    assert lines[0].split("|") == ["-I", "-B", "-X", "utf8", str(bundle / "app" / "run.py"),
                                   "路径 含 空格\\末尾\\", "汉字", 'quote"value',
                                   "--assets", str(bundle / "resources")]
    assert lines[1] == "0", "the console child must not create a console window"
    assert lines[2:6] == ["<unset>"] * 4
    assert lines[6] == "keep"
    assert not logs


def test_diagnose_runs_dedicated_entry_and_records_success(bundle):
    prepare(bundle)
    result, observation, logs = invoke(bundle, "--diagnose")
    assert result.returncode == 0
    assert observation.read_text(encoding="utf-8").splitlines()[0].split("|")[:5] == [
        "-I", "-B", "-X", "utf8", str(bundle / "app" / "startup_diagnostics.py")]
    assert len(logs) == 1
    assert "fixture diagnostic OK" in logs[0].read_text(encoding="utf-8")
    assert "中文诊断" in logs[0].read_text(encoding="utf-8")


def test_default_launch_opens_gui_command_without_diagnostic_log(bundle):
    prepare(bundle)
    result, observation, logs = invoke(bundle)
    assert result.returncode == 0
    assert observation.read_text(encoding="utf-8").splitlines()[0].split("|")[:6] == [
        "-I", "-B", "-X", "utf8", str(bundle / "app" / "run.py"), "gui"]
    assert not logs


@pytest.mark.parametrize("missing", ["resources/python/python.exe", "app/run.py", "resources/python/python313.dll"])
def test_missing_runtime_resource_gets_explanatory_log(bundle, missing):
    prepare(bundle)
    (bundle / missing).unlink()
    result, observation, logs = invoke(bundle)
    assert result.returncode != 0
    assert not observation.exists()
    assert len(logs) == 1
    assert "缺少" in logs[0].read_text(encoding="utf-8")
