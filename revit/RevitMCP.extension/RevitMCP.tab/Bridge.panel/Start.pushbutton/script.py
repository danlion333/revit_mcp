# -*- coding: utf-8 -*-
"""Start the RevitMCP bridge so an MCP client (Claude, Cursor, ...) can drive this Revit session.

Listens on 127.0.0.1:9877 by default (REVIT_MCP_HOST / REVIT_MCP_PORT override)."""
__title__ = "Start\nBridge"
__author__ = "Danylo Vyslotskyi"
# Keep this engine alive after the script returns: the listener thread and the
# ExternalEvent handler it created live in it.
__persistentengine__ = True

from pyrevit import forms  # noqa: E402

from revitmcp_bridge import control, server  # noqa: E402

existing = control.ping()
if existing:
    forms.alert(
        "RevitMCP bridge %s is already running (%d requests served)."
        % (existing.get("bridge_version"), existing.get("requests_served", 0)),
        title="RevitMCP",
    )
else:
    try:
        bridge = server.start()
    except Exception as exc:  # port in use, etc.
        forms.alert("Could not start the RevitMCP bridge:\n%s" % exc, title="RevitMCP", warn_icon=True)
    else:
        forms.alert(
            "RevitMCP bridge listening on %s:%d.\nLog: %s" % (bridge.host, bridge.port, bridge.log.path),
            title="RevitMCP",
        )
