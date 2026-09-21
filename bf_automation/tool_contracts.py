"""Enforce the public input/output contracts at every BF MCP tool boundary."""

from __future__ import annotations

import asyncio
import base64
import io
import json
from copy import deepcopy
from hashlib import sha256

from fastmcp.tools.base import ToolResult
from fastmcp.tools.tool_transform import TransformedTool, forward
from jsonschema import Draft202012Validator
from mcp.types import TextContent
from PIL import Image

from .tool_schemas import (
    CONTRACT_VERSION,
    DESCRIPTIONS,
    UPSTREAM_NAMES,
    WRAPPED_STRING_NAMES,
    input_schema_for,
    output_schema_for,
)

ACTION_TOOLS = {"App", "Click", "Clipboard", "Move", "Notification", "Scroll", "Shortcut", "Type"}


def _error(code, message, *, retry_safe=False):
    # Never echo input values (task leases, typed credentials, etc.) in errors.
    return ToolResult(structured_content={"error": {
        "code": code, "message": message, "retry_safe": retry_safe,
    }}, is_error=True)


def normalize_upstream_result(name, raw):
    text = "\n".join(block.text for block in raw.content if block.type == "text")
    images = []
    for block in raw.content:
        if block.type != "image":
            continue
        decoded = base64.b64decode(block.data, validate=True)
        with Image.open(io.BytesIO(decoded)) as image:
            width, height = image.size
        images.append({"mime_type": block.mime_type, "width": width, "height": height,
                       "sha256": sha256(decoded).hexdigest()})
    # The pinned screenshot tools return an ordinary string list on capture
    # failure. Match only their known prefix, not arbitrary application text.
    message = text.strip()
    if message.startswith("["):
        try:
            decoded_text = json.loads(message)
            if isinstance(decoded_text, list) and len(decoded_text) == 1 and isinstance(decoded_text[0], str):
                message = decoded_text[0]
        except ValueError:
            pass
    capture_error = name in {"Screenshot", "Snapshot"} and message.startswith(
        ("Error capturing screenshot:", "Error capturing desktop state:"))
    failed = bool(raw.is_error or capture_error)
    payload = {"tool": name, "status": "error" if failed else "returned", "text": text,
               "verified": False, "verification_required": name in ACTION_TOOLS, "images": images}
    if name in WRAPPED_STRING_NAMES and raw.structured_content is not None:
        payload["result"] = raw.structured_content.get("result", text)
    elif name == "DisplayInventory" and raw.structured_content is not None:
        payload["result"] = raw.structured_content["result"]
    content = [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]
    content.extend(block for block in raw.content if block.type != "text")
    meta = deepcopy(raw.meta)
    if meta and isinstance(meta.get("fastmcp"), dict):
        # This is now an object envelope, not the old scalar/list result
        # wrapper. Leaving wrap_result set makes SDK clients unwrap the wrong
        # value before parsing the new schema.
        meta["fastmcp"].pop("wrap_result", None)
    return ToolResult(content=content, structured_content=payload, meta=meta, is_error=failed)


def _normalize_legacy(arguments):
    normalized = dict(arguments)
    for key in ("loc", "from_loc", "args"):
        if isinstance(normalized.get(key), str):
            try:
                value = json.loads(normalized[key])
            except ValueError:
                raise ValueError(f"{key} must be an array or a JSON-encoded array") from None
            if not isinstance(value, list):
                raise ValueError(f"{key} must be an array")
            normalized[key] = value
    for key in ("use_vision", "use_dom", "use_annotation", "use_ui_tree", "drag", "clear", "press_enter"):
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = value.strip().casefold() == "true"
    if isinstance(normalized.get("duration"), str):
        normalized["duration"] = float(normalized["duration"])
    return normalized


def _validate_input(name, arguments, validator):
    # JSON Schema alone does not reject NaN in all Python validators.
    try:
        json.dumps(arguments, allow_nan=False)
    except (ValueError, TypeError):
        raise ValueError("arguments must be finite JSON values") from None
    errors = list(validator.iter_errors(arguments))
    if errors:
        error = errors[0]
        location = ".".join(str(p) for p in error.absolute_path) or "arguments"
        raise ValueError(f"{name}: {location} violates {error.validator}; check the published input schema")
    normalized = _normalize_legacy(arguments)
    errors = list(validator.iter_errors(normalized))
    if errors:
        error = errors[0]
        location = ".".join(str(p) for p in error.absolute_path) or "arguments"
        raise ValueError(f"{name}: normalized {location} violates {error.validator}")
    if name == "Move" and not normalized.get("drag") and any(normalized.get(k) is not None for k in ("from_loc", "duration")):
        raise ValueError("from_loc and duration require drag=true")
    return normalized


def _governed_tool(parent, *, browser_transfers_enabled=False):
    name = parent.name
    input_schema = input_schema_for(name, parent.parameters)
    output_schema = output_schema_for(name, browser_transfers_enabled=browser_transfers_enabled)
    Draft202012Validator.check_schema(input_schema)
    Draft202012Validator.check_schema(output_schema)
    input_validator = Draft202012Validator(input_schema)
    output_validator = Draft202012Validator(output_schema)

    async def governed(**kwargs):
        try:
            arguments = _validate_input(name, kwargs, input_validator)
        except ValueError as exc:
            return _error("INVALID_ARGUMENT", str(exc), retry_safe=True)
        try:
            raw = await forward(**arguments)
        except PermissionError:
            return _error("ACCESS_DENIED", "Task/window ownership was refused. Inspect the task context; do not borrow another task's IDs.")
        except (ValueError, KeyError):
            return _error("INVALID_ARGUMENT", f"{name}: invalid arguments or unknown application. Recheck the profile and input contract; do not blindly retry.")
        except LookupError:
            return _error("STALE_TARGET", "The window or element is stale. Refresh WindowInventory/WindowSnapshot before any action.")
        except TimeoutError:
            return _error("TIMEOUT", f"{name}: timed out. Observe current state before retrying; a dispatched action may already have run.")
        except Exception as exc:
            # A provider can fail after dispatch. Do not perform another action
            # or promise a safe retry, and do not leak provider/input secrets.
            return _error("EXECUTION_ERROR", f"{name}: {type(exc).__name__}; outcome unknown. Inspect current state before retrying.")
        try:
            result = normalize_upstream_result(name, raw) if name in UPSTREAM_NAMES else raw
            output_validator.validate(result.structured_content)
            json.dumps(result.structured_content, allow_nan=False)
        except Exception:
            return _error("RESULT_CONTRACT_VIOLATION", f"{name}: the adapter returned invalid structured output. An action may have run; inspect before retrying.")
        return result

    annotations = parent.annotations.model_copy(deep=True) if parent.annotations is not None else None
    if name == "Scroll" and annotations is not None:
        annotations = type(annotations).model_validate({
            **annotations.model_dump(by_alias=True), "idempotentHint": False,
        })
    transformed = TransformedTool.from_tool(
        parent, name=name, transform_fn=governed, description=DESCRIPTIONS.get(name, parent.description),
        annotations=annotations, output_schema=output_schema,
        meta={**(parent.meta or {}), "bf_contract_version": ('2026-09-17.U2.2-candidate'
              if name in {'BrowserUpload', 'BrowserDownload'} else '2026-09-17.U2-candidate'
              if name in {'BrowserAction', 'BrowserWaitFor'} else
              '2026-09-17.U1' if name.startswith('Browser') else CONTRACT_VERSION)},
    )
    # FastMCP uses parameters for discovery/default injection, but transformed
    # **kwargs functions need the explicit runtime validator above as well.
    transformed.parameters = deepcopy(input_schema)
    return transformed


def apply_tool_contracts(mcp):
    tools = asyncio.run(mcp.list_tools())
    manager = getattr(mcp, '_bf_browser_manager', None)
    transfers_enabled = bool(manager is not None and getattr(manager, 'transfers_enabled', False))
    for parent in tools:
        transformed = _governed_tool(parent, browser_transfers_enabled=transfers_enabled)
        mcp._local_provider.remove_tool(parent.name)  # Pinned FastMCP 4.0.3.
        mcp.add_tool(transformed)
