"""Contract regressions: declarations must match execution, not just remove UI warnings."""

import asyncio
import base64
import copy
import hashlib
import importlib
import io

import pytest
from fastmcp import Client
from fastmcp.tools.base import ToolResult
from jsonschema import Draft202012Validator
from mcp.types import ImageContent, TextContent
from PIL import Image

from personal_mcp.bf.bootstrap import build_mcp

TOKEN = "bf_" + "a" * 32
WINDOW = "bfw_" + "b" * 24
ELEMENT = "bfe_" + "c" * 20


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("catalog")
    server = build_mcp(state_root=tmp_path / "state", allowed_root=tmp_path_factory.getbasetemp(),
                       apps_dir=tmp_path / "apps")
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    return server, tools


def test_every_tool_has_a_concrete_output_schema(catalog):
    _, tools = catalog
    assert len(tools) == 21
    for name, tool in tools.items():
        assert tool.output_schema, f"{name} has no outputSchema"
        Draft202012Validator.check_schema(tool.output_schema)
        assert tool.output_schema != {"type": "object", "additionalProperties": True}, name


@pytest.mark.parametrize("name,args", [
    ("App", {"mode": "resize", "window_size": [200]}),
    ("App", {"mode": "resize", "window_size": [0, 200]}),
    ("App", {"mode": "launch_executable"}),
    ("Click", {"loc": [1, 2, 3]}),
    ("Click", {"loc": [1, 2], "clicks": 3}),
    ("Move", {"loc": "[1]"}),
    ("Wait", {"duration": -1}),
    ("WaitFor", {"condition": "unsupported", "text": "fixture"}),
    ("WaitFor", {"condition": "text_exists", "text": "fixture", "timeout": 121}),
    ("WaitFor", {"condition": "text_exists", "text": "fixture", "interval": 0}),
    ("WindowSnapshot", {"window_id": WINDOW, "max_elements": 201}),
    ("WindowControl", {"window_id": WINDOW, "action": "click", "loc": [1]}),
    ("LaunchApplication", {"application_id": "sample", "wait_seconds": -1}),
    ("LaunchApplication", {"application_id": "sample", "wait_seconds": 61}),
    ("task_context", {"action": "begin"}),
    ("task_context", {"action": "status", "bf_task_id": None}),
])
def test_invalid_inputs_are_rejected_by_the_published_schema(catalog, name, args):
    _, tools = catalog
    payload = {"bf_task_id": TOKEN, **args}
    schema = tools[name].parameters
    assert not Draft202012Validator(schema).is_valid(payload), (name, payload)


@pytest.mark.parametrize("name,args", [
    ("Click", {"loc": [-120, 2], "clicks": 0}),
    ("Move", {"loc": "[-120, 2]"}),
    ("App", {"mode": "resize", "name": "Fixture", "window_size": [300, 200]}),
    ("Wait", {"duration": 0}),
    ("WaitFor", {"condition": "text", "text": "fixture", "timeout": 120, "interval": 5}),
    ("WindowSnapshot", {"window_id": WINDOW, "max_elements": 200}),
    ("WindowControl", {"window_id": WINDOW, "action": "set_value", "element_id": ELEMENT, "text": ""}),
    ("LaunchApplication", {"application_id": "sample", "wait_seconds": 0}),
    ("task_context", {"action": "begin", "project_path": "D:/run/sample"}),
])
def test_valid_boundary_and_legacy_inputs_remain_accepted(catalog, name, args):
    _, tools = catalog
    Draft202012Validator(tools[name].parameters).validate({"bf_task_id": TOKEN, **args})


def test_descriptions_and_annotations_are_not_misleading(catalog):
    _, tools = catalog
    assert "Always call this first" not in tools["Snapshot"].description
    assert "WindowSnapshot" in tools["Snapshot"].description
    assert tools["Scroll"].annotations.model_dump(by_alias=True)["idempotentHint"] is False
    assert tools["WindowControl"].annotations.model_dump(by_alias=True)["destructiveHint"] is True
    assert "does not" in tools["Wait"].description.lower()


def test_invalid_runtime_call_does_not_reach_the_window_adapter(tmp_path):
    calls = []

    class Controller:
        def snapshot(self, *a, **kw):
            calls.append(kw)
            raise AssertionError("invalid input reached the device adapter")

    server = build_mcp(state_root=tmp_path / "state", allowed_root=tmp_path,
                       apps_dir=tmp_path / "apps", controller_factory=Controller)

    async def run():
        async with Client(server) as client:
            started = await client.call_tool("task_context", {"project_path": str(tmp_path)})
            token = started.structured_content["bf_task_id"]
            result = await client.call_tool("WindowSnapshot", {
                "bf_task_id": token, "window_id": WINDOW, "max_elements": 201,
            }, raise_on_error=False)
            assert result.is_error is True
            assert result.structured_content["error"]["code"] == "INVALID_ARGUMENT"
            assert token not in str(result.structured_content)
            await client.call_tool("task_context", {"action": "end", "bf_task_id": token})

    asyncio.run(run())
    assert calls == []


def test_actual_task_and_wait_results_conform_to_declared_schemas(catalog, tmp_path):
    server, tools = catalog

    async def run():
        async with Client(server) as client:
            result = await client.call_tool("task_context", {"project_path": str(tmp_path)})
            token = result.structured_content["bf_task_id"]
            Draft202012Validator(tools["task_context"].output_schema).validate(result.structured_content)
            for name, extra in [("task_context", {"action": "status"}), ("task_diagnostics", {}),
                                ("prepare_browser_session", {"browser": "edge"}), ("Wait", {"duration": 0})]:
                value = await client.call_tool(name, {"bf_task_id": token, **extra})
                Draft202012Validator(tools[name].output_schema).validate(value.structured_content)
            ended = await client.call_tool("task_context", {"action": "end", "bf_task_id": token})
            Draft202012Validator(tools["task_context"].output_schema).validate(ended.structured_content)

    asyncio.run(run())


def contracts():
    return importlib.import_module("bf_automation.tool_contracts")


def test_screenshot_normalization_preserves_image_and_declares_only_observed_metadata():
    module = contracts()
    data = io.BytesIO()
    Image.new("RGB", (23, 17)).save(data, format="PNG")
    png = data.getvalue()
    image = ImageContent(type="image", mimeType="image/png", data=base64.b64encode(png).decode())
    raw = ToolResult(content=[TextContent(type="text", text="fixture screenshot"), image])
    original = copy.deepcopy(raw)
    result = module.normalize_upstream_result("Screenshot", raw)
    Draft202012Validator(module.output_schema_for("Screenshot")).validate(result.structured_content)
    assert [c.data for c in result.content if c.type == "image"] == [image.data]
    observed = result.structured_content["images"][0]
    assert (observed["width"], observed["height"]) == (23, 17)
    assert observed["sha256"] == hashlib.sha256(png).hexdigest()
    assert result.structured_content["verified"] is False
    assert raw == original


def test_upstream_capture_failure_becomes_error_not_successful_empty_capture():
    module = contracts()
    raw = ToolResult(content=[TextContent(type="text", text='["Error capturing screenshot: screen grab failed. Please try again."]')])
    result = module.normalize_upstream_result("Screenshot", raw)
    assert result.is_error is True
    assert result.structured_content["status"] == "error"
    Draft202012Validator(module.output_schema_for("Screenshot")).validate(result.structured_content)


def test_an_action_result_is_not_business_verification():
    module = contracts()
    result = module.normalize_upstream_result("Shortcut", ToolResult(content="Pressed ctrl+s."))
    assert result.structured_content["verified"] is False
    assert result.structured_content["verification_required"] is True


def test_new_object_result_removes_stale_upstream_unwrap_metadata():
    module = contracts()
    raw = ToolResult(structured_content={"result": "Waited for 0 seconds."},
                     meta={"fastmcp": {"wrap_result": True}, "trace": "kept"})
    result = module.normalize_upstream_result("Wait", raw)
    assert not (result.meta or {}).get("fastmcp", {}).get("wrap_result")
    assert result.meta["trace"] == "kept"
    assert raw.meta["fastmcp"]["wrap_result"] is True


def test_output_validator_catches_bad_adapter_data(catalog):
    _, tools = catalog
    bad = {"count": "one", "windows": []}
    assert not Draft202012Validator(tools["WindowInventory"].output_schema).is_valid(bad)


def test_omitted_default_action_has_the_same_contract_as_explicit_begin(catalog):
    _, tools = catalog
    validator = Draft202012Validator(tools["task_context"].parameters)
    validator.validate({"project_path": "D:/run/sample"})
    assert not validator.is_valid({})
    assert not validator.is_valid({"action": "status", "project_path": "D:/run/sample"})


def test_omitted_app_mode_defaults_to_name_launch_not_executable_launch(catalog):
    _, tools = catalog
    validator = Draft202012Validator(tools["App"].parameters)
    validator.validate({"bf_task_id": TOKEN, "name": "Calculator"})
    assert not validator.is_valid({"bf_task_id": TOKEN, "executable": "C:/sample.exe"})


def test_malformed_native_output_is_rejected_at_execution_boundary(tmp_path):
    class Controller:
        def inventory(self, *a, **k):
            return {"count": "one", "windows": []}

    server = build_mcp(state_root=tmp_path / "state", allowed_root=tmp_path,
                       apps_dir=tmp_path / "apps", controller_factory=Controller)

    async def run():
        async with Client(server) as client:
            task = (await client.call_tool("task_context", {"project_path": str(tmp_path)})).structured_content
            result = await client.call_tool("WindowInventory", {"bf_task_id": task["bf_task_id"]}, raise_on_error=False)
            assert result.is_error
            assert result.structured_content["error"]["code"] == "RESULT_CONTRACT_VIOLATION"
            assert result.structured_content["error"]["retry_safe"] is False
            await client.call_tool("task_context", {"action": "end", "bf_task_id": task["bf_task_id"]})

    asyncio.run(run())
