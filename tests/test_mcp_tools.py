"""The MCP layer, driven by a real MCP client in-process, with a fake bridge behind it.

Proves: tools are advertised with the right schemas and annotations, arguments
reach the bridge unchanged, results come back as structured content, and every
bridge failure becomes an `is_error` result whose text tells the model what went
wrong. It proves nothing about Revit.
"""

from __future__ import annotations

import json

import pytest
from mcp import Client

from revit_mcp import server
from revit_mcp.server import mcp
from tests.fakebridge import FakeError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def scripted_bridge(fake_bridge, client_for):
    """Start a fake bridge whose handler is `script(method, params)` and point the server at it."""

    def start(script):
        b = fake_bridge(script)
        server.set_client(client_for(b))
        return b

    yield start
    server.set_client(None)


EXPECTED_TOOLS = {
    "revit_status",
    "get_project_info",
    "list_levels",
    "list_categories",
    "list_elements",
    "get_element",
    "set_parameter",
    "delete_elements",
    "create_wall",
    "create_level",
    "create_sheet",
    "list_views",
    "list_sheets",
    "execute_python",
}


async def test_tool_surface_and_annotations():
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    for t in tools.values():
        assert t.description and len(t.description) > 40, f"{t.name} needs a real description"
        assert t.annotations is not None, f"{t.name} lacks annotations"
        assert t.output_schema is not None, f"{t.name} should declare structured output"
    assert tools["get_element"].annotations.read_only_hint is True
    assert tools["delete_elements"].annotations.destructive_hint is True
    assert tools["set_parameter"].annotations.read_only_hint is False
    schema = tools["get_element"].input_schema
    assert schema["required"] == ["element_id"]
    assert schema["properties"]["element_id"]["type"] == "integer"
    wall = tools["create_wall"].input_schema
    assert set(wall["required"]) == {"start", "end", "level_id"}
    assert wall["properties"]["height"]["default"] == 10.0


async def test_arguments_reach_bridge_and_result_is_structured(scripted_bridge):
    b = scripted_bridge(lambda m, p: {"id": p["element_id"], "category": "Walls"})
    async with Client(mcp) as c:
        r = await c.call_tool("get_element", {"element_id": 4242, "include_type_parameters": True})
    assert r.is_error is False
    assert r.structured_content == {"id": 4242, "category": "Walls"}
    assert json.loads(r.content[0].text) == {"id": 4242, "category": "Walls"}
    assert b.requests[-1] == {
        "v": 1,
        "id": b.requests[-1]["id"],
        "method": "get_element",
        "params": {"element_id": 4242, "include_type_parameters": True},
        "timeout": 5.0,
    }


async def test_defaults_are_sent_explicitly(scripted_bridge):
    b = scripted_bridge(lambda m, p: {"elements": [], "total": 0})
    async with Client(mcp) as c:
        await c.call_tool("list_elements", {"category": "Doors"})
    assert b.requests[-1]["params"] == {
        "category": "Doors",
        "limit": 100,
        "offset": 0,
        "element_types": False,
    }


async def test_set_parameter_accepts_string_number_and_bool(scripted_bridge):
    b = scripted_bridge(lambda m, p: {"ok": True})
    async with Client(mcp) as c:
        for value in ["1 Hour", 3.5, 7, True]:
            r = await c.call_tool("set_parameter", {"element_id": 1, "parameter_name": "Comments", "value": value})
            assert r.is_error is False
    assert [r["params"]["value"] for r in b.requests] == ["1 Hour", 3.5, 7, True]


async def test_bridge_error_becomes_is_error_with_code_message_and_details(scripted_bridge):
    def script(m, p):
        raise FakeError("not_found", "No element with id 99", "Traceback (most recent call last):\n  ...")

    scripted_bridge(script)
    async with Client(mcp) as c:
        r = await c.call_tool("get_element", {"element_id": 99})
    assert r.is_error is True
    text = r.content[0].text
    assert "[not_found]" in text and "No element with id 99" in text and "Traceback" in text


async def test_unreachable_bridge_is_error_with_start_instructions(scripted_bridge):
    b = scripted_bridge(lambda m, p: {})
    b.close()  # nothing is listening on that port any more
    async with Client(mcp) as c:
        r = await c.call_tool("revit_status", {})
    assert r.is_error is True
    assert "Start Bridge" in r.content[0].text


async def test_invalid_arguments_are_rejected_before_reaching_the_bridge(scripted_bridge):
    b = scripted_bridge(lambda m, p: {})
    async with Client(mcp) as c:
        r = await c.call_tool("get_element", {"element_id": "not-an-int"})
    assert r.is_error is True
    assert b.requests == []


async def test_execute_python_passes_timeout_and_transaction_flag(scripted_bridge):
    b = scripted_bridge(lambda m, p: {"result": 3, "stdout": ""})
    async with Client(mcp) as c:
        r = await c.call_tool("execute_python", {"code": "result = 1 + 2", "transaction": False})
    assert r.structured_content == {"result": 3, "stdout": ""}
    assert b.requests[-1]["params"] == {"code": "result = 1 + 2", "transaction": False}
