"""Windows console policy scoped to backend modules, never global subprocess.

Shell policy, argument parsing, pipes and exit codes retain upstream semantics.
Windows cancellation and synchronous timeouts clean the owned process tree.
GUI executables are not hidden and no existing console window is touched.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

import psutil


class QuietSubprocess:
    """Module-local facade for the backend's Popen and run entry points."""

    def __init__(self, original: Any):
        self._original = original

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)

    @staticmethod
    def _options(options: dict[str, Any]) -> dict[str, Any]:
        result = dict(options)
        if os.name == "nt":
            flags = result.get("creationflags", 0)
            incompatible = subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS
            if flags & incompatible:
                raise ValueError("coding background process cannot request a new/detached console")
            result["creationflags"] = flags | subprocess.CREATE_NO_WINDOW
        return result

    def Popen(self, *args: Any, **kwargs: Any) -> Any:
        from personal_mcp.windows_jobs import ACTIVE_JOB

        job = ACTIVE_JOB.get()
        process = (job.spawn(*args, **self._options(kwargs)) if job else
                   self._original.Popen(*args, **self._options(kwargs)))
        try:
            process._coding_process_identity = psutil.Process(process.pid)
        except psutil.NoSuchProcess:
            pass  # A very short-lived command can finish before identity capture.
        return process

    def run(
        self, *args: Any, input: Any = None, capture_output: bool = False,
        timeout: float | None = None, check: bool = False, **kwargs: Any,
    ) -> Any:
        """Preserve run's public contract, but clean Windows descendants on timeout.

        The standard run helper only kills the immediate process. Its children
        may retain pipe handles or keep working after a timeout. Use the same
        owned-tree cleanup as exec_command before collecting diagnostic output.
        """
        if os.name != "nt":
            return self._original.run(
                *args, input=input, capture_output=capture_output,
                timeout=timeout, check=check, **kwargs,
            )
        if input is not None:
            if kwargs.get("stdin") is not None:
                raise ValueError("stdin and input arguments may not both be used")
            kwargs["stdin"] = subprocess.PIPE
        if capture_output:
            if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
                raise ValueError("stdout and stderr may not be used with capture_output")
            kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
        with self.Popen(*args, **kwargs) as process:
            try:
                stdout, stderr = process.communicate(input, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                terminate_owned_tree(process, None, force=True)
                # Bounded drain; a deliberately detached process holding a pipe
                # must not make a timed-out call wait indefinitely.
                try:
                    exc.stdout, exc.stderr = process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                raise
            except BaseException:
                terminate_owned_tree(process, None, force=True)
                raise
            returncode = process.poll()
            if check and returncode:
                raise subprocess.CalledProcessError(
                    returncode, process.args, output=stdout, stderr=stderr,
                )
        return subprocess.CompletedProcess(process.args, returncode, stdout, stderr)


def terminate_owned_tree(process: Any, signum: Any, *, force: bool = False) -> None:
    """Cancel one managed Windows tree, without sending events to a shared console.

    psutil Process objects retain creation-time identity and guard against PID
    reuse. Descendants are collected before ending the parent shell; killing by
    executable name or closing another workflow's console is never used.
    """
    del signum  # Windows terminate/kill both use TerminateProcess, not POSIX signals.
    root = getattr(process, "_coding_process_identity", None)
    if root is None:
        if process.poll() is not None:
            return
        try:
            root = psutil.Process(process.pid)
        except psutil.NoSuchProcess:
            return
    if not root.is_running():
        return
    try:
        owned = list(reversed(root.children(recursive=True))) + [root]
    except psutil.NoSuchProcess:
        return
    for item in owned:
        try:
            item.kill() if force else item.terminate()
        except psutil.NoSuchProcess:
            pass
    _, remaining = psutil.wait_procs(owned, timeout=1)
    for item in remaining:
        try:
            item.kill()
        except psutil.NoSuchProcess:
            pass
    process.wait(timeout=2)


def install_console_policy() -> None:
    """Cover exec, rg, git and project probes without changing third-party code."""
    if os.name != "nt":
        return
    from coding_tools_mcp import processes, project_context, server

    for module in (processes, project_context, server):
        if not isinstance(module.subprocess, QuietSubprocess):
            module.subprocess = QuietSubprocess(module.subprocess)
    processes.terminate_process_group = terminate_owned_tree
    server.terminate_process_group = terminate_owned_tree


def install_bf_console_policy() -> None:
    """Hide BF's internal PowerShell helpers, including timeout cleanup.

    Windows-MCP uses this helper for desktop discovery and notifications even
    when its public PowerShell tool is disabled. Keep application launchers and
    their GUI windows separate from this background-only path.
    """
    if os.name != "nt":
        return
    from windows_mcp.powershell import utils

    if not isinstance(utils.subprocess, QuietSubprocess):
        utils.subprocess = QuietSubprocess(utils.subprocess)
