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
from .catalog import unified_catalog
from .coding import LocalTelemetry, build_coding
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
        super().__init__(config.workspace_root, auth_token=auth_token, permission_mode=config.permission_mode,
                         project_context=context, transport="http")
        self.telemetry = LocalTelemetry()
        self.catalog = unified_catalog(bf_server)
        self.validators = {k: Draft202012Validator(v["inputSchema"]) for k, v in self.catalog.items()}
        self._exposed_tool_names = list(self.catalog)
        self._exposed_tool_name_set = frozenset(self.catalog)
        self.searches = SearchManager(rg=rg_path)
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

    def call_tool(self, name, arguments, *, context=None):
        if name not in self.catalog:
            raise JsonRpcError(-32602, "Unknown tool")
        try:
            self.validators[name].validate(arguments)
            args = dict(arguments)
            if name == "server_info":
                return tool_result({**self.server_info_payload(), "ok": True, "device_label": self.config.device_label})
            if name == "UnifiedTask":
                action = args.pop("action")
                if action == "begin":
                    result = self.registry.begin(self.principal, args["project_path"], args["request_id"],
                                                 args.get("access", "write"), args.get("ttl", 120))
                else:
                    result = getattr(self.registry, action)(self.principal, args["workflow_id"])
                return tool_result(result)
            token = args.pop("workflow_id")
            write = name != "SearchSession" and not self.catalog[name]["annotations"].get("readOnlyHint", False)
            with self.registry.use(self.principal, token, write=write) as resource:
                if name in TOOL_REGISTRY:
                    if write:
                        request_id = args.pop("request_id")
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
                    return self.operations.run(resource.key, request_id, name, args,
                        lambda: self.bf.call(name, {"bf_task_id": resource.bf_token, **args}))
                return self.bf.call(name, {"bf_task_id": resource.bf_token, **args})
        except WorkflowError as exc:
            return tool_result({"ok": False, "error": {"code": exc.code}})
        except ValidationError:
            return tool_result({"ok": False, "error": {"code": "INVALID_ARGUMENT"}})
        except (ValueError, PermissionError, FileNotFoundError):
            return tool_result({"ok": False, "error": {"code": "REQUEST_REFUSED"}})
        except TimeoutError:
            return tool_result({"ok": False, "error": {"code": "OUTCOME_UNKNOWN"}})
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
