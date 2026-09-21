# -*- coding: utf-8 -*-
"""The RevitMCP bridge: runs inside Revit under pyRevit (IronPython 2.7).

A TCP listener on a background thread accepts newline-delimited JSON
requests from the MCP server and hands each one to Revit's UI thread through
an ExternalEvent, because the Revit API may only be called from that thread
and only while Revit is idle. See server.py for the threading, commands.py
for what each request does to the model, and protocol.py for the wire format
(a mirror of src/revit_mcp/protocol.py on the CPython side).

This package must stay IronPython 2.7 compatible: no f-strings, no
type annotations, print as a function via __future__.
"""

BRIDGE_VERSION = "1.0.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9877
