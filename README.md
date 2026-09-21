# revit-mcp

An MCP server that lets an LLM client (Claude Desktop, Claude Code, Cursor, any MCP client) read and
modify the model open in a running Autodesk Revit session. Written by Danylo Vyslotskyi
([danlion333](https://github.com/danlion333)); MIT licensed.

## How it works

```text
MCP client (Claude Desktop / Claude Code / Cursor)
      |
      |  stdio, MCP
      v
revit-mcp  (CPython 3.10+, this package)
      |
      |  TCP 127.0.0.1:9877, newline-delimited JSON
      v
RevitMCP bridge  (pyRevit extension, IronPython 2.7, inside Revit.exe)
      |
      |  ExternalEvent
      v
Revit API, on Revit's UI thread
```

Two processes, because two runtimes are unavoidable. Revit hosts .NET and, through pyRevit, an
IronPython 2.7 engine; that is the only place the Revit API exists. The MCP SDK is a CPython
package and cannot be imported into IronPython 2.7. So the MCP server runs as an ordinary CPython
process and talks to a small, dependency-free listener inside Revit over a loopback socket. Inside
Revit the socket threads never touch the API: each request becomes a job, an `ExternalEvent` is
raised, and Revit runs the job on its UI thread when it is next idle — which is the only time and
place the Revit API may be called. If Revit does not pick the job up in time (a modal dialog is
open, or a long operation is running) the caller gets a `busy` error rather than a hung socket.

Every modifying command runs in its own Revit transaction with a failure preprocessor that
suppresses warnings and rolls back on errors, so a successful call is one entry in Revit's undo
history and a failed one leaves the model untouched.

## Tools

14 tools, all defined in `src/revit_mcp/server.py`; the read-only / mutating / destructive column is
the MCP tool annotation each one carries.

| Tool | What it does | Kind |
| --- | --- | --- |
| `revit_status` | Revit version/build, bridge and protocol versions, IronPython engine, active document | read-only |
| `get_project_info` | Project Information fields, title, path, length display unit | read-only |
| `list_levels` | Levels with id, name, elevation (feet), lowest first | read-only |
| `list_categories` | Model and annotation categories; `with_counts=true` adds instance counts | read-only |
| `list_elements` | Paged instances of one category, or its types with `element_types=true` | read-only |
| `get_element` | One element in full: category, family, type, level, location, bounding box, parameters | read-only |
| `set_parameter` | Set one instance parameter; returns before/after | mutating |
| `delete_elements` | Delete elements by id; returns what Revit actually deleted, dependents included | destructive |
| `create_wall` | Straight wall between two points on a level | mutating |
| `create_level` | New level at an elevation (no plan views are created) | mutating |
| `create_sheet` | New sheet, optionally with a title block type | mutating |
| `list_views` | Views (not sheets), filterable by view type | read-only |
| `list_sheets` | Sheets with number, name and the views placed on each | read-only |
| `execute_python` | Run IronPython 2.7 against the Revit API | destructive |

Units are Revit internal units: lengths are decimal feet, angles are radians, regardless of the
project's display units. Parameter objects also carry a `value_string` field holding the value
formatted in the project's display units. `set_parameter` accepts a string for a numeric parameter
and parses it in display units, so `"3000 mm"` or `"10' 6\""` work.

`execute_python` runs on IronPython 2.7: no f-strings; both the `print` statement and the `print`
function work (the code is compiled with `dont_inherit`, so the bridge's own `__future__` import
does not leak into it). Pre-bound names are `doc`, `uidoc`, `app`, `uiapp`, `DB`, `UI`, `clr`, and
`FilteredElementCollector`, `Transaction`, `ElementId`, `XYZ`, `BuiltInCategory`,
`BuiltInParameter`. `ElementId(n)` takes a plain int: under IronPython the real constructor is
ambiguous between its `Int64` and enum overloads, so the sandbox binds a small factory that resolves
it (use `DB.ElementId` for `isinstance` checks). Assign to `result` to return a value; anything
printed comes back as `stdout`.
With `transaction=true` (the default) the code already runs inside a transaction, so it must not
open one of its own — Revit forbids nested transactions. Use `transaction=false` for read-only code
or code that manages its own transactions. Code must not open modal dialogs (`TaskDialog`): a modal
dialog blocks Revit's idle loop, which blocks the bridge and every later call.

## Requirements

- Windows with Autodesk Revit. Developed and tested against Revit 2026. The code uses no
  2026-only API and should work on 2024 and later, but that is untested.
- pyRevit 6.5.x, tested with 6.5.5. Install with
  `winget install --id pyRevit.pyRevit --exact`, or the installer from
  <https://github.com/pyrevitlabs/pyRevit/releases>.
- Python 3.10 or newer with [uv](https://docs.astral.sh/uv/) for the MCP server side.

The MCP server does not have to run on the Revit machine. It connects to `REVIT_MCP_HOST` /
`REVIT_MCP_PORT` (default `127.0.0.1:9877`), so it can run elsewhere with the port forwarded — an
SSH port forward is how the end-to-end tests run from Linux against Revit in a Windows VM.

## Install — the Revit side

1. Copy or symlink `revit/RevitMCP.extension` into `%APPDATA%\pyRevit\Extensions\`, pyRevit's
   default extension folder, which the installer creates. Alternatively register the folder that
   *contains* the `.extension` directory:

   ```powershell
   pyrevit extensions paths add C:\path\to\revit_mcp\revit
   ```

2. Restart Revit. A **RevitMCP** tab appears with **Start Bridge**, **Stop Bridge** and
   **Bridge Status**.
3. Open a project, then click **Start Bridge**. It reports the address it is listening on.

The bridge writes a log to `%LOCALAPPDATA%\RevitMCP\bridge.log`.

For unattended use, set `REVIT_MCP_AUTOSTART=1` in the environment Revit is launched with and
`startup.py` starts the bridge when the extension loads; `scripts/launch-revit-with-bridge.ps1`
does that and waits for the port. `REVIT_MCP_HOST` and `REVIT_MCP_PORT` are
honoured by both halves (defaults `127.0.0.1` and `9877`); the MCP side also reads
`REVIT_MCP_TIMEOUT` (default 60 seconds per call).

## Install — the MCP side

Claude Code:

```bash
claude mcp add --transport stdio revit -- uvx --from git+https://github.com/danlion333/revit_mcp revit-mcp
```

The `.mcp.json` equivalent:

```json
{
  "mcpServers": {
    "revit": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/danlion333/revit_mcp", "revit-mcp"]
    }
  }
}
```

Claude Desktop, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "revit": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/danlion333/revit_mcp", "revit-mcp"]
    }
  }
}
```

Claude Desktop does not see a user-level `PATH`, so if the server fails to start, use the absolute
path to `uvx` instead, for example `C:\Users\<you>\.local\bin\uvx.exe`.

From a clone:

```bash
uv sync
uv run revit-mcp          # or: uv run python -m revit_mcp
uv run revit-mcp --help   # --host, --port, --timeout, -v/--verbose, --version
```

Logs go to stderr; stdout is the MCP transport.

## A worked example

> **You:** What's open?

The model calls `revit_status`, which returns the Revit version and build, the bridge and protocol
versions, the IronPython engine, and the active document's title, path, and whether it is a family
or workshared.

> **You:** List the levels and tell me how many walls are on Level 1.

`list_levels` gives the levels with their ids and elevations in feet; `list_elements` with
`category="Walls"` returns `total` plus a page of wall summaries, each carrying its `level` and
`level_id`, which the model matches against the Level 1 id.

> **You:** Set the Comments on wall 123456 to "Fire rated".

`set_parameter(element_id=123456, parameter_name="Comments", value="Fire rated")` runs in its own
transaction and returns both states:

```json
{
  "element_id": 123456,
  "parameter": "Comments",
  "before": {
    "name": "Comments",
    "built_in": "ALL_MODEL_INSTANCE_COMMENTS",
    "storage_type": "String",
    "value": null,
    "value_string": null,
    "is_read_only": false,
    "is_shared": false,
    "has_value": false
  },
  "after": {
    "name": "Comments",
    "built_in": "ALL_MODEL_INSTANCE_COMMENTS",
    "storage_type": "String",
    "value": "Fire rated",
    "value_string": null,
    "is_read_only": false,
    "is_shared": false,
    "has_value": true
  }
}
```

A follow-up `get_element(element_id=123456)` confirms it against the model rather than against the
call's own return value.

> **You:** Add a 20 ft wall on Level 2 from (0,0) to (20,0).

`create_wall(start=[0, 0, <Level 2 elevation>], end=[20, 0, <same>], level_id=<Level 2 id>)` — the
coordinates and the default `height` of 10.0 are in feet. It returns the new wall's id and summary,
its location start/end/length, and its height parameter.

## Testing — what is tested where

```bash
uv run pytest
```

runs the unit tests: the wire protocol's framing and error decoding (`tests/test_protocol.py`); the
TCP client against a fake bridge that speaks only the wire protocol — timeouts, refused
connections, garbage replies, chunked large payloads, unicode (`tests/test_client.py`); and the MCP
tool layer driven by a real MCP client in-process with that fake bridge behind it — tool surface,
annotations, argument passing, structured results, error mapping (`tests/test_mcp_tools.py`).
These prove the CPython side and nothing whatsoever about Revit.

```bash
REVIT_MCP_E2E=1 uv run pytest tests/e2e -m e2e
```

runs `tests/e2e/test_live_revit.py` against a live Revit with the bridge running and a project open.
It starts the real `revit-mcp` server as a subprocess behind a real MCP client and asserts, in order:

1. `revit_status`: bridge reachable, a document is open, Revit version reported.
2. `get_project_info` / `list_levels` / `list_categories` / `list_views` / `list_sheets` are sane
   and non-empty.
3. `list_elements(Walls)` plus `get_element` on one wall: parameters present, location is a curve.
4. `set_parameter` on Comments, read back through `get_element`, then restored.
5. `create_level`, `create_wall` on it, `create_sheet`: ids come back, and the list/get tools see them.
6. the created wall is visible from a *second* server process — the state lives in Revit, not here.
7. `execute_python` reads the wall through the raw API and returns its length.
8. `delete_elements` cleans up, and `get_element` on the deleted wall is a `not_found` error.
9. error paths: unknown category, bad element id, read-only parameter.

Every run writes a JSON transcript of each tool call and its result to `docs/verification/`
(override the path with `REVIT_MCP_E2E_REPORT`). `docs/verification/` holds the transcript from the
author's own run against Revit 2026 in a Windows VM.

<!-- VERIFICATION_EVIDENCE -->

There are no mocked Revit tests, because a mocked Revit proves nothing.

## Protocol

Newline-delimited JSON over TCP, one object per line, UTF-8.

```text
request   {"v": 1, "id": "<opaque>", "method": "get_element", "params": {"element_id": 1234}, "timeout": 60}
success   {"id": "<opaque>", "ok": true, "result": <any JSON>}
failure   {"id": "<opaque>", "ok": false,
           "error": {"code": "revit_error", "message": "...", "details": "<traceback or null>"}}
```

`timeout` tells the bridge how long the client will wait, so the bridge can give up just before the
client does and answer with an error instead of leaving a socket to time out.

| Code | Meaning |
| --- | --- |
| `bad_request` | the request line was not valid JSON or lacked a method |
| `unsupported_protocol` | the request's protocol version is not one the bridge speaks |
| `unknown_method` | no command handler is registered under that name |
| `invalid_params` | the handler rejected the parameters (missing, wrong type, out of range) |
| `no_document` | the command needs an open project document and Revit has none |
| `not_found` | an element, view or type id did not resolve to anything in the model |
| `revit_error` | the Revit API raised while executing the command |
| `busy` | Revit did not run the command in time (a modal dialog is usually the reason) |
| `bridge_error` | the bridge itself failed outside a handler |

`revit/RevitMCP.extension/lib/revitmcp_bridge/protocol.py` is a deliberate, dependency-free mirror
of `src/revit_mcp/protocol.py`: IronPython 2.7 cannot import the CPython module, so the format is
written twice. `PROTOCOL_VERSION` is how the two halves find out that they disagree.

## Limitations

- One bridge serves one Revit session. A second Revit on the same machine needs a different port.
- The bridge binds to loopback by default and has no authentication. Anything that can reach the
  port can modify the model. Do not expose the port to a network; tunnel it (SSH, for example).
- `execute_python` can do anything the Revit API can do, including destructive things, and it is
  driven by a model. Use it on a saved model.
- ExternalEvents only run while Revit is idle, so a modal dialog in Revit makes every call return a
  `busy` error until it is dismissed.
- Revit 2024 and 2025 are untested; only 2026 has been exercised.
- Family documents are largely untested. The tools assume a project document.

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format .
uv run pytest
```

`revit/` is excluded from ruff (`extend-exclude` in `pyproject.toml`): it is IronPython 2.7 code and
the py3 rules do not apply to it. Everything under `revit/RevitMCP.extension/` must stay 2.7
compatible — no f-strings, no type annotations, `from __future__ import print_function`.

To iterate on the bridge: edit the files, then restart Revit. Stop Bridge / Start Bridge is enough
for changes confined to the button scripts, but pyRevit's engine caches the `lib/` modules, so a
Revit restart is the reliable way to pick up changes there.

## Prior art / acknowledgements

pyRevit's own Routes module was the reference for the ExternalEvent marshalling pattern used here.

## License

MIT. Copyright (c) 2026 Danylo Vyslotskyi. See [LICENSE](LICENSE).
