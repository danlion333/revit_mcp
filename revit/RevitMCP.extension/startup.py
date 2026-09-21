# -*- coding: utf-8 -*-
"""pyRevit runs this on Revit's main thread when it loads the extension at startup.

Auto-start the bridge when REVIT_MCP_AUTOSTART is 1/true/yes in the environment
Revit was launched with. Unattended sessions (CI, a VM driven by an agent) use
this; a person clicks Start Bridge instead. Nothing is printed here on purpose:
pyRevit would open its output window for it.
"""
from __future__ import print_function

import os

_flag = (os.environ.get("REVIT_MCP_AUTOSTART") or "").strip().lower()
if _flag in ("1", "true", "yes", "on"):
    try:
        from revitmcp_bridge import server

        server.start()
    except Exception:
        # Deliberately self-contained: if the failure was importing the bridge itself,
        # this handler must still be able to write the traceback somewhere.
        try:
            import traceback

            _log = os.path.join(os.environ.get("LOCALAPPDATA") or ".", "RevitMCP", "bridge.log")
            if not os.path.isdir(os.path.dirname(_log)):
                os.makedirs(os.path.dirname(_log))
            with open(_log, "a") as _fh:
                _fh.write("auto-start failed:\n" + traceback.format_exc() + "\n")
        except Exception:
            pass
