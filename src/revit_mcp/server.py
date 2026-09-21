"""The MCP server: tools an LLM can call, each proxied to the bridge inside Revit.

Every tool is a thin, typed wrapper around one bridge command. The tool
docstrings are what the model reads, so they say what Revit actually does
(units, ids, transactions) rather than what one might wish it did.

Units: the Revit API works in internal units. Lengths are decimal feet,
angles are radians, regardless of the project's display units. Tools that
take or return numbers say so; ``value_string`` fields carry the
display-formatted value for humans.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .client import (
    BridgeError,
    ProtocolError,
    RevitClient,
    RevitConnectionError,
    RevitTimeoutError,
    Settings,
)

log = logging.getLogger("revit_mcp")

INSTRUCTIONS = """\
Tools for reading and modifying the Autodesk Revit model that is currently open
in a running Revit session (via the RevitMCP pyRevit bridge).

Start with `revit_status` to confirm the bridge is reachable and see which
document is open. Element ids are integers and are stable for the life of the
document. Lengths are in decimal feet (Revit's internal unit); use the
`value_string` fields when you need the project's display units.

Every modifying tool runs inside its own Revit transaction, so a failure rolls
back completely and shows up in Revit's Undo history as one step when it
succeeds. `execute_python` runs arbitrary IronPython against the Revit API and
is the escape hatch for anything the typed tools do not cover.
"""

mcp = MCPServer(
    name="revit",
    version=__version__,
    instructions=INSTRUCTIONS,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False)

_client: RevitClient | None = None


def get_client() -> RevitClient:
    global _client
    if _client is None:
        _client = RevitClient(Settings.from_env())
    return _client


def set_client(client: RevitClient | None) -> None:
    """Swap the bridge client (tests point it at a fake bridge)."""
    global _client
    _client = client


def bridge(method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
    """Call the bridge and translate every failure into a ToolError the model can act on."""
    try:
        return get_client().call(method, params, timeout=timeout)
    except BridgeError as exc:
        text = f"Revit bridge error [{exc.code}]: {exc.message}"
        if exc.details:
            text += f"\n{exc.details.rstrip()}"
        raise ToolError(text) from exc
    except (RevitConnectionError, RevitTimeoutError, ProtocolError) as exc:
        raise ToolError(str(exc)) from exc


# ----------------------------------------------------------------------------- status & project


@mcp.tool(annotations=READ_ONLY)
def revit_status() -> dict[str, Any]:
    """Check that Revit and the bridge are reachable and describe the session.

    Returns the Revit version/build, the bridge and protocol versions, the
    IronPython engine, and the active document (title, path, whether it is a
    family or workshared) or null when no document is open.
    """
    return bridge("hello")


@mcp.tool(annotations=READ_ONLY)
def get_project_info() -> dict[str, Any]:
    """Read the open document's Project Information (name, number, client, address,
    status, author, organisation, issue date...) plus title, path and length unit.
    """
    return bridge("get_project_info")


@mcp.tool(annotations=READ_ONLY)
def list_levels() -> dict[str, Any]:
    """List the levels in the document: id, name and elevation in feet, lowest first."""
    return bridge("list_levels")


@mcp.tool(annotations=READ_ONLY)
def list_categories(with_counts: bool = False) -> dict[str, Any]:
    """List the model and annotation categories the document knows about.

    Returns each category's display name and its BuiltInCategory name (e.g.
    "Walls" / "OST_Walls"); either form is accepted by `list_elements`. With
    `with_counts=true` it also counts the element instances per category, which
    costs one query per category (a few seconds on a large model).
    """
    return bridge("list_categories", {"with_counts": with_counts}, timeout=300)


# ----------------------------------------------------------------------------- elements


@mcp.tool(annotations=READ_ONLY)
def list_elements(
    category: str,
    limit: int = 100,
    offset: int = 0,
    element_types: bool = False,
) -> dict[str, Any]:
    """List element instances of one category, e.g. "Walls", "Doors", "OST_Rooms".

    Returns the total count and a page of summaries (id, name, family, type,
    level). Set `element_types=true` to list the category's types (wall types,
    door families' types...) instead of placed instances.
    """
    return bridge(
        "list_elements",
        {"category": category, "limit": limit, "offset": offset, "element_types": element_types},
    )


@mcp.tool(annotations=READ_ONLY)
def get_element(element_id: int, include_type_parameters: bool = False) -> dict[str, Any]:
    """Describe one element in full: category, family, type, level, location
    (point or curve endpoints, feet), bounding box, and every instance parameter
    with its storage type, raw value, display string and read-only flag.
    `include_type_parameters=true` appends the parameters of the element's type.
    """
    return bridge("get_element", {"element_id": element_id, "include_type_parameters": include_type_parameters})


@mcp.tool(annotations=MUTATING)
def set_parameter(element_id: int, parameter_name: str, value: str | float | int | bool) -> dict[str, Any]:
    """Set one parameter on an element, inside a transaction.

    The value is coerced to the parameter's storage type: numbers go in as
    internal units (feet for lengths); a string given to a numeric parameter
    is parsed in the project's display units, so "3000 mm" or "10' 6\\"" work.
    Returns the value before and after. Fails on read-only parameters.
    """
    return bridge("set_parameter", {"element_id": element_id, "parameter_name": parameter_name, "value": value})


@mcp.tool(annotations=DESTRUCTIVE)
def delete_elements(element_ids: list[int]) -> dict[str, Any]:
    """Delete elements by id, in one transaction. Revit also deletes dependents
    (a wall's hosted doors, a level's views); the returned `deleted_ids` lists
    everything that actually went.
    """
    return bridge("delete_elements", {"element_ids": element_ids})


# ----------------------------------------------------------------------------- creation


@mcp.tool(annotations=MUTATING)
def create_wall(
    start: list[float],
    end: list[float],
    level_id: int,
    height: float = 10.0,
    wall_type_id: int | None = None,
    structural: bool = False,
) -> dict[str, Any]:
    """Create a straight wall between two points on a level.

    `start` and `end` are [x, y, z] in feet (z is normally the level's
    elevation; only x and y matter for the wall's line). `height` is in feet.
    `wall_type_id` defaults to the project's default wall type; find others
    with `list_elements("Walls", element_types=true)`. Returns the new wall.
    """
    return bridge(
        "create_wall",
        {
            "start": start,
            "end": end,
            "level_id": level_id,
            "height": height,
            "wall_type_id": wall_type_id,
            "structural": structural,
        },
    )


@mcp.tool(annotations=MUTATING)
def create_level(name: str, elevation: float) -> dict[str, Any]:
    """Create a level at `elevation` feet with the given unique name. Does not
    create plan views for it. Returns the new level.
    """
    return bridge("create_level", {"name": name, "elevation": elevation})


@mcp.tool(annotations=MUTATING)
def create_sheet(sheet_number: str, sheet_name: str, titleblock_type_id: int | None = None) -> dict[str, Any]:
    """Create a sheet with the given number (must be unique) and name.

    `titleblock_type_id` is the id of a title block *type* (see
    `list_elements("Title Blocks", element_types=true)`); omit it for a blank
    sheet. Returns the new sheet.
    """
    return bridge(
        "create_sheet",
        {"sheet_number": sheet_number, "sheet_name": sheet_name, "titleblock_type_id": titleblock_type_id},
    )


# ----------------------------------------------------------------------------- views & sheets


@mcp.tool(annotations=READ_ONLY)
def list_views(view_type: str | None = None, include_templates: bool = False) -> dict[str, Any]:
    """List the document's views (not sheets): id, name, view type, scale,
    whether it is a template, and its level for plans. Filter by `view_type`
    (e.g. "FloorPlan", "CeilingPlan", "Elevation", "Section", "ThreeD",
    "Schedule", "Legend").
    """
    return bridge("list_views", {"view_type": view_type, "include_templates": include_templates})


@mcp.tool(annotations=READ_ONLY)
def list_sheets() -> dict[str, Any]:
    """List the document's sheets: id, number, name and the ids of the views placed on each."""
    return bridge("list_sheets")


# ----------------------------------------------------------------------------- escape hatch


@mcp.tool(annotations=DESTRUCTIVE)
def execute_python(code: str, transaction: bool = True, timeout_seconds: float = 120) -> dict[str, Any]:
    """Run IronPython 2.7 code inside Revit with the Revit API in scope.

    Pre-bound names: `doc` (Document), `uidoc`, `app` (Application), `uiapp`,
    `DB` (Autodesk.Revit.DB), `UI` (Autodesk.Revit.UI), `clr`, and
    `FilteredElementCollector`, `Transaction`, `ElementId`, `XYZ`,
    `BuiltInCategory`, `BuiltInParameter` for convenience. `ElementId(n)`
    accepts a plain int (a wrapper resolves IronPython's overload ambiguity;
    use `DB.ElementId` for isinstance checks). Assign to `result` to return a
    value (ElementIds, XYZs, .NET lists and Elements are converted to JSON);
    anything printed comes back as `stdout`. Never open a TaskDialog or other
    modal UI: it blocks Revit's UI thread and every later call.

    With `transaction=true` (default) the code runs inside one Revit
    transaction that is committed on success and rolled back if the code
    raises, so you can modify the model without managing transactions. Use
    `transaction=false` for read-only code or when the code opens its own
    transactions or transaction groups. This is IronPython 2.7: no f-strings.
    """
    return bridge("execute_python", {"code": code, "transaction": transaction}, timeout=timeout_seconds)


# ----------------------------------------------------------------------------- entry point


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="revit-mcp",
        description="MCP server for Autodesk Revit (stdio). Connects to the RevitMCP bridge inside Revit.",
    )
    parser.add_argument("--host", help="bridge host (default: $REVIT_MCP_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="bridge port (default: $REVIT_MCP_PORT or 9877)")
    parser.add_argument("--timeout", type=float, help="per-call timeout in seconds (default: $REVIT_MCP_TIMEOUT or 60)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging on stderr")
    parser.add_argument("--version", action="version", version=f"revit-mcp {__version__}")
    args = parser.parse_args(argv)

    # stdout is the MCP transport; every log line must go to stderr.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    base = Settings.from_env()
    set_client(
        RevitClient(
            Settings(
                host=args.host or base.host,
                port=args.port or base.port,
                timeout=args.timeout or base.timeout,
            )
        )
    )
    s = get_client().settings
    log.info("revit-mcp %s: bridge at %s:%d, timeout %.0fs", __version__, s.host, s.port, s.timeout)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
