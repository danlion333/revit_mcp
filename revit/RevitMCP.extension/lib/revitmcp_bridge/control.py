# -*- coding: utf-8 -*-
"""Talk to a running bridge from another engine or process (ping / shutdown).

The Stop and Status buttons use this instead of the module singleton in
server.py because pyRevit runs each button in its own engine.
"""
from __future__ import print_function

import json
import os

import clr

clr.AddReference("System")
from System.IO import StreamReader, StreamWriter  # noqa: E402
from System.Net.Sockets import TcpClient  # noqa: E402
from System.Text import UTF8Encoding  # noqa: E402

from . import DEFAULT_HOST, DEFAULT_PORT  # noqa: E402
from .protocol import PROTOCOL_VERSION  # noqa: E402


def configured_endpoint():
    return (os.environ.get("REVIT_MCP_HOST") or DEFAULT_HOST, int(os.environ.get("REVIT_MCP_PORT") or DEFAULT_PORT))


def request(method, host=None, port=None, timeout_ms=2000):
    """Send one request to the bridge and return the decoded response, or None if nothing is listening."""
    default_host, default_port = configured_endpoint()
    host = host or default_host
    port = int(port or default_port)
    client = TcpClient()
    try:
        result = client.BeginConnect(host, port, None, None)
        if not result.AsyncWaitHandle.WaitOne(timeout_ms):
            return None
        client.EndConnect(result)
    except Exception:
        return None
    try:
        stream = client.GetStream()
        stream.ReadTimeout = timeout_ms
        utf8 = UTF8Encoding(False)
        writer = StreamWriter(stream, utf8)
        writer.NewLine = "\n"
        writer.WriteLine(json.dumps({"v": PROTOCOL_VERSION, "id": "ui", "method": method, "params": {}}))
        writer.Flush()
        line = StreamReader(stream, utf8).ReadLine()
        return json.loads(line) if line else None
    except Exception:
        return None
    finally:
        client.Close()


def ping(host=None, port=None):
    reply = request("ping", host, port)
    if reply and reply.get("ok"):
        return reply["result"]
    return None


def shutdown(host=None, port=None):
    reply = request("shutdown", host, port)
    return bool(reply and reply.get("ok"))
