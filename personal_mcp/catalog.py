"""Instance-local unified tool schemas; upstream declarations stay unchanged."""

import asyncio
from copy import deepcopy

from coding_tools_mcp.server import TOOL_REGISTRY, tool_definition

WORKFLOW = {"type": "string", "pattern": "^wf_[A-Za-z0-9_-]{43}$"}
TEXT = {"type": "string", "minLength": 1, "maxLength": 128}


def definition(name, description, properties, required, output, *, readonly=False):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": required,
                            "additionalProperties": False},
            "outputSchema": output,
            "annotations": {"readOnlyHint": readonly, "destructiveHint": not readonly,
                            "idempotentHint": False, "openWorldHint": False}}


def unified_catalog(bf_server, *, full_control=False):
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
    task_output = {"type": "object", "required": ["ok"], "properties": {
        "ok": {"type": "boolean"}, "state": {"type": "string"}, "code": {"type": "string"},
        "workflow_id": WORKFLOW, "project": {"type": "string"}, "access": {"enum": ["read", "write"]},
        "expires": {"type": "number"}, "instructions": {"type": "string"}, "error": {"type": "object"}}}
    task = definition("UnifiedTask",
        "Begin a project workflow, keep its one-time workflow_id, then activate it. End releases owned resources. "
        "A repeated begin never returns the secret again; if its first reply was lost, wait for reservation expiry and use a new request_id.",
        {"action": {"enum": ["begin", "activate", "status", "end"]}, "workflow_id": WORKFLOW,
         "project_path": {"type": "string", "minLength": 1}, "request_id": TEXT,
         "access": {"enum": ["read", "write"], "default": "write"},
         "ttl": {"type": "integer", "minimum": 1, "maximum": 300, "default": 120}}, ["action"], task_output)
    task["inputSchema"]["allOf"] = [{"if": {"properties": {"action": {"const": "begin"}}},
        "then": {"required": ["project_path", "request_id"], "not": {"required": ["workflow_id"]}},
        "else": {"required": ["workflow_id"], "not": {"anyOf": [{"required": [x]} for x in ("project_path", "request_id", "access", "ttl")]}}}]
    tools["UnifiedTask"] = task
    search_output = {"type": "object", "required": ["ok"], "properties": {
        "ok": {"type": "boolean"}, "search_id": {"type": "string"}, "state": {"type": "string"},
        "reason": {"type": ["string", "null"]}, "next_cursor": {"type": "integer"},
        "has_more": {"type": "boolean"}, "results": {"type": "array", "items": {"type": "object",
            "properties": {"path": {"type": "string"}, "line": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["path", "line", "text"]}}, "error": {"type": "object"}}}
    search = definition("SearchSession", "Start/read/status/stop/release a bounded progressive search in this workflow's project. "
        "Use nonnegative absolute cursors. At most 2 retained sessions per workflow and 4 per host. "
        "stop retains results; release stops and forgets one search, freeing its slot without ending the workflow.",
        {"action": {"enum": ["start", "read", "status", "stop", "release"]}, "workflow_id": WORKFLOW,
         "pattern": {"type": "string", "minLength": 1, "maxLength": 1024}, "regex": {"type": "boolean"},
         "search_id": TEXT, "cursor": {"type": "integer", "minimum": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": 1000}}, ["action", "workflow_id"], search_output)
    search["inputSchema"]["allOf"] = [{"if": {"properties": {"action": {"const": "start"}}},
        "then": {"required": ["pattern"]}, "else": {"required": ["search_id"]}}]
    tools["SearchSession"] = search
    return tools
