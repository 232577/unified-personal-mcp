"""Per-workflow coding runtime adapters."""

import os
from dataclasses import replace
from pathlib import Path

from coding_tools_mcp.project_context import MAX_ROOT_CONTEXT_BYTES, LoadedContextFile, ProjectContext
from coding_tools_mcp.server import Runtime, ShellEnvPolicy, is_filtered_env_var

from .config import AppConfig
from .full_control import FullControlProjectContext, FullControlWorkspace
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
    def __init__(self, workspace, *args, full_control=False, **kwargs):
        self.full_control = full_control
        local_workspace = FullControlWorkspace(workspace) if full_control else None
        if full_control:
            kwargs["permission_mode"] = "dangerous"
            kwargs["shell_env_policy"] = ShellEnvPolicy(inherit="core")
            # Upstream refuses the user's home as its initial root. Initialize
            # its manager at the parent, then bind our explicit full-access view
            # before any tool can run. build_coding assigns private runtime state.
            if local_workspace.root == Path.home().resolve():
                workspace = local_workspace.root.parent
        super().__init__(workspace, *args, **kwargs)
        if full_control:
            self.workspace = local_workspace
            self.command_manager.workspace = local_workspace.root
            self.permission_mode = "full_control"
            self.capabilities = replace(self.capabilities, secret_env_filter=True)
            self.project_context = FullControlProjectContext(
                self.project_context.root_files, self.project_context.nested_files, self.project_context.warnings)
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
        if self.full_control:
            env = {key: value for key, value in env.items() if not is_filtered_env_var(key, value)}
        if os.name == "nt":
            env = repair_windows_pathext(env)
            env["USERPROFILE"] = str(self.command_home_dir())
        return env

    def _scan(self, name, args):
        method = getattr(super(), name)
        if not self.full_control:
            return method(args)
        path = self.resolve_existing(str(args.get("path", "."))).path
        with self.workspace.scan(path):
            return method(args)

    def list_dir(self, args):
        return self._scan("list_dir", args)

    def list_files(self, args):
        return self._scan("list_files", args)

    def search_text(self, args):
        return self._scan("search_text", args)


def project_context(umbrella: Path, project: Path) -> ProjectContext:
    roots = [project]
    while roots[-1] != umbrella and roots[-1].parent != roots[-1]:
        roots.append(roots[-1].parent)
    loaded, warnings = [], []
    remaining = MAX_ROOT_CONTEXT_BYTES
    for root in reversed(roots):
        context = load_workspace_context(root)
        for item in context.root_files:
            data = item.content.encode("utf-8")
            content = data[:remaining].decode("utf-8", errors="ignore")
            if remaining > 0:
                label = ((root.relative_to(umbrella) if root.is_relative_to(umbrella) else root)
                         / item.path).as_posix()
                loaded.append(LoadedContextFile(label, content, item.truncated or len(data) > remaining))
                remaining -= len(content.encode("utf-8"))
        warnings.extend(context.warnings)
    return ProjectContext(tuple(loaded), (), tuple(dict.fromkeys(warnings)))


def build_coding(config: AppConfig, project: Path) -> CodingRuntime:
    project = config.project(str(project))
    install_console_policy()
    runtime = CodingRuntime(project, permission_mode=config.permission_mode,
                            full_control=config.permission_mode == "full_control",
                            project_context=project_context(config.workspace_root, project))
    runtime.telemetry = LocalTelemetry()
    private = config.data_root / "coding" / runtime.server_instance_id
    runtime.command_manager.runtime_dir = private
    runtime.command_manager.fallback_runtime_dir = None
    runtime._set_runtime_dir(private)
    runtime.fallback_runtime_dir = None
    return runtime
