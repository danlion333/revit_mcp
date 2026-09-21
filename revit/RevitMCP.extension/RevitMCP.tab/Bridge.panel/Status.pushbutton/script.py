# -*- coding: utf-8 -*-
"""Show whether the RevitMCP bridge is running and where it listens."""
__title__ = "Bridge\nStatus"
__author__ = "Danylo Vyslotskyi"

from pyrevit import forms  # noqa: E402

from revitmcp_bridge import control  # noqa: E402

host, port = control.configured_endpoint()
info = control.ping()
if info:
    forms.alert(
        "RevitMCP bridge %s is running on %s:%d\nprotocol %s, %d request(s) served since %s"
        % (
            info.get("bridge_version"),
            host,
            port,
            info.get("protocol_version"),
            info.get("requests_served", 0),
            info.get("started_at"),
        ),
        title="RevitMCP",
    )
else:
    forms.alert("RevitMCP bridge is not running (nothing listens on %s:%d)." % (host, port), title="RevitMCP")
