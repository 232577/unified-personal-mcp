"""Per-workflow coding runtime adapters."""

import os
from pathlib import Path

from coding_tools_mcp.project_context import MAX_ROOT_CONTEXT_BYTES, LoadedContextFile, ProjectContext
from coding_tools_mcp.server import Runtime

from .config import AppConfig
from .overlays.console_policy import install_console_policy
from .overlays.context_override import load_workspace_context
from .overlays.windows_environment import repair_windows_pathext
from .windows_jobs import ACTIVE_JOB, OwnedJob


class LocalTelemetry:
    """The personal host has no external analytics or install tracking."""

    def record_request(self, *args, **kwargs):
        pass

    def record_session_start(self, *args, **kwargs):
        pass

    def record_tool_call(self, *args, **kwargs):
        pass

    def finish(self, *args, **kwargs):
        pass


class CodingRuntime(Runtime):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.owned_job = OwnedJob()

    def call_tool(self, *args, **kwargs):
        token = ACTIVE_JOB.set(self.owned_job)
        try:
            return super().call_tool(*args, **kwargs)
        finally:
            ACTIVE_JOB.reset(token)

    def exec_command(self, args):
        token = ACTIVE_JOB.set(self.owned_job)
        try:
            return super().exec_command(args)
        finally:
            ACTIVE_JOB.reset(token)

    def close(self):
        self.owned_job.close()
        super().close()

    def _command_env(self, extra):
        env = super()._command_env(extra)
        if os.name == "nt":
            env = repair_windows_pathext(env)
            env["USERPROFILE"] = str(self.command_home_dir())
        return env


def project_context(umbrella: Path, project: Path) -> ProjectContext:
    roots = [project]
    while roots[-1] != umbrella:
        roots.append(roots[-1].parent)
    loaded, warnings = [], []
    remaining = MAX_ROOT_CONTEXT_BYTES
    for root in reversed(roots):
        context = load_workspace_context(root)
        for item in context.root_files:
            data = item.content.encode("utf-8")
            content = data[:remaining].decode("utf-8", errors="ignore")
            if remaining > 0:
                label = (root.relative_to(umbrella) / item.path).as_posix()
                loaded.append(LoadedContextFile(label, content, item.truncated or len(data) > remaining))
                remaining -= len(content.encode("utf-8"))
        warnings.extend(context.warnings)
    return ProjectContext(tuple(loaded), (), tuple(dict.fromkeys(warnings)))


def build_coding(config: AppConfig, project: Path) -> CodingRuntime:
    project = config.project(str(project))
    install_console_policy()
    runtime = CodingRuntime(project, permission_mode=config.permission_mode,
                            project_context=project_context(config.workspace_root, project))
    runtime.telemetry = LocalTelemetry()
    private = config.data_root / "coding" / runtime.server_instance_id
    runtime.command_manager.runtime_dir = private
    runtime.command_manager.fallback_runtime_dir = None
    runtime._set_runtime_dir(private)
    runtime.fallback_runtime_dir = None
    return runtime
