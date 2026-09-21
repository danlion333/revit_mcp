"""revit-mcp: a Model Context Protocol server for Autodesk Revit.

Two halves: this package (CPython, runs next to the MCP client and speaks MCP
over stdio) and a pyRevit extension (IronPython, runs inside Revit and executes
Revit API calls on Revit's UI thread). They talk newline-delimited JSON over
a loopback TCP socket; see :mod:`revit_mcp.protocol`.
"""

__version__ = "1.0.0"
