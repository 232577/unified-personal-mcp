from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from coding_tools_mcp.errors import JsonRpcError, ToolFailure
from coding_tools_mcp.protocol import RequestContext, _call_tool
from coding_tools_mcp.processes import CommandRun
from coding_tools_mcp import server as server_module
from coding_tools_mcp.server import Runtime, ShellEnvPolicy, Workspace


class FilePaginationBoundaryTests(unittest.TestCase):
    def test_pages_preserve_physical_lines(self) -> None:
        for endings in (("\n",) * 4, ("\r\n",) * 4, ("\r",) * 4, ("\r\n", "\r", "\n", "")):
            with self.subTest(endings=endings), TemporaryDirectory() as tmp:
                root = Path(tmp)
                lines = [f"line{n}{ending}" for n, ending in enumerate(endings, 1)]
                (root / "paged.txt").write_bytes("".join(lines).encode("utf-8"))
                runtime = Runtime(root)
                try:
                    args = {"path": "paged.txt", "max_bytes": 14}
                    seen = []
                    for _ in range(4):
                        result = runtime.read_file(args)
                        expected = lines[result["start_line"] - 1 : result["end_line"]]
                        self.assertEqual(result["content"], "".join(expected))
                        self.assertEqual(result["output_lines"], len(expected))
                        seen.extend(expected)
                        if "next_action" not in result:
                            break
                        args = result["next_action"]["arguments"]
                    self.assertEqual(seen, lines)
                finally:
                    runtime.close()

    def test_continuation_preserves_requested_range(self) -> None:
        for bounds in ({"end_line": 2}, {"max_lines": 2}):
            with self.subTest(bounds=bounds), TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "bounded.txt").write_bytes(b"l1\nl2\nl3\n")
                runtime = Runtime(root)
                try:
                    first = runtime.read_file({"path": "bounded.txt", "max_bytes": 3, **bounds})
                    action = first["next_action"]
                    second = runtime.read_file(action["arguments"])
                    self.assertEqual(second["content"], "l2\n")
                    self.assertFalse(second["truncated"])
                    self.assertNotIn("next_action", second)
                finally:
                    runtime.close()

    def test_partial_last_requested_line_does_not_continue_outside_range(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bounded.txt").write_bytes(b"long\nnext\n")
            runtime = Runtime(root)
            try:
                result = runtime.read_file({"path": "bounded.txt", "max_bytes": 1, "end_line": 1})
                self.assertTrue(result["truncated"])
                self.assertNotIn("next_action", result)
            finally:
                runtime.close()


class ToolArgumentBoundaryTests(unittest.TestCase):
    def test_falsy_nonobject_arguments_are_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            runtime = Runtime(Path(tmp))
            try:
                for invalid in ([], False, 0, ""):
                    with self.subTest(arguments=invalid):
                        with self.assertRaises(JsonRpcError) as caught:
                            _call_tool(runtime, {"name": "server_info", "arguments": invalid}, RequestContext())
                        self.assertEqual(caught.exception.code, -32602)
                self.assertFalse(_call_tool(runtime, {"name": "server_info", "arguments": None}, RequestContext())["isError"])
            finally:
                runtime.close()


class CommandPollingBoundaryTests(unittest.TestCase):
    def test_poll_returns_new_output_after_rolling_buffer_eviction(self) -> None:
        with TemporaryDirectory() as tmp:
            runtime = Runtime(Path(tmp))
            command = CommandRun("pending", SimpleNamespace(poll=lambda: None), buffer_limit=8)
            command.append_stdout(b"12345678")
            command.snapshot_since_cursor(100)
            now = [0.0]
            sleeps = []

            def deliver_output(delay: float) -> None:
                sleeps.append(delay)
                now[0] += delay
                if len(sleeps) == 1:
                    command.append_stdout(b"9")

            try:
                with patch.object(runtime, "_get_command", return_value=command), patch.object(server_module.time, "time", side_effect=lambda: now[0]), patch.object(server_module.time, "sleep", side_effect=deliver_output):
                    result = runtime.write_stdin({"command_id": "pending", "yield_time_ms": 200})
                self.assertEqual(result["stdout"], "9")
                self.assertEqual(len(sleeps), 1, "new bytes must end the poll without spending the full wait budget")
            finally:
                runtime.close()


@unittest.skipUnless(shutil.which("git"), "git not installed")
class GitFixtureCase(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        self.env_patch.start()
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        self.runtime = Runtime(self.root, shell_env_policy=ShellEnvPolicy(set={"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}))

    def tearDown(self) -> None:
        self.runtime.close()
        self.tmp.cleanup()
        self.env_patch.stop()

    def commit(self, subject: str) -> None:
        (self.root / "file.txt").write_bytes(b"hello\n")
        subprocess.run(["git", "-C", str(self.root), "add", "file.txt"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Audit", "-c", "user.email=audit@example.invalid", "commit", "-qm", subject], check=True, capture_output=True)


class GitEncodingBoundaryTests(GitFixtureCase):
    def test_git_log_ignores_host_pipe_encoding(self) -> None:
        self.commit("中文 café")
        with patch("subprocess._text_encoding", return_value="cp1252"):
            result = self.runtime.git_log({})
        self.assertEqual(result["commits"][0]["subject"], "中文 café")

    def test_git_log_overrides_configured_nonutf8_output(self) -> None:
        self.commit("café")
        subprocess.run(["git", "-C", str(self.root), "config", "i18n.logOutputEncoding", "ISO-8859-1"], check=True, capture_output=True)
        result = self.runtime.git_log({})
        self.assertEqual(result["commits"][0]["subject"], "café")

    def test_invalid_blame_bytes_return_structured_error(self) -> None:
        self.commit("initial")
        (self.root / "file.txt").write_bytes(b"caf\xff\n")
        with self.assertRaises(ToolFailure) as caught:
            self.runtime.git_blame({"path": "file.txt", "end_line": 1})
        self.assertEqual(caught.exception.code, "GIT_ERROR")

    def test_git_ignore_path_transport_ignores_host_pipe_encoding(self) -> None:
        name = "ignored-文件.txt"
        (self.root / name).write_bytes(b"hello\n")
        (self.root / ".gitignore").write_bytes((name + "\n").encode("utf-8"))
        with patch("subprocess._text_encoding", return_value="cp1252"):
            ignored = Workspace(self.root).git_ignored_paths([name])
        self.assertIn(name, ignored)

    @unittest.skipIf(os.name == "nt", "POSIX byte filenames only")
    def test_git_ignore_preserves_nonutf8_filenames(self) -> None:
        name = os.fsdecode(b"ignored-\xff.txt")
        (self.root / name).write_bytes(b"hello\n")
        (self.root / ".gitignore").write_bytes(b"ignored-\xff.txt\n")
        self.assertIn(name, Workspace(self.root).git_ignored_paths([name]))


@unittest.skipUnless(shutil.which("git"), "git not installed")
class GitReadOnlyHelperTests(GitFixtureCase):
    def setUp(self) -> None:
        super().setUp()
        self.commit("initial")
        self.helper = self.root / "helper.py"
        self.marker = self.root / "helper-ran.txt"
        self.helper.write_text(
            "from pathlib import Path\nimport sys\nPath(sys.argv[1]).write_text('ran', encoding='utf-8')\nprint('converted')\n",
            encoding="utf-8",
        )
        self.helper_command = f'"{Path(sys.executable).as_posix()}" "{self.helper.as_posix()}" "{self.marker.as_posix()}"'

    def configure(self, key: str, value: str) -> None:
        subprocess.run(["git", "-C", str(self.root), "config", key, value], check=True, capture_output=True)

    def test_diff_does_not_execute_external_diff(self) -> None:
        self.configure("diff.external", self.helper_command)
        (self.root / "file.txt").write_bytes(b"changed\n")
        result = self.runtime.git_diff({})
        self.assertFalse(self.marker.exists(), "read-only diff executed external helper")
        self.assertIn("+changed", result["diff"])

    def test_diff_does_not_execute_textconv(self) -> None:
        (self.root / ".gitattributes").write_bytes(b"file.txt diff=custom\n")
        self.configure("diff.custom.textconv", self.helper_command)
        (self.root / "file.txt").write_bytes(b"changed\n")
        result = self.runtime.git_diff({})
        self.assertFalse(self.marker.exists(), "read-only diff executed textconv helper")
        self.assertIn("+changed", result["diff"])

    def test_status_does_not_execute_fsmonitor(self) -> None:
        self.configure("core.fsmonitor", self.helper_command)
        self.runtime.git_status({})
        self.assertFalse(self.marker.exists(), "read-only status executed fsmonitor helper")

    def test_status_does_not_execute_clean_filter(self) -> None:
        (self.root / ".gitattributes").write_bytes(b"file.txt filter=custom\n")
        self.configure("filter.custom.clean", self.helper_command)
        (self.root / "file.txt").write_bytes(b"changed\n")
        self.runtime.git_status({})
        self.assertFalse(self.marker.exists(), "read-only status executed clean filter")

    def test_diff_does_not_execute_clean_filter(self) -> None:
        (self.root / ".gitattributes").write_bytes(b"file.txt filter=custom\n")
        self.configure("filter.custom.clean", self.helper_command)
        (self.root / "file.txt").write_bytes(b"changed\n")
        result = self.runtime.git_diff({})
        self.assertFalse(self.marker.exists(), "read-only diff executed clean filter")
        self.assertIn("+changed", result["diff"])
        configured = subprocess.run(["git", "-C", str(self.root), "config", "--get", "filter.custom.clean"], check=True, capture_output=True)
        self.assertEqual(configured.stdout.decode("utf-8").strip(), self.helper_command)

    def test_show_does_not_execute_textconv(self) -> None:
        (self.root / ".gitattributes").write_bytes(b"file.txt diff=custom\n")
        self.configure("diff.custom.textconv", self.helper_command)
        result = self.runtime.git_show({})
        self.assertFalse(self.marker.exists(), "read-only show executed textconv helper")
        self.assertIn("+hello", result["content"])

    def test_blame_does_not_execute_clean_filter(self) -> None:
        (self.root / ".gitattributes").write_bytes(b"file.txt filter=custom\n")
        self.configure("filter.custom.clean", self.helper_command)
        (self.root / "file.txt").write_bytes(b"changed\n")
        self.runtime.git_blame({"path": "file.txt", "end_line": 1})
        self.assertFalse(self.marker.exists(), "read-only blame executed clean filter")

    def test_queries_do_not_execute_submodule_clean_filter(self) -> None:
        nested = self.root / "nested"
        subprocess.run(["git", "init", "-q", str(nested)], check=True, capture_output=True)
        (nested / "file.txt").write_bytes(b"hello\n")
        subprocess.run(["git", "-C", str(nested), "add", "file.txt"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(nested), "-c", "user.name=Audit", "-c", "user.email=audit@example.invalid", "commit", "-qm", "nested"], check=True, capture_output=True)
        revision = subprocess.run(["git", "-C", str(nested), "rev-parse", "HEAD"], check=True, capture_output=True).stdout.decode("ascii").strip()
        subprocess.run(["git", "-C", str(self.root), "update-index", "--add", "--cacheinfo", f"160000,{revision},nested"], check=True, capture_output=True)
        (self.root / ".gitmodules").write_bytes(b'[submodule "nested"]\n\tpath = nested\n\turl = ./nested\n')
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Audit", "-c", "user.email=audit@example.invalid", "commit", "-qm", "record submodule"], check=True, capture_output=True)
        (nested / ".gitattributes").write_bytes(b"file.txt filter=nested\n")
        subprocess.run(["git", "-C", str(nested), "config", "filter.nested.clean", self.helper_command], check=True, capture_output=True)
        (nested / "file.txt").write_bytes(b"changed\n")
        self.configure("diff.submodule", "diff")
        diff = self.runtime.git_diff({})
        status = self.runtime.git_status({})
        self.assertFalse(self.marker.exists(), "read-only query executed submodule helper")
        self.assertTrue(any("Submodule working-tree" in item for item in diff["warnings"]))
        self.assertTrue(any("Submodule working-tree" in item for item in status["warnings"]))
        subprocess.run(["git", "-c", "filter.nested.clean=", "-C", str(nested), "add", "file.txt"], check=True, capture_output=True)
        subprocess.run(["git", "-c", "filter.nested.clean=", "-C", str(nested), "-c", "user.name=Audit", "-c", "user.email=audit@example.invalid", "commit", "-qm", "new nested revision"], check=True, capture_output=True)
        revision_diff = self.runtime.git_diff({})
        revision_status = self.runtime.git_status({})
        self.assertIn("Subproject commit", revision_diff["diff"])
        self.assertTrue(any(item["path"] == "nested" for item in revision_status["entries"]))
        self.assertFalse(self.marker.exists(), "revision queries must also avoid helpers")
