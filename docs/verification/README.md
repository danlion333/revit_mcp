# Verification against a real Revit

This directory is the evidence for the claim that revit-mcp works against Autodesk Revit,
not against a mock. Everything here was produced by one run of `tests/e2e/test_live_revit.py`
on 2026-09-21 and copied out of the machine that ran Revit.

## Environment

| | |
| --- | --- |
| Revit | Autodesk Revit 2026, build 26.5.0.55 (journal: `journal.0056.txt (2026-09-21T13:02:16.1894241Z)`) |
| pyRevit | 6.5.5.26237+2044 |
| Engine inside Revit | IronPython 2.7.12 (pyRevit's default engine for Revit 2026) |
| Model | `Snowdon Towers Sample Architectural` (the sample project that ships with Revit 2026, C:\Program Files\Autodesk\Revit 2026\Samples\Snowdon Towers Sample Architectural.rvt) |
| Where Revit ran | a Windows 11 VM (dockur/windows under KVM) with no GPU; Revit was started with `REVIT_MCP_AUTOSTART=1` so `startup.py` started the bridge |
| Where the MCP server and test ran | Linux, connecting to the bridge through an SSH port-forward to the VM's loopback port 9877 |
| revit-mcp | 1.0.0 at the commit that adds this directory |

## What the run did

`tests/e2e/test_live_revit.py` starts the real `revit-mcp` server as a stdio subprocess behind the
official `mcp` Python client and calls tools the way an MCP client would. 28 tool calls:
23 succeeded and 5 were expected failures (the error-path checks). In order:

1. `revit_status`: bridge reachable, document open, Revit version reported.
2. `get_project_info`, `list_levels`, `list_categories(with_counts=true)`, `list_views`, `list_sheets`.
3. `list_elements("Walls")` and `get_element` on the first wall (id 619340) with type parameters.
4. `set_parameter` Comments on that wall to a run-specific tag, `get_element` to read it back from
   the model, `set_parameter` to restore the previous value.
5. `create_level` 12 ft above the top level, `create_wall` (20 ft, 9.5 ft high) on it, `create_sheet`;
   `list_levels`, `list_elements` (wall count went up by one), `get_element`, `list_sheets` confirm.
6. `get_element` on the new wall from a **second** `revit-mcp` process: the state lives in Revit.
7. `execute_python` reads the wall through the raw API (`doc.GetElement(ElementId(...))`), returns
   its length and level; a second call raises on purpose and comes back as `revit_error` with the
   IronPython traceback.
8. `delete_elements` on the wall, sheet and level; Revit reports the dependents it removed as well;
   `get_element` on the wall is now `not_found`; the wall count is back to where it started.
9. Error paths: unknown category, nonexistent element id, read-only parameter (`Area`).

## Files

- `e2e-revit2026-20260921T130209Z.json`: the full transcript, every call with its arguments, timing
  and the structured result or error text exactly as the MCP client received it.
- `bridge-log-20260921T130209Z.txt`: `%LOCALAPPDATA%\RevitMCP\bridge.log` from inside the VM for that
  session, written by the bridge on the Revit side, one line per command.
- `revit-journal-excerpt-20260921T130209Z.txt`: lines from Revit's own journal file for the session.
  Revit records every ExternalEvent it executes (`ExternalEventName: RevitMCP bridge`, 8 of them)
  and every committed transaction by name (`Transaction Successful`: 6 lines, e.g.
  `"Transaction Successful"  , "RevitMCP: set Comments"`). This is Revit's log, not ours.

The VM's Windows user name is replaced by `<user>` in paths; nothing else was edited.

## Caveats, stated plainly

- The VM has no GPU. Revit shows a "Hardware Acceleration disabled" dialog on startup; the run
  harness clicks Close on it. While any modal dialog is up, ExternalEvents do not run and the
  bridge answers `busy` (that behaviour was observed and is what the error message describes).
- The Start / Stop / Status ribbon buttons were not clicked in this run (no GUI automation for the
  ribbon). The Start button runs the same `server.start()` that `startup.py` ran here, and the Stop
  button's `shutdown` request was exercised directly over the socket at the end of the session:
  `shutdown` was acknowledged with `{'stopped': True}` after 33 requests; the bridge log then records "bridge stopped after 33 request(s)" and `netstat` inside the VM no longer lists port 9877. (Through the SSH tunnel used for this run the test client saw a 2 s timeout rather than "connection refused", because the tunnel accepts the local connection before the guest refuses it; a client on the Revit machine itself gets the refusal.)
- Only Revit 2026 was exercised. Nothing here says anything about 2024 or 2025.
- The model was never saved; the run creates and deletes its own elements and restores the one
  parameter it changes.
