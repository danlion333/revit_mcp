# -*- coding: utf-8 -*-
"""Wire protocol, bridge side.

A dependency-free mirror of src/revit_mcp/protocol.py. The two files must
agree; PROTOCOL_VERSION is how they find out when they do not.
"""
from __future__ import print_function

import json

PROTOCOL_VERSION = 1


class CommandError(Exception):
    """A failure the bridge reports to the client with a code the model can act on."""

    def __init__(self, code, message, details=None, request_id=None):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.details = details
        self.request_id = request_id


class Request(object):
    def __init__(self, request_id, method, params, timeout=None):
        self.id = request_id
        self.method = method
        self.params = params
        self.timeout = timeout  # seconds the client will wait, or None


def parse_request(line):
    """Return a Request or raise CommandError('bad_request'|'unsupported_protocol').

    The error carries the request id when one could be read, so the client can
    match the reply to its request and show the real message.
    """
    try:
        obj = json.loads(line)
    except ValueError as exc:
        raise CommandError("bad_request", "request is not valid JSON: %s" % exc)
    if not isinstance(obj, dict):
        raise CommandError("bad_request", "request must be a JSON object")
    request_id = obj.get("id")
    version = obj.get("v", PROTOCOL_VERSION)
    if version != PROTOCOL_VERSION:
        raise CommandError(
            "unsupported_protocol",
            "bridge speaks protocol %s, request used %s; update revit-mcp or the extension"
            % (PROTOCOL_VERSION, version),
            request_id=request_id,
        )
    method = obj.get("method")
    if not method or not isinstance(method, basestring):  # noqa: F821 - IronPython 2.7
        raise CommandError("bad_request", "request lacks a 'method' string", request_id=request_id)
    params = obj.get("params") or {}
    if not isinstance(params, dict):
        raise CommandError("bad_request", "'params' must be an object", request_id=request_id)
    timeout = obj.get("timeout")
    if timeout is not None:
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            raise CommandError("bad_request", "'timeout' must be a number of seconds", request_id=request_id)
    return Request(request_id, method, params, timeout)


# ensure_ascii=True on purpose: IronPython 2.7's json encoder mixes str and
# unicode fragments when asked for raw non-ASCII output and can raise
# UnicodeDecodeError on a model with accented or Cyrillic names. \uXXXX
# escapes are decoded losslessly on the CPython side.


def encode_ok(request_id, result):
    return json.dumps({"id": request_id, "ok": True, "result": result}, ensure_ascii=True)


def encode_error(request_id, code, message, details=None):
    return json.dumps(
        {"id": request_id, "ok": False, "error": {"code": code, "message": message, "details": details}},
        ensure_ascii=True,
    )
