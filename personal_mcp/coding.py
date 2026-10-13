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
            result = super().call_tool(*args, **kwargs)
            name = args[0] if args else kwargs.get("name")
            if self.full_control and name == "request_permissions":
                notice = result.get("structuredContent", {}).get("warnings", [])
                if notice:
                    result["content"].append({"type": "text", "text": "\n".join(notice)})
            return result
        finally:
            ACTIVE_JOB.reset(token)

    def request_permissions(self, args):
        if not self.full_control:
            return super().request_permissions(args)
        # 查询已有配置不产生审批；full_control 仍保留敏感环境变量过滤。
        constraints = {"mode": "full_control", "workspace": str(self.workspace.root),
                       "secret_env_filter": "enabled", "existing_configuration": True,
                       "user_approval": False, "platform_approval": False}
        notice = ["This reports existing local configuration only, not user approval or platform approval. "
                  "It does not change permissions or override platform safety."]
        if args.get("permission") == "sensitive_env":
            return {"ok": False, "status": "denied", "grant_id": None, "expires_at": None,
                    "constraints": constraints, "warnings": notice,
                    "error": {"code": "SENSITIVE_ENV_FILTER_ENABLED", "category": "permission",
                              "retryable": False,
                              "message": "This installation filters sensitive and loader/startup environment "
                                         "variables, including explicitly supplied values. "
                                         "A permission query does not disable that filter."}}
        return {"ok": True, "status": "granted", "grant_id": "existing-full-control-policy",
                "expires_at": None, "constraints": constraints, "warnings": notice}

    def exec_command(self, args):
        token = ACTIVE_JOB.set(self.owned_job)
        try:
            return super().exec_command(args)
        finally:
            ACTIVE_JOB.reset(token)

    def close(self):
        self.owned_job.close()
        super().close()

    def command_inventory(self):
        """Owner-scoped handles; command strings and environment are deliberately omitted."""
        with self.commands_lock:
            commands = {**self.output_commands, **self.commands}
        return [{'command_id': key, 'pid': command.process.pid,
                 'status': 'running' if command.process.poll() is None else 'exited',
                 'started_at': command.started_at, 'completed_at': command.completed_at,
                 'stdout_ref': f'command:{key}:stdout', 'stderr_ref': f'command:{key}:stderr'}
                for key, command in commands.items()]

    def command_observation(self, command_id):
        """Non-consuming observation; never submit input, terminate or evict.

        An exited root does not prove its descendants stopped or its output was
        verified. A busy/missing handle is unknown, never an inferred success.
        """
        def unavailable(code):
            return {'ok': False, 'command_id': command_id, 'observation': 'unavailable',
                    'status': 'unknown', 'exit_code': None, 'error': {'code': code}}

        if not self.commands_lock.acquire(blocking=False):
            return unavailable('COMMAND_STATUS_BUSY')
        try:
            command = self.commands.get(command_id) or self.output_commands.get(command_id)
        finally:
            self.commands_lock.release()
        if command is None:
            return unavailable('COMMAND_NOT_FOUND')
        if not command.lock.acquire(blocking=False):
            return unavailable('COMMAND_STATUS_BUSY')
        try:
            exit_code = command.process.poll()
            return {'ok': True, 'command_id': command_id, 'observation': 'available',
                    'status': 'running' if exit_code is None else 'exited',
                    'exit_code': exit_code, 'started_at': command.started_at,
                    'completed_at': command.completed_at,
                    'output_complete': exit_code is not None and
                        not any(thread.is_alive() for thread in command.reader_threads),
                    'stdout_ref': f'command:{command_id}:stdout',
                    'stderr_ref': f'command:{command_id}:stderr'}
        except (OSError, ValueError):
            return unavailable('COMMAND_STATUS_UNAVAILABLE')
        finally:
            command.lock.release()

    def job_process_snapshot(self):
        """Observe job membership; these PID diagnostics never authorize termination."""
        unknown = {'status': 'unavailable', 'active_process_count': None,
                   'running_command_pids': None, 'descendant_pids': None,
                   'descendants_after_command_exit': None, 'diagnostic_only': True,
                   'error_code': 'JOB_PROCESS_STATE_BUSY'}
        if not self.commands_lock.acquire(blocking=False):
            return unknown
        try:
            # A spawned process is not a descendant merely because its command
            # root has not yet been registered in the inventory.
            if self.starting_commands:
                return unknown
            if not self.owned_job.lock.acquire(blocking=False):
                return unknown
            try:
                active = set(self.owned_job.active_pids())
                commands = {**self.output_commands, **self.commands}
                roots = {command.process.pid for command in commands.values()
                         if command.process.pid in active and command.process.poll() is None}
                descendants = active - roots
                return {'status': 'available', 'active_process_count': len(active),
                        'running_command_pids': sorted(roots), 'descendant_pids': sorted(descendants),
                        'descendants_after_command_exit': bool(descendants) and not roots,
                        'diagnostic_only': True}
            except Exception:
                return {**unknown, 'error_code': 'JOB_PROCESS_STATE_UNAVAILABLE'}
            finally:
                self.owned_job.lock.release()
        finally:
            self.commands_lock.release()

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
