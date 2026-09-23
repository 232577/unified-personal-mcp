"""Instance-local unified tool schemas; upstream declarations stay unchanged."""

import asyncio
import hashlib
import json
from copy import deepcopy

from coding_tools_mcp.server import TOOL_REGISTRY, tool_definition

WORKFLOW = {"type": "string", "pattern": "^wf_[A-Za-z0-9_-]{43}$"}
WORKFLOW_REF = {"type": "string", "pattern": "^wfr_[0-9a-f]{64}$"}
TEXT = {"type": "string", "minLength": 1, "maxLength": 128}


def catalog_revision(catalog):
    fields = ('name', 'inputSchema', 'outputSchema', 'annotations')
    contract = [{key: catalog[name].get(key) for key in fields} for name in sorted(catalog)]
    return hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def definition(name, description, properties, required, output, *, readonly=False):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required,
                            "additionalProperties": False},
            "outputSchema": output,
            "annotations": {"readOnlyHint": readonly, "destructiveHint": not readonly,
                            "idempotentHint": False, "openWorldHint": False}}


def unified_catalog(bf_server, *, full_control=False, search_sessions=4):
    tools = {name: deepcopy(tool_definition(name)) for name in TOOL_REGISTRY}
    for tool in asyncio.run(bf_server.list_tools()):
        if tool.name in {"task_context", "task_diagnostics"}:
            continue
        schema = deepcopy(tool.parameters)
        schema["properties"].pop("bf_task_id", None)
        schema["required"] = [p for p in schema.get("required", []) if p != "bf_task_id"]
        tools[tool.name] = {"name": tool.name,
            "description": (tool.description or "").replace("bf_task_id", "workflow_id"),
            "inputSchema": schema, "outputSchema": deepcopy(tool.output_schema),
            "annotations": tool.annotations.model_dump(by_alias=True, exclude_none=True)}
    for name, tool in tools.items():
        if name == "App":
            tool["description"] = "Switch to or resize an existing application. Use LaunchApplication with a registered profile to start an owned application."
            mode = tool["inputSchema"]["properties"]["mode"]
            mode["enum"] = ["switch", "resize"]
            mode.pop("default", None)
            tool["inputSchema"]["required"] = list(dict.fromkeys([*tool["inputSchema"].get("required", []), "mode"]))
        if name == "server_info":
            continue
        schema = tool["inputSchema"]
        schema["properties"]["workflow_id"] = deepcopy(WORKFLOW)
        schema["required"] = list(dict.fromkeys([*schema.get("required", []), "workflow_id"]))
        schema["additionalProperties"] = False
        tool["description"] = "Requires an active UnifiedTask workflow. " + tool["description"]
        if full_control and name in TOOL_REGISTRY:
            tool["description"] += (
                " This installation is owner-authorized for full local control: absolute paths and paths "
                "outside the default project are permitted with the Windows user's access. "
                "For shared build tools, keep the target project workdir and pass the tool's absolute path. "
                "Set workdir explicitly on each command; it does not change other workflows.")
        if not tool["annotations"].get("readOnlyHint", False) and not name.startswith("Browser"):
            schema["properties"]["request_id"] = deepcopy(TEXT)
            schema["required"] = list(dict.fromkeys([*schema["required"], "request_id"]))
            tool["description"] += " Supply a unique request_id for this operation; repeats never replay an unknown outcome."
            if name == "write_stdin":
                tool["description"] += (
                    " Each new output poll needs a new request_id, even when chars is empty; "
                    "reuse the same ID only to retry a lost reply to that exact poll.")
    task_output = {"type": "object", "required": ["ok"], "properties": {
        "ok": {"type": "boolean"}, "state": {"type": "string"}, "code": {"type": "string"},
        "workflow_id": WORKFLOW, "project": {"type": "string"}, "access": {"enum": ["read", "write"]},
        "workflow_ref": WORKFLOW_REF, "created": {"type": "number"},
        "credential_generation": {"type": "integer"}, "commands": {"type": "array"},
        "job_processes": {"type": "object"},
        "last_activity": {"type": "number"}, "idle_seconds": {"type": "number"},
        "workflows": {"type": "array", "items": {"type": "object"}},
        "expires": {"type": "number"}, "instructions": {"type": "string"}, "error": {"type": "object"}}}
    task = definition("UnifiedTask",
        "Begin a project workflow, keep its one-time workflow_id, then activate it. End releases owned resources. "
        "Different projects run concurrently. Use the concrete project directory, not a shared parent; "
        "call shared build scripts by absolute path while keeping that project. For simultaneous edits to one "
        "project, use separate working copies. list shows your workflow_refs and project owners without tokens. "
        "release_idle accepts a workflow_ref only after the configured idle period and only when no operation "
        "or owned resource is running. Busy or unknown resources are retained. Idle sessions are also reaped "
        "automatically. A repeated begin never returns the secret again; if its first reply was lost, "
        "wait for reservation expiry and use a new request_id. To explicitly continue the SAME ACTIVE "
        "workflow after losing its token, list first then resume with workflow_ref, credential_generation "
        "as expected_generation and a unique request_id. This replaces the old credential without restarting "
        "owned commands. Never automatically resume another task on PROJECT_BUSY. Retry a lost resume reply "
        "with exactly the same request_id and parameters. status lists retained command handles and "
        "diagnostic-only owned Job PIDs. A command root may have exited while its descendants still run; "
        "commands=0 does not mean idle. If the owner explicitly wants the entire workflow stopped, "
        "resume the SAME workflow when needed, inspect status, then end it. Do not kill arbitrary PIDs "
        "or restart the host to resolve one workflow. Unknown diagnostics do not prove idle.",
        {"action": {"enum": ["begin", "activate", "status", "end", "list", "release_idle", "resume"]},
         "workflow_id": WORKFLOW, "workflow_ref": WORKFLOW_REF,
         "expected_generation": {"type": "integer", "minimum": 0},
         "project_path": {"type": "string", "minLength": 1}, "request_id": TEXT,
         "access": {"enum": ["read", "write"], "default": "write"},
         "ttl": {"type": "integer", "minimum": 1, "maximum": 300, "default": 120}}, ["action"], task_output)
    properties = task["inputSchema"]["properties"]
    task["inputSchema"]["oneOf"] = []
    for actions, required, allowed in (
        (["begin"], ["project_path", "request_id"], {"action", "project_path", "request_id", "access", "ttl"}),
        (["activate", "status", "end"], ["workflow_id"], {"action", "workflow_id"}),
        (["list"], [], {"action"}),
        (["release_idle"], ["workflow_ref"], {"action", "workflow_ref"}),
        (["resume"], ["workflow_ref", "request_id", "expected_generation"],
         {"action", "workflow_ref", "request_id", "expected_generation"}),
    ):
        # Union-aware clients generate each branch's callable signature without
        # merging the parent properties. Keep every branch self-contained.
        branch = {key: deepcopy(value) for key, value in properties.items() if key in allowed}
        branch["action"] = {"enum": actions}
        task["inputSchema"]["oneOf"].append({"type": "object", "properties": branch,
            "required": ["action", *required], "additionalProperties": False})
    tools["UnifiedTask"] = task
    tools['OperationStatus'] = definition('OperationStatus',
        'Query a previously submitted request_id in this workflow without repeating its side effects. '
        'running means only the client wait ended; poll this tool. unknown must be observed, never replayed. '
        'Completed results have bounded retention; an expired result still prevents replay. '
        'Browser actions retain their existing BrowserActionStatus query.',
        {'workflow_id': WORKFLOW, 'request_id': TEXT}, ['workflow_id', 'request_id'],
        {'type': 'object', 'required': ['ok'], 'properties': {
            'ok': {'type': 'boolean'}, 'state': {'enum': ['running', 'completed', 'failed', 'unknown']},
            'request_id': TEXT, 'operation': {'type': 'string'}, 'updated': {'type': 'number'},
            'result': {}, 'result_ref': {'type': ['string', 'null']},
            'result_expired': {'type': 'boolean'}, 'error': {'type': 'object'}}}, readonly=True)
    search_output = {"type": "object", "required": ["ok"], "properties": {
        "ok": {"type": "boolean"}, "search_id": {"type": "string"}, "state": {"type": "string"},
        "reason": {"type": ["string", "null"]}, "next_cursor": {"type": "integer"},
        "has_more": {"type": "boolean"}, "results": {"type": "array", "items": {"type": "object",
            "properties": {"path": {"type": "string"}, "line": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["path", "line", "text"]}}, "error": {"type": "object"}}}
    search = definition("SearchSession", "Start/read/status/stop/release a bounded progressive search in this workflow's project. "
        f"Use nonnegative absolute cursors. At most 2 retained sessions per workflow and {search_sessions} per host. "
        "stop retains results; release stops and forgets one search, freeing its slot without ending the workflow.",
        {"action": {"enum": ["start", "read", "status", "stop", "release"]}, "workflow_id": WORKFLOW,
         "pattern": {"type": "string", "minLength": 1, "maxLength": 1024}, "regex": {"type": "boolean"},
         "search_id": TEXT, "cursor": {"type": "integer", "minimum": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": 1000}}, ["action", "workflow_id"], search_output)
    search["inputSchema"]["allOf"] = [{"if": {"properties": {"action": {"const": "start"}}},
        "then": {"required": ["pattern"]}, "else": {"required": ["search_id"]}}]
    tools["SearchSession"] = search
    return tools
