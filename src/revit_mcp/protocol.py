"""Wire protocol between the MCP server and the bridge running inside Revit.

The two halves of this project run in different Python runtimes: this module
runs on CPython next to the MCP client, the bridge runs on IronPython inside
Revit (see ``revit/RevitMCP.extension/lib/revitmcp_bridge/protocol.py``,
which is a deliberately dependency-free mirror of the same format). Anything
that changes here must change there too; ``PROTOCOL_VERSION`` is how the two
sides find out they disagree.

Format: newline-delimited JSON over TCP, one object per line, UTF-8.

Request::

    {"v": 1, "id": "<opaque>", "method": "get_element", "params": {"element_id": 1234}}

Success::

    {"id": "<opaque>", "ok": true, "result": <any JSON>}

Failure::

    {"id": "<opaque>", "ok": false,
     "error": {"code": "revit_error", "message": "...", "details": "<traceback or null>"}}

Error codes the bridge emits are listed in :data:`ERROR_CODES`.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1

#: Error codes the bridge can return in ``error.code``.
ERROR_CODES = {
    "bad_request": "the request line was not valid JSON or lacked a method",
    "unsupported_protocol": "the request's protocol version is not one the bridge speaks",
    "unknown_method": "no command handler is registered under that name",
    "invalid_params": "the handler rejected the parameters (missing, wrong type, out of range)",
    "no_document": "the command needs an open project document and Revit has none",
    "not_found": "an element, view or type id did not resolve to anything in the model",
    "revit_error": "the Revit API raised while executing the command",
    "busy": "Revit did not run the command in time (a modal dialog is usually the reason)",
    "bridge_error": "the bridge itself failed outside a handler",
}


class ProtocolError(ValueError):
    """A frame that could not be decoded as this protocol."""


@dataclass(frozen=True)
class Request:
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    v: int = PROTOCOL_VERSION

    def encode(self) -> bytes:
        """One line, newline-terminated, ready for the socket."""
        payload = {"v": self.v, "id": self.id, "method": self.method, "params": self.params}
        return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


@dataclass(frozen=True)
class BridgeError(Exception):
    """A failure reported by the bridge inside Revit (``ok: false``)."""

    code: str
    message: str
    details: str | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.message}"


@dataclass(frozen=True)
class Response:
    id: str | None
    ok: bool
    result: Any = None
    error: BridgeError | None = None

    @classmethod
    def decode(cls, line: bytes | str) -> Response:
        """Parse one response line. Raises :class:`ProtocolError` on malformed input."""
        if isinstance(line, bytes):
            try:
                line = line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProtocolError(f"response is not UTF-8: {exc}") from exc
        line = line.strip()
        if not line:
            raise ProtocolError("empty response line")
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"response is not JSON: {exc}: {line[:200]!r}") from exc
        if not isinstance(obj, dict) or "ok" not in obj:
            raise ProtocolError(f"response lacks an 'ok' field: {line[:200]!r}")
        ok = bool(obj["ok"])
        if ok:
            return cls(id=obj.get("id"), ok=True, result=obj.get("result"))
        err = obj.get("error")
        if not isinstance(err, dict):
            err = {"code": "bridge_error", "message": str(err or "unknown error")}
        return cls(
            id=obj.get("id"),
            ok=False,
            error=BridgeError(
                code=str(err.get("code", "bridge_error")),
                message=str(err.get("message", "")),
                details=err.get("details"),
            ),
        )

    def unwrap(self) -> Any:
        """Return the result, or raise the :class:`BridgeError` the bridge sent."""
        if self.ok:
            return self.result
        assert self.error is not None
        raise self.error
