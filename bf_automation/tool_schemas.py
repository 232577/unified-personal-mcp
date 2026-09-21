"""Public BF contracts, independent of GUI imports and transport internals.

The same JSON Schemas are published and executed by tool_contracts.py. Existing
success keys are retained; errors have a separate, explicit envelope.
"""

from __future__ import annotations

from copy import deepcopy

CONTRACT_VERSION = "2026-09-17.1"
STR = {"type": "string"}
BOOL = {"type": "boolean"}
INT = {"type": "integer"}
NONEMPTY = {"type": "string", "minLength": 1, "pattern": r"\S"}
TOKEN = {"type": "string", "pattern": "^bf_[0-9a-f]{32}$", "minLength": 35, "maxLength": 35}
WINDOW_ID = {"type": "string", "pattern": "^bfw_[0-9a-f]{24}$"}
ELEMENT_ID = {"type": "string", "pattern": "^bfe_[0-9a-f]{20}$"}
TASK_KEY = {"type": "string", "pattern": "^[0-9a-f]{16}$"}
POSITIVE_INT = {"type": "integer", "minimum": 1}
NONNEG_INT = {"type": "integer", "minimum": 0}
HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}


def obj(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else list(required),
            "additionalProperties": False}


def array(items, **limits):
    return {"type": "array", "items": deepcopy(items), **limits}


def nullable(schema):
    return {"anyOf": [deepcopy(schema), {"type": "null"}]}


def enum(*values):
    return {"type": "string", "enum": list(values)}


RECT = nullable(array(INT, minItems=4, maxItems=4))
POINT = array(INT, minItems=2, maxItems=2)
WINDOW = obj({
    "window_id": WINDOW_ID, "pid": POSITIVE_INT, "title": STR, "class_name": STR,
    "visible": BOOL, "minimized": BOOL, "window_status": enum("normal", "minimized", "hidden"),
    "rect": RECT,
})
CAPTURE = obj({
    "window_id": WINDOW_ID, "pid": POSITIVE_INT, "title": STR,
    "backend": enum("wgc_d3d11", "staging_wgc_d3d11", "print_window"),
    "capture_mode": enum("wgc_d3d11", "staging_wgc_d3d11", "print_window"),
    "width": POSITIVE_INT, "height": POSITIVE_INT, "staging_used": BOOL,
    "path": NONEMPTY, "sha256": HASH, "fallback_chain": array(STR),
}, required=["window_id", "pid", "title", "backend", "capture_mode", "width", "height",
             "staging_used", "path", "sha256"])
ELEMENT = obj({
    "element_id": ELEMENT_ID, "name": STR, "automation_id": STR, "class_name": STR,
    "control_type": STR, "native_handle": nullable(POSITIVE_INT), "native_text": STR,
    "enabled": BOOL, "focusable": BOOL, "rect": RECT, "depth": NONNEG_INT,
})
SNAPSHOT = obj({
    "window_id": WINDOW_ID, "pid": POSITIVE_INT, "title": STR,
    "elements": array(ELEMENT, maxItems=200), "element_count": {"type": "integer", "minimum": 0, "maximum": 200},
    "capture": CAPTURE,
}, required=["window_id", "pid", "title", "elements", "element_count"])
TASK_FIELDS = {
    "status": enum("active"), "task_key": TASK_KEY, "task_directory": NONEMPTY,
    "project_path": NONEMPTY, "owned_pids": array(POSITIVE_INT), "owned_searches": array(STR),
}
TASK_BEGIN = obj({k: v for k, v in TASK_FIELDS.items() if k not in {"owned_pids", "owned_searches"}}
                 | {"bf_task_id": TOKEN})
TASK_STATUS = obj(TASK_FIELDS)
TASK_END = obj({"status": enum("ended"), "task_key": TASK_KEY})
ERROR = obj({"error": obj({
    "code": NONEMPTY, "message": STR, "retry_safe": BOOL,
})})
IMAGE_INFO = obj({"mime_type": NONEMPTY, "width": POSITIVE_INT, "height": POSITIVE_INT, "sha256": HASH})
DISPLAY_RECT = obj({name: INT for name in ("left", "top", "right", "bottom", "width", "height")})
DISPLAY = obj({
    "index": NONNEG_INT, "device": STR, "primary": BOOL, "bounds": nullable(DISPLAY_RECT),
    "work_area": nullable(DISPLAY_RECT), "resolution": STR, "orientation": nullable(STR),
    "effective_dpi": nullable({"type": "number"}), "scale": nullable({"type": "number"}),
})

NATIVE_OUTPUTS = {
    "task_context": {"type": "object", "oneOf": [TASK_BEGIN, TASK_STATUS, TASK_END]},
    "task_diagnostics": obj(TASK_FIELDS | {
        "desktop_lease": enum("available", "owned_by_task", "owned_by_other_task"),
        "lease_age_seconds": {"type": "number", "minimum": 0}, "raw_task_id_exposed": {"const": False},
    }),
    "prepare_browser_session": obj({
        "browser": enum("edge", "chrome"), "task_key": TASK_KEY, "profile_path": NONEMPTY,
        "launch_arguments": array(STR), "note": STR,
    }),
    "WindowInventory": obj({"count": NONNEG_INT, "windows": array(WINDOW)}),
    "WindowScreenshot": obj({"window_capture": CAPTURE}),
    "WindowSnapshot": obj({"window_snapshot": SNAPSHOT}),
    "WindowControl": obj({
        "window_id": WINDOW_ID, "accepted": BOOL, "mode": enum(
            "background_refused", "uia_invoke", "win32_message", "win32_command", "win32_value", "uia_value",
            "uia_toggle", "uia_select", "uia_scroll", "foreground_fallback"),
        "verified": BOOL, "verification_required": BOOL, "foreground_fallback_allowed": BOOL,
        "reason": STR, "foreground_preserved": BOOL, "foreground_restored": BOOL, "placement_restored": BOOL,
    }, required=["window_id", "accepted", "mode", "verified", "foreground_fallback_allowed"]),
    "LaunchApplication": obj({
        "application_id": NONEMPTY, "status": enum("existing_window", "window_ready", "exited_without_window", "window_timeout"),
        "pid": POSITIVE_INT, "owned_pids": array(POSITIVE_INT), "reused_existing": BOOL,
        "window": nullable(WINDOW), "window_detected": BOOL, "note": STR, "exit_code": nullable(INT),
        "instance_directory": NONEMPTY, "logs": obj({"stdout": NONEMPTY, "stderr": NONEMPTY}),
    }, required=["application_id", "status", "pid", "owned_pids", "reused_existing", "window", "window_detected"]),
}
UPSTREAM_NAMES = {"App", "Click", "Clipboard", "DisplayInventory", "Move", "Notification",
                  "Screenshot", "Scroll", "Shortcut", "Snapshot", "Type", "Wait", "WaitFor"}
WRAPPED_STRING_NAMES = {"Click", "Clipboard", "Move", "Notification", "Scroll", "Type", "Wait", "WaitFor"}


def output_schema_for(name, *, browser_transfers_enabled=False):
    if name.startswith('Browser'):
        from .browser.schemas import output_schema
        return output_schema(name, transfers_enabled=browser_transfers_enabled)
    if name in NATIVE_OUTPUTS:
        success = deepcopy(NATIVE_OUTPUTS[name])
    elif name in UPSTREAM_NAMES:
        fields = {
            "tool": {"const": name}, "status": enum("returned", "error"), "text": STR,
            "verified": {"const": False}, "verification_required": BOOL, "images": array(IMAGE_INFO),
        }
        required = list(fields)
        if name in WRAPPED_STRING_NAMES:
            fields["result"] = STR  # Keep the upstream's existing result key.
        elif name == "DisplayInventory":
            fields["result"] = array(DISPLAY)
        success = obj(fields, required)
    else:
        raise ValueError(f"unknown BF tool: {name}")
    return {"type": "object", "anyOf": [success, deepcopy(ERROR)],
            "description": "BF contract " + CONTRACT_VERSION + ". Errors use isError=true and the error branch. "
                           "returned/accepted does not by itself verify the application's outcome."}


def _non_null_field(name, schema=None):
    return {"required": [name], "properties": {name: deepcopy(schema or NONEMPTY)}}


def _when(field, values, then):
    return {"if": {"required": [field], "properties": {field: {"enum": values}}}, "then": then}


def input_schema_for(name, original):
    if name.startswith('Browser'):
        from .browser.schemas import INPUTS
        return deepcopy(INPUTS[name])
    schema = deepcopy(original)
    props = schema.setdefault("properties", {})
    rules = schema.setdefault("allOf", [])

    def replace(key, value):
        previous = props.get(key, {})
        props[key] = deepcopy(value)
        if "default" in previous:
            props[key]["default"] = previous["default"]

    if "bf_task_id" in props:
        replace("bf_task_id", nullable(TOKEN) if name == "task_context" else TOKEN)
        props["bf_task_id"]["description"] = "Task lease returned by task_context(begin); reuse it for this workflow only."
    for key, value in [("window_id", WINDOW_ID), ("element_id", nullable(ELEMENT_ID)),
                       ("process_id", nullable(POSITIVE_INT)), ("label", nullable(NONNEG_INT))]:
        if key in props:
            replace(key, value)

    for key in ("loc", "from_loc", "window_loc", "window_size"):
        if key not in props:
            continue
        is_size = key == "window_size"
        pair = array(POSITIVE_INT if is_size else INT, minItems=2, maxItems=2)
        branches = [pair, {"type": "null"}]
        if any(b.get("type") == "string" for b in props[key].get("anyOf", [])):
            branches.insert(1, {"type": "string", "pattern": r"^\s*\[\s*-?\d+\s*,\s*-?\d+\s*\]\s*$",
                                "description": "Legacy JSON pair. Prefer the two-integer array."})
        replace(key, {"anyOf": branches})
        props[key]["description"] = (
            "[width,height], both positive" if is_size else
            "[x,y] in window-client pixels" if name == "WindowControl" else
            "[x,y] in physical desktop pixels; negative coordinates are allowed")

    for key in ("use_vision", "use_dom", "use_annotation", "use_ui_tree", "drag", "clear", "press_enter"):
        if key in props and any(b.get("type") == "string" for b in props[key].get("anyOf", [])):
            replace(key, {"anyOf": [BOOL, {"type": "string", "pattern": r"^\s*([Tt][Rr][Uu][Ee]|[Ff][Aa][Ll][Ss][Ee])\s*$"}]})
    if "display" in props:
        replace("display", nullable(array(NONNEG_INT, minItems=1, uniqueItems=True)))
    for key in ("width_reference_line", "height_reference_line"):
        if key in props:
            replace(key, nullable(POSITIVE_INT))
    if "shortcut" in props:
        replace("shortcut", nullable(NONEMPTY) if name == "WindowControl" else NONEMPTY)
    if name == "WindowScreenshot":
        replace("include_frame", {"type": "boolean", "const": False,
                                 "description": "Reserved compatibility parameter; only false is supported. The backend determines the surface."})
    if name == "WindowSnapshot":
        replace("max_elements", {"type": "integer", "minimum": 1, "maximum": 200})
    if name == "LaunchApplication":
        replace("wait_seconds", {"type": "number", "minimum": 0, "maximum": 60})
        replace("application_id", {"type": "string", "pattern": "^[a-z0-9_-]+$", "minLength": 1})
    if name == "Wait":
        replace("duration", {"type": "integer", "minimum": 0, "maximum": 120,
                             "description": "Seconds to wait, not proof the application is ready."})
    if name == "Click":
        replace("clicks", {"type": "integer", "enum": [0, 1, 2]})
    if name in {"Click", "Move"}:
        rules.append({"anyOf": [_non_null_field("loc", {"not": {"type": "null"}}),
                                _non_null_field("label", NONNEG_INT)]})
    if name in {"Click", "Move", "Scroll", "Type"}:
        rules.append({"not": {"allOf": [_non_null_field("loc", {"not": {"type": "null"}}),
                                         _non_null_field("label", NONNEG_INT)]}})
    if name == "Move":
        replace("duration", nullable({"anyOf": [
            {"type": "number", "minimum": 0, "maximum": 10},
            {"type": "string", "pattern": r"^\s*\d+(\.\d+)?\s*$", "description": "Legacy seconds, 0 to 10; prefer a number."}]}))
    if name == "Scroll":
        replace("wheel_times", {"type": "integer", "minimum": 1, "maximum": 100})
        rules += [_when("type", ["vertical"], {"properties": {"direction": {"enum": ["up", "down"]}}}),
                  _when("type", ["horizontal"], {"properties": {"direction": {"enum": ["left", "right"]}}})]
    if name == "WaitFor":
        replace("condition", enum("text_exists", "active_window", "element_exists", "element_enabled", "focused_element",
                                  "text", "window", "element", "enabled", "focused"))
        replace("timeout", {"type": "number", "exclusiveMinimum": 0, "maximum": 120})
        replace("interval", {"type": "number", "exclusiveMinimum": 0, "maximum": 5})
        rules += [_when("condition", ["text", "text_exists"], _non_null_field("text")),
                  _when("condition", ["window", "active_window", "element", "element_exists", "enabled", "element_enabled"],
                        {"anyOf": [_non_null_field("text"), _non_null_field("window_name")]})]
    if name == "Clipboard":
        rules.append(_when("mode", ["set"], _non_null_field("text", STR)))
    if name == "App":
        rules += [_when("mode", ["launch", "switch"], _non_null_field("name")),
                  _when("mode", ["launch_executable"], _non_null_field("executable"))]
    if name == "task_context":
        rules += [_when("action", ["begin"], _non_null_field("project_path")),
                  _when("action", ["status", "end"], _non_null_field("bf_task_id", TOKEN))]
    if name == "WindowControl":
        rules += [_when("action", ["invoke", "toggle", "select", "scroll_into_view"], _non_null_field("element_id", ELEMENT_ID)),
                  _when("action", ["shortcut"], _non_null_field("shortcut")),
                  _when("action", ["click"], {"anyOf": [_non_null_field("element_id", ELEMENT_ID), _non_null_field("loc", POINT)]})]
    # JSON Schema defaults are annotations, not mutation. Describe the omitted
    # field branch explicitly so discovery and execution agree on defaults.
    for rule in rules:
        condition = rule.get("if", {})
        for field, condition_schema in condition.get("properties", {}).items():
            default = props.get(field, {}).get("default")
            if "default" in props.get(field, {}) and default in condition_schema.get("enum", []):
                rule["if"] = {"anyOf": [condition, {"not": {"required": [field]}}]}
    if not rules:
        schema.pop("allOf", None)
    schema["additionalProperties"] = False
    return schema


DESCRIPTIONS = {
    "App": "Explicit application launch or foreground window management. Prefer LaunchApplication for a registered project; never open documents/source files by association. launch/switch require name; launch_executable requires executable and separate args. resize uses a name or the current foreground window. Use WindowInventory and WindowSnapshot first for application work. Results report dispatch, not application readiness.",
    "Click": "Foreground pointer action at desktop loc=[x,y] or a label from a fresh Snapshot, not both. clicks=0 hovers, 1 clicks, 2 double-clicks. Prefer WindowControl for background application operations. Repetition may activate twice; inspect the result before retrying.",
    "Clipboard": "Read (get) or replace (set) the shared Windows clipboard. set requires text, including an empty string to clear. Requires the foreground-desktop lease; do not use it as task-private storage.",
    "DisplayInventory": "Read display indices, physical desktop bounds, work areas, DPI and scale. Does not operate an application or acquire the foreground-desktop lease.",
    "Move": "Move/drag the real pointer using loc or a fresh Snapshot label. Prefer WindowControl for background operations. from_loc and duration require drag=true; duration is 0..10 seconds. This is a foreground fallback, not hidden operation.",
    "Notification": "Send an explicitly requested Windows toast with title, message and application app_id. Does not acquire the foreground-desktop lease; sending twice may notify twice.",
    "Screenshot": "Capture the whole desktop without collecting its UI tree. For a specific application prefer WindowInventory then WindowScreenshot/WindowSnapshot. Returns image content plus text and measured image metadata; do not infer desktop coordinates from a scaled image without checking the text. No claim of application success.",
    "Snapshot": "Inspect the whole desktop and optionally its UI tree/DOM and image. For a specific application prefer WindowInventory then WindowSnapshot; this is not the default first step for every task. use_vision controls image content, use_ui_tree controls tree extraction, and display contains nonnegative display indices. Legacy labels apply only to this fresh foreground snapshot.",
    "Scroll": "Relative foreground scroll at loc, a fresh Snapshot label, or the current pointer. Vertical uses up/down; horizontal uses left/right; wheel_times is 1..100. Each repeated call scrolls again: not idempotent. Prefer window-targeted operations for applications.",
    "Shortcut": "Send a keyboard combination such as ctrl+s to the current foreground window. This may save, submit, close or switch windows and is not a background action. Confirm the target first; execution does not verify the intended business outcome.",
    "Type": "Type into a confirmed foreground control using loc, a fresh Snapshot label, or current focus. clear replaces existing text; press_enter may submit. Prefer WindowControl set_value for background controls and verify the resulting state. Do not enter unapproved credentials.",
    "Wait": "Wait 0..120 seconds only. Waiting does not prove an application is ready. Does not acquire the foreground-desktop lease; use a state observation to verify readiness.",
    "WaitFor": "Poll a foreground UI condition: text_exists, active_window, element_exists, element_enabled, focused_element (short aliases retained). timeout is (0,120], interval (0,5]. text_exists requires text; window/element conditions require text or window_name. Uses the shared desktop lease; for background windows observe WindowSnapshot instead.",
    "task_context": "Begin, inspect or end a BF workflow. begin (default) requires project_path within the configured workspace and returns bf_task_id; status/end require that same bf_task_id. End releases ownership but does not close user applications. Use separate contexts for separate conversations.",
    "task_diagnostics": "Read current task state, recorded PIDs and foreground lease ownership. No raw task token is returned. Recorded PIDs are not proof the processes are still alive.",
    "prepare_browser_session": "Prepare task-private Edge/Chrome profile directories and launch arguments. Does not start a browser or prove it is ready; use the returned profile only for this task.",
    "WindowInventory": "Primary application discovery: enumerate top-level windows and obtain task-scoped window_id values. Returns visibility/minimization and physical screen bounds. This does not claim exclusive write ownership. Follow with WindowSnapshot/WindowScreenshot; never reuse another task's IDs.",
    "WindowScreenshot": "Capture a bound window and return an actual MCP image plus typed capture metadata. For minimized windows, allow_staging=true permits temporary off-screen staging/restoration under the shared lease. Otherwise no foreground activation. include_frame is reserved and supports only false. A captured image is not a business-outcome assertion.",
    "WindowSnapshot": "Primary application observation: return up to max_elements (1..200) UI Automation elements for a task-bound window and, if include_image=true, actual image content with capture metadata. element_id values require a fresh snapshot. Minimized image capture may stage only when allow_staging=true.",
    "WindowControl": "Primary background application control. Use a fresh element_id for semantic actions or client-relative loc for click. The first writer claims the actual window; another task is refused. Foreground fallback is opt-in. accepted means dispatch; verified means an observed value check, not general business success. Read WindowSnapshot after actions; never blindly retry an unknown outcome.",
    "LaunchApplication": "Launch a registered application with explicit executable/argv and shell=false, or attach an exact executable's existing window when its profile allows reuse. Returns owned PIDs only for newly launched instances, startup logs and window status. wait_seconds is 0..60. Reusing an existing program does not give permission to kill it. Follow with WindowSnapshot to verify the real page.",
}
