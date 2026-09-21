"""End-to-end: a real MCP client, the real `revit-mcp` server as a subprocess, and a real
Revit session with the bridge running. Reads, writes, creates, verifies, and cleans up.

Skipped unless REVIT_MCP_E2E=1. Every run writes a JSON transcript of every tool call and
its result to docs/verification/ (path in REVIT_MCP_E2E_REPORT, default is timestamped),
which is the evidence the README points at.

What it asserts, in order:
  1. revit_status: bridge reachable, a document is open, Revit version reported
  2. get_project_info / list_levels / list_categories / list_views / list_sheets: sane, non-empty
  3. list_elements(Walls) + get_element on one wall: parameters present, location is a curve
  4. set_parameter(Comments) on that wall, read back through get_element, restore it
  5. create_level, create_wall on it, create_sheet: ids come back, get_element/list_* see them
  6. the created wall is visible from a *second* server process (state lives in Revit, not here)
  7. execute_python: reads the wall through the raw API and returns its length
  8. delete_elements: cleanup, and get_element on the deleted wall is a not_found error
  9. error paths: unknown category, bad element id, read-only parameter
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, StdioServerParameters

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("REVIT_MCP_E2E") != "1", reason="set REVIT_MCP_E2E=1 with Revit + bridge running"
    ),
    pytest.mark.anyio,
]

REPO = Path(__file__).resolve().parents[2]
STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
REPORT = Path(os.environ.get("REVIT_MCP_E2E_REPORT") or REPO / "docs" / "verification" / f"e2e-{STAMP}.json")
TAG = f"revit-mcp e2e {STAMP}"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def server_params() -> StdioServerParameters:
    env = {k: v for k, v in os.environ.items() if k.startswith("REVIT_MCP_") or k in ("PATH", "SYSTEMROOT")}
    return StdioServerParameters(command=sys.executable, args=["-m", "revit_mcp", "-v"], env=env)


class Recorder:
    """Calls tools through the MCP client and keeps a transcript for the evidence file."""

    def __init__(self, client: Client):
        self.client = client
        self.calls: list[dict[str, Any]] = []

    async def call(self, name: str, args: dict[str, Any] | None = None, *, expect_error: bool = False) -> Any:
        started = time.perf_counter()
        result = await self.client.call_tool(name, args or {})
        elapsed = round(time.perf_counter() - started, 3)
        text = result.content[0].text if result.content else ""
        entry = {"tool": name, "arguments": args or {}, "seconds": elapsed, "is_error": result.is_error}
        if result.is_error:
            entry["error"] = text
        else:
            entry["result"] = result.structured_content if result.structured_content is not None else text
        self.calls.append(entry)
        if expect_error:
            assert result.is_error, f"{name} should have failed, got: {text[:300]}"
            return text
        assert not result.is_error, f"{name} failed: {text[:2000]}"
        return result.structured_content


async def test_live_revit_round_trip():
    async with Client(server_params()) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        rec = Recorder(client)
        report: dict[str, Any] = {"started": STAMP, "tools_advertised": sorted(tools), "calls": rec.calls}
        try:
            await _run(rec, report)
        finally:
            report["finished"] = datetime.now(timezone.utc).isoformat()
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
            print(f"\nevidence written to {REPORT}")


async def _run(rec: Recorder, report: dict[str, Any]) -> None:
    # 1. session
    status = await rec.call("revit_status")
    assert status["document"] is not None, "open a project in Revit before running the e2e"
    assert status["revit"]["version_number"]
    report["revit"] = status["revit"]
    report["document"] = status["document"]
    report["engine"] = status["engine"]

    # 2. reads
    info = await rec.call("get_project_info")
    assert info["title"] == status["document"]["title"]
    levels = (await rec.call("list_levels"))["levels"]
    assert levels and levels == sorted(levels, key=lambda lv: lv["elevation"])
    cats = (await rec.call("list_categories", {"with_counts": True}))["categories"]
    assert any(c["built_in"] == "OST_Walls" for c in cats)
    views = (await rec.call("list_views"))["views"]
    assert views and all(v["view_type"] != "DrawingSheet" for v in views)
    sheets_before = (await rec.call("list_sheets"))["sheets"]

    # 3. one wall in detail
    walls = await rec.call("list_elements", {"category": "Walls", "limit": 5})
    assert walls["total"] > 0 and walls["elements"]
    wall_id = walls["elements"][0]["id"]
    wall = await rec.call("get_element", {"element_id": wall_id, "include_type_parameters": True})
    assert wall["id"] == wall_id and wall["category"] == "Walls"
    assert wall["location"] and wall["location"]["kind"] == "curve"
    names = {p["name"] for p in wall["parameters"]}
    assert "Comments" in names and wall["type_parameters"]

    # 4. write a parameter, read it back, restore
    before = next(p for p in wall["parameters"] if p["name"] == "Comments")["value"]
    set_result = await rec.call("set_parameter", {"element_id": wall_id, "parameter_name": "Comments", "value": TAG})
    assert set_result["after"]["value"] == TAG
    again = await rec.call("get_element", {"element_id": wall_id})
    assert next(p for p in again["parameters"] if p["name"] == "Comments")["value"] == TAG
    await rec.call("set_parameter", {"element_id": wall_id, "parameter_name": "Comments", "value": before or ""})

    # 5. create things
    top = levels[-1]["elevation"]
    level = await rec.call("create_level", {"name": f"MCP {STAMP}", "elevation": top + 12.0})
    assert level["id"] > 0 and any(lv["id"] == level["id"] for lv in (await rec.call("list_levels"))["levels"])
    wall_count_before = (await rec.call("list_elements", {"category": "Walls", "limit": 1}))["total"]
    new_wall = await rec.call(
        "create_wall",
        {"start": [0, 0, top + 12.0], "end": [20, 0, top + 12.0], "level_id": level["id"], "height": 9.5},
    )
    assert new_wall["category"] == "Walls" and abs(new_wall["location"]["length"] - 20.0) < 1e-6
    assert abs(new_wall["height"] - 9.5) < 1e-6
    assert (await rec.call("list_elements", {"category": "Walls", "limit": 1}))["total"] == wall_count_before + 1
    fetched = await rec.call("get_element", {"element_id": new_wall["id"]})
    assert fetched["level_id"] == level["id"]
    sheet = await rec.call("create_sheet", {"sheet_number": f"MCP-{STAMP[-6:]}", "sheet_name": "revit-mcp e2e"})
    sheets_after = (await rec.call("list_sheets"))["sheets"]
    assert len(sheets_after) == len(sheets_before) + 1 and any(s["id"] == sheet["id"] for s in sheets_after)

    # 6. the wall exists outside this server process
    async with Client(server_params()) as other:
        seen = await other.call_tool("get_element", {"element_id": new_wall["id"]})
        assert not seen.is_error and seen.structured_content["id"] == new_wall["id"]
        rec.calls.append(
            {
                "tool": "get_element (from a second revit-mcp process)",
                "arguments": {"element_id": new_wall["id"]},
                "is_error": False,
                "result": {"id": seen.structured_content["id"], "level_id": seen.structured_content["level_id"]},
            }
        )

    # 7. raw API through execute_python
    code = "\n".join(
        [
            f"wall = doc.GetElement(ElementId({new_wall['id']}))",
            "print('type: ' + type(wall).__name__)",
            "result = {'length': wall.Location.Curve.Length, 'level': doc.GetElement(wall.LevelId).Name}",
        ]
    )
    py = await rec.call("execute_python", {"code": code, "transaction": False})
    assert abs(py["result"]["length"] - 20.0) < 1e-6 and py["result"]["level"] == level["name"]
    assert "type: Wall" in py["stdout"]
    bad = await rec.call("execute_python", {"code": "raise ValueError('boom')"}, expect_error=True)
    assert "ValueError" in bad and "boom" in bad

    # 8. cleanup, and confirm it is gone
    deleted = await rec.call("delete_elements", {"element_ids": [new_wall["id"], sheet["id"], level["id"]]})
    assert {new_wall["id"], sheet["id"], level["id"]} <= set(deleted["deleted_ids"])
    gone = await rec.call("get_element", {"element_id": new_wall["id"]}, expect_error=True)
    assert "not_found" in gone
    assert (await rec.call("list_elements", {"category": "Walls", "limit": 1}))["total"] == wall_count_before

    # 9. error paths
    assert "not_found" in await rec.call("list_elements", {"category": "Unicorns"}, expect_error=True)
    assert "not_found" in await rec.call("get_element", {"element_id": 1}, expect_error=True)
    ro = await rec.call(
        "set_parameter", {"element_id": wall_id, "parameter_name": "Area", "value": 1}, expect_error=True
    )
    assert "read-only" in ro
