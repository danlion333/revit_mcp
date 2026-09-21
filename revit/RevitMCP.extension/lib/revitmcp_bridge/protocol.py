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


# Output is written by a small encoder of our own rather than json.dumps.
# IronPython 2.7's json encoder treats every str as a byte string and calls
# .decode("utf-8") on any that contains a character in U+0080..U+00FF, which
# raises on a model with a view called "Élévation". Escaping everything
# non-ASCII as \uXXXX here is lossless for the CPython side and sidesteps
# the whole str/unicode question in the host runtime.

try:
    long
except NameError:  # pragma: no cover - IronPython 3
    long = int  # noqa: A001
try:
    unicode
except NameError:  # pragma: no cover - IronPython 3
    unicode = str  # noqa: A001

_ESCAPES = {u'"': u'\\"', u"\\": u"\\\\", u"\n": u"\\n", u"\r": u"\\r", u"\t": u"\\t", u"\b": u"\\b", u"\f": u"\\f"}


def _encode_string(value):
    out = [u'"']
    for ch in value:
        code = ord(ch)
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif code > 0xFFFF:
            # CPython gives whole code points; .NET strings already come as UTF-16 units.
            code -= 0x10000
            out.append(u"\\u%04x\\u%04x" % (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)))
        elif code < 0x20 or code > 0x7E:
            out.append(u"\\u%04x" % code)
        else:
            out.append(ch)
    out.append(u'"')
    return u"".join(out)


def dumps(value):
    """JSON text (ASCII only) for None, bool, int, long, float, str, list/tuple, dict."""
    if value is None:
        return u"null"
    if value is True:
        return u"true"
    if value is False:
        return u"false"
    if isinstance(value, (int, long)):
        return unicode(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return u"null"  # JSON has no NaN/Infinity
        return unicode(repr(value))
    if isinstance(value, basestring):  # noqa: F821
        return _encode_string(value)
    if isinstance(value, (list, tuple)):
        return u"[" + u",".join(dumps(v) for v in value) + u"]"
    if isinstance(value, dict):
        items = []
        for key, item in value.items():
            if not isinstance(key, basestring):  # noqa: F821
                key = unicode(key)
            items.append(_encode_string(key) + u":" + dumps(item))
        return u"{" + u",".join(items) + u"}"
    raise TypeError("cannot encode %s as JSON" % type(value).__name__)


def encode_ok(request_id, result):
    return dumps({"id": request_id, "ok": True, "result": result})


def encode_error(request_id, code, message, details=None):
    return dumps({"id": request_id, "ok": False, "error": {"code": code, "message": message, "details": details}})
