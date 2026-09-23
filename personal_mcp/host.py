"""Single authenticated HTTP host for coding and in-process BF automation."""

import json
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError
from coding_tools_mcp.errors import JsonRpcError
from coding_tools_mcp.project_context import LoadedContextFile, ProjectContext
from coding_tools_mcp.server import Runtime, TOOL_REGISTRY

from . import __version__
from .bf.executor import BFExecutor
from .catalog import catalog_revision, unified_catalog
from .coding import LocalTelemetry, build_coding
from .full_control import FullControlProjectContext
from .operations import OperationJournal
from .search import SearchManager
from .workflows import AuthPrincipal, WorkflowError, WorkflowRegistry


def tool_result(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": payload, "isError": payload.get("ok") is False}


class WorkflowResources:
    def __init__(self, host, row):
        import win32crypt

        self.host, self.key = host, row["token_hash"]
        self.coding = build_coding(host.config, Path(row["project"]))
        self.bf_token = None
        self.bf_closed = False
        try:
            self.bf_token = host.bf_server._bf_store.begin(row["project"], context_id=self.key)["bf_task_id"]
            private = host.config.data_root / "workflow-secrets"
            private.mkdir(exist_ok=True)
            protected = win32crypt.CryptProtectData(self.bf_token.encode(), "Unified Personal MCP", None, None, None, 0)
            (private / (self.key + ".bin")).write_bytes(protected)
        except BaseException:
            self.close()
            raise

    def idle_blockers(self):
        """Observe owned resources while the registry holds this workflow's gate."""
        blockers = []
        if self.coding.owned_job.active_pids():
            blockers.append("running_commands")
        with self.host.bf.lock:
            if self.host.bf.closed or self.host.bf.failure:
                raise RuntimeError("DESKTOP_RESOURCE_STATE_UNKNOWN")
            if any(not future.done() for future in self.host.bf.pending.get(self.bf_token, ())):
                blockers.append("desktop_operations")
        store = self.host.bf_server._bf_store
        with store._lock:
            _, record = store.require(self.bf_token)
            task_key = record["task_key"]
            if any(job.active_pids() for job in store._owned_jobs.get(task_key, ())):
                blockers.append("native_applications")
        browser = getattr(self.host.bf_server, "_bf_browser_manager", None)
        if browser is not None:
            with browser.lock:
                if any(session.owner == task_key for session in browser.sessions.values()):
                    blockers.append("browser_sessions")
        hybrid = getattr(self.host.bf_server, "_bf_hybrid_manager", None)
        if hybrid is not None:
            with hybrid._lock:
                if (any(instance.owner == task_key for instance in hybrid.instances.values())
                        or task_key in hybrid._pending.values()
                        or any(owner == task_key for owner, _ in hybrid._failed_launches.values())):
                    blockers.append("webview2_instances")
        blockers.extend(self.host.searches.idle_blockers(self.key))
        return blockers

    def close(self):
        errors = []
        try:
            if self.bf_token and not self.bf_closed:
                self.host.bf.drain(self.bf_token)
                self.host.bf_server._bf_store.end(self.bf_token)
                self.bf_closed = True
        except Exception as exc:
            errors.append(exc)
        for release in (lambda: self.host.searches.end_owner(self.key), self.coding.close):
            try:
                release()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError("WORKFLOW_CLEANUP_INCOMPLETE") from errors[0]


class UnifiedRuntime(Runtime):
    def __init__(self, config, *, auth_token, bf_server, rg_path=None):
        if not isinstance(auth_token, str) or len(auth_token) < 32:
            raise ValueError("a private backend bearer key of at least 32 characters is required")
        self.config, self.bf_server = config, bf_server
        self.principal = AuthPrincipal("local-owner")
        context = ProjectContext((LoadedContextFile("Unified workflow",
            "Begin UnifiedTask for a concrete project, keep the one-time workflow_id and activate it. "
            "Pass workflow_id to coding and desktop tools. Read project instructions before edits. "
            "Prefer Window tools for desktop work. Observe unknown outcomes; do not replay. End the workflow when done.", False),), (), ())
        if config.full_control:
            context = FullControlProjectContext(context.root_files, context.nested_files, context.warnings)
        super().__init__(config.workspace_root, auth_token=auth_token, permission_mode=config.runtime_permission_mode,
                         project_context=context, transport="http")
        self.telemetry = LocalTelemetry()
        self.catalog = unified_catalog(bf_server, full_control=config.full_control,
                                       search_sessions=config.search_sessions)
        self.catalog_revision = catalog_revision(self.catalog)
        self.health_provider = None
        self._usage_cache = {'commands': 0, 'retained_commands': 0, 'coding_job_active_processes': None,
                             'browser_sessions': 0,
                             'webview2_instances': 0, 'search_sessions': 0}
        self.validators = {k: Draft202012Validator(v["inputSchema"]) for k, v in self.catalog.items()}
        self._exposed_tool_names = list(self.catalog)
        self._exposed_tool_name_set = frozenset(self.catalog)
        self.searches = SearchManager(rg=rg_path, max_sessions=config.search_sessions)
        self.operations = OperationJournal(config.data_root / "operations.sqlite3")
        self.bf = BFExecutor(bf_server)
        try:
            self.registry = WorkflowRegistry(config, lambda row: WorkflowResources(self, row),
                recovery=lambda row: self.bf_server._bf_store.recover_context(row["token_hash"]))
            self.recovery_status = self.registry.recover()
        except BaseException:
            self.bf.close()
            self.searches.close()
            super().close()
            raise

    def server_identity(self):
        return {"name": "unified-personal-mcp", "title": "Unified Personal MCP", "version": __version__}

    def list_tools(self):
        return {"tools": deepcopy(list(self.catalog.values()))}

    def health_snapshot(self):
        health = self.health_provider() if self.health_provider else {
            'status': 'healthy', 'tunnel': {'status': 'disabled', 'last_success': None,
                'error_code': None, 'recovery_attempts': 0, 'next_retry': None}}
        health = deepcopy(health)
        health['bf'] = self.bf.snapshot()
        states = {health['tunnel']['status'], health['bf']['status']}
        if states & {'degraded', 'stopped'}:
            health['status'] = 'degraded'
        elif 'recovering' in states:
            health['status'] = 'recovering'
        return health

    def resource_usage(self):
        metadata = self.registry.snapshot(self.principal)
        counters = dict(self._usage_cache)
        stale = bool(metadata.get('stale'))
        resources = None
        if self.registry.lock.acquire(blocking=False):
            try:
                resources = [resource for key, resource in self.registry.resources.items()
                             if self.registry._metadata[key]['owner'] == self.principal.key]
            finally:
                self.registry.lock.release()
        if resources is None:
            stale = True
        else:
            commands = retained = 0
            complete = True
            for resource in resources:
                coding = resource.coding
                if not coding.commands_lock.acquire(blocking=False):
                    complete = False
                    break
                try:
                    commands += sum(command.process.poll() is None for command in coding.commands.values())
                    retained += len(coding.output_commands)
                finally:
                    coding.commands_lock.release()
            if complete:
                counters.update(commands=commands, retained_commands=retained)
            else:
                stale = True
            job_processes = 0
            for resource in resources:
                observed = resource.coding.job_process_snapshot()
                if observed['status'] != 'available':
                    stale = True
                    break
                job_processes += observed['active_process_count']
            else:
                counters['coding_job_active_processes'] = job_processes
            keys = {resource.key for resource in resources}
            task_keys = {self.bf_server._bf_store._task_key(resource.bf_token)
                         for resource in resources if resource.bf_token}
            browser = getattr(self.bf_server, '_bf_browser_manager', None)
            hybrid = getattr(self.bf_server, '_bf_hybrid_manager', None)
            for label, manager, lock_name, collection, owners in (
                ('browser_sessions', browser, 'lock', 'sessions', task_keys),
                ('webview2_instances', hybrid, '_lock', 'instances', task_keys),
                ('search_sessions', self.searches, 'lock', 'sessions', keys),
            ):
                if manager is None:
                    counters[label] = 0
                    continue
                lock = getattr(manager, lock_name)
                if not lock.acquire(blocking=False):
                    stale = True
                    continue
                try:
                    counters[label] = sum(item.owner in owners for item in getattr(manager, collection).values())
                finally:
                    lock.release()
        self._usage_cache = counters
        return {**metadata, **counters, 'stale': stale,
                'operation_results': self.operations.result_policy()}

    def call_tool(self, name, arguments, *, context=None):
        if name not in self.catalog:
            raise JsonRpcError(-32602, "Unknown tool")
        journaled = False

        def recovery_guidance():
            if journaled:
                return 'Query OperationStatus with the original request_id; do not replay.'
            if name.startswith('Browser') and arguments.get('request_id'):
                return 'Read server_info health and query BrowserActionStatus for the original browser action.'
            return 'Read server_info health, then obtain a fresh observation; do not infer that a prior write failed.'

        try:
            self.validators[name].validate(arguments)
            args = dict(arguments)
            if name == "server_info":
                info = self.server_info_payload()
                browser = getattr(self.bf_server, "_bf_browser_manager", None)
                hybrid = getattr(self.bf_server, "_bf_hybrid_manager", None)
                if self.config.full_control:
                    info["exec_policy"]["secret_env_filter"] = "enabled"
                return tool_result({**info, "ok": True, "server": "unified-personal-mcp",
                    "title": "Unified Personal MCP", "version": __version__,
                    "device_label": self.config.device_label, "permission_mode": self.config.permission_mode,
                    "filesystem_scope": "current_windows_user" if self.config.full_control else "workflow_project",
                    "absolute_paths_allowed": self.config.full_control,
                    "health": self.health_snapshot(), "catalog_revision": self.catalog_revision,
                    "resource_usage": self.resource_usage(),
                    "concurrency": {"project_limit": None, "commands_per_workflow": 16,
                        "browser_sessions": browser.max_sessions if browser else 0,
                        "webview2_instances": hybrid.max_managed if hybrid else 0,
                        "search_sessions": self.searches.max_sessions,
                        "search_sessions_per_workflow": self.searches.max_per_owner,
                        "foreground_input": 1, "workflow_idle_seconds": self.config.workflow_idle_seconds},
                    "working_directory": "Set exec_command.workdir per call; other workflows are unchanged."})
            if name == "UnifiedTask":
                action = args.pop("action")
                if action == "begin":
                    result = self.registry.begin(self.principal, args["project_path"], args["request_id"],
                                                 args.get("access", "write"), args.get("ttl", 120))
                elif action == "list":
                    result = self.registry.list(self.principal)
                elif action == "release_idle":
                    result = self.registry.release_idle(self.principal, args["workflow_ref"])
                elif action == 'resume':
                    result = self.registry.resume(self.principal, args['workflow_ref'],
                                                  args['request_id'], args['expected_generation'])
                else:
                    result = getattr(self.registry, action)(self.principal, args["workflow_id"])
                    if action == 'status' and result.get('state') == 'ACTIVE':
                        with self.registry.use(self.principal, args['workflow_id'], control=True, touch=False) as resource:
                            result['commands'] = resource.coding.command_inventory()
                            result['job_processes'] = resource.coding.job_process_snapshot()
                return tool_result(result)
            token = args.pop("workflow_id")
            write = name != "SearchSession" and not self.catalog[name]["annotations"].get("readOnlyHint", False)
            control = (name in {'OperationStatus', 'kill_command', 'read_output'}
                       or (name == 'write_stdin' and not args.get('chars', '')))
            with self.registry.use(self.principal, token, write=write, control=control) as resource:
                if write and not control:
                    # The client wait may have ended while native work remains.
                    # Check only after obtaining the workflow's write gate and
                    # before journaling or dispatching any additional mutation.
                    try:
                        self.bf.drain(resource.bf_token, timeout=0)
                    except TimeoutError:
                        raise WorkflowError('WORKFLOW_WRITE_PENDING', {'next_action':
                            'Observe the original operation and server_info health. For journaled writes, '
                            'query OperationStatus with the original request_id; for browser actions, '
                            'use BrowserActionStatus. Wait for the pending operation or native cleanup '
                            'to finish before submitting another write. '
                            'Status, command output polling and command cancellation remain available.'}) from None
                if name == 'OperationStatus':
                    return tool_result(self.operations.status(resource.key, args['request_id']))
                if name in TOOL_REGISTRY:
                    if write:
                        request_id = args.pop("request_id")
                        journaled = True
                        return self.operations.run(resource.key, request_id, name, args,
                            lambda: resource.coding.call_tool(name, args, context=context))
                    return resource.coding.call_tool(name, args, context=context)
                if name == "SearchSession":
                    action = args.pop("action")
                    if action == "start":
                        result = self.searches.start(resource.key, resource.coding.workspace.root,
                            args["pattern"], regex=args.get("regex", False), env=resource.coding._command_env({}))
                    elif action == "stop":
                        result = self.searches.stop(resource.key, args["search_id"])
                    elif action == "release":
                        result = self.searches.release(resource.key, args["search_id"])
                    else:
                        result = self.searches.read(resource.key, args["search_id"],
                                                    cursor=args.get("cursor", 0), limit=args.get("limit", 100))
                    return tool_result({"ok": True, **result})
                if write and not name.startswith("Browser"):
                    request_id = args.pop("request_id")
                    journaled = True
                    return self.operations.run(resource.key, request_id, name, args,
                        lambda: self.bf.submit(name, {"bf_task_id": resource.bf_token, **args}))
                return self.bf.call(name, {"bf_task_id": resource.bf_token, **args})
        except WorkflowError as exc:
            return tool_result({"ok": False, "error": {"code": exc.code, "details": exc.details}})
        except ValidationError:
            return tool_result({"ok": False, "error": {"code": "INVALID_ARGUMENT"}})
        except (ValueError, PermissionError, FileNotFoundError) as exc:
            code = str(exc)
            allowed = {'REQUEST_CONFLICT', 'OPERATION_NOT_FOUND', 'RESULT_EXPIRED', 'OPERATION_CAPACITY'}
            return tool_result({"ok": False, "error": {"code": code if code in allowed else "REQUEST_REFUSED"}})
        except TimeoutError as exc:
            code = 'OPERATION_RUNNING' if str(exc) == 'OPERATION_RUNNING' else 'OUTCOME_UNKNOWN'
            return tool_result({"ok": False, "error": {"code": code,
                'next_action': recovery_guidance()}})
        except RuntimeError as exc:
            code = str(exc)
            allowed = {'BF_EXECUTOR_CLOSED', 'BF_RECOVERING', 'BF_CONNECTION_UNAVAILABLE',
                       'BF_CALL_OUTCOME_UNKNOWN', 'BF_OPERATIONS_STILL_RUNNING'}
            return tool_result({'ok': False, 'error': {
                'code': code if code in allowed else 'EXECUTION_ERROR',
                'next_action': recovery_guidance()}})
        except Exception:
            return tool_result({"ok": False, "error": {"code": "EXECUTION_ERROR", "outcome": "unknown"}})

    def close(self):
        if self._closed:
            return
        self.registry.close()
        self.searches.close()
        self.bf.close()
        manager = getattr(self.bf_server, "_bf_browser_manager", None)
        if manager:
            manager.shutdown()
        super().close()
