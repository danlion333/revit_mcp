# -*- coding: utf-8 -*-
"""Stop the RevitMCP bridge. MCP clients will get a connection error until it is started again."""
__title__ = "Stop\nBridge"
__author__ = "Danylo Vyslotskyi"

from pyrevit import forms  # noqa: E402

from revitmcp_bridge import control  # noqa: E402

if control.shutdown():
    forms.alert("RevitMCP bridge stopped.", title="RevitMCP")
else:
    forms.alert("No RevitMCP bridge is listening on %s:%d." % control.configured_endpoint(), title="RevitMCP")
