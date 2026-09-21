"""A small, synchronous TCP client for the bridge running inside Revit.

One connection per call. That is deliberate: the bridge serves clients
sequentially on a single background thread, Revit executes one command at a
time on its UI thread anyway, and a fresh connection per request means a
Revit restart (or a crashed bridge) never leaves this side holding a dead
socket. The cost is a loopback TCP handshake per tool call, which is noise
next to the Revit API work behind it.
"""

from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass
from typing import Any

from .protocol import BridgeError, ProtocolError, Request, Response

log = logging.getLogger("revit_mcp.client")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9877
DEFAULT_TIMEOUT = 60.0
MAX_LINE = 64 * 1024 * 1024  # a get_element on a huge family can be big, but 64 MiB is a bug


class RevitConnectionError(ConnectionError):
    """The bridge is not listening (Revit is closed, or the bridge was never started)."""


class RevitTimeoutError(TimeoutError):
    """The bridge accepted the request but did not answer within the timeout."""


@dataclass(frozen=True)
class Settings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        return cls(
            host=env.get("REVIT_MCP_HOST", DEFAULT_HOST),
            port=int(env.get("REVIT_MCP_PORT", DEFAULT_PORT)),
            timeout=float(env.get("REVIT_MCP_TIMEOUT", DEFAULT_TIMEOUT)),
        )


class RevitClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()

    def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
        """Send one request and return its result.

        Raises :class:`RevitConnectionError`, :class:`RevitTimeoutError`,
        :class:`ProtocolError` or :class:`BridgeError`.
        """
        request = Request(method=method, params=dict(params or {}))
        timeout = self.settings.timeout if timeout is None else timeout
        log.debug("-> %s %s", method, request.params)
        raw = self._exchange(request.encode(), timeout)
        response = Response.decode(raw)
        if response.id != request.id:
            raise ProtocolError(f"response id {response.id!r} does not match request id {request.id!r}")
        if response.ok:
            log.debug("<- %s ok", method)
        else:
            log.debug("<- %s error %s", method, response.error)
        return response.unwrap()

    def ping(self, timeout: float = 2.0) -> dict[str, Any]:
        """Cheap liveness check answered on the bridge's socket thread, never touching Revit's UI thread."""
        return self.call("ping", timeout=timeout)

    # ------------------------------------------------------------------ internals

    def _exchange(self, payload: bytes, timeout: float) -> bytes:
        host, port = self.settings.host, self.settings.port
        try:
            sock = socket.create_connection((host, port), timeout=min(timeout, 10.0))
        except (TimeoutError, ConnectionRefusedError, OSError) as exc:
            raise RevitConnectionError(
                f"Cannot reach the RevitMCP bridge at {host}:{port} ({exc}). "
                "Is Revit running with the RevitMCP extension loaded, and was the bridge "
                "started (RevitMCP tab -> Start Bridge, or REVIT_MCP_AUTOSTART=1)?"
            ) from exc
        with sock:
            sock.settimeout(timeout)
            try:
                sock.sendall(payload)
                return self._read_line(sock)
            except TimeoutError as exc:
                raise RevitTimeoutError(
                    f"The bridge did not answer within {timeout:.0f}s. Revit may be showing a modal "
                    "dialog or running a long operation; dismiss it and retry, or raise REVIT_MCP_TIMEOUT."
                ) from exc
            except (ConnectionResetError, BrokenPipeError) as exc:
                raise RevitConnectionError(f"Connection to the bridge dropped mid-request: {exc}") from exc

    @staticmethod
    def _read_line(sock: socket.socket) -> bytes:
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                if not chunks:
                    raise RevitConnectionError("The bridge closed the connection without answering.")
                break
            chunks.append(chunk)
            size += len(chunk)
            if chunk.endswith(b"\n"):
                break
            if size > MAX_LINE:
                raise ProtocolError(f"response exceeded {MAX_LINE} bytes without a newline")
        return b"".join(chunks)


__all__ = [
    "BridgeError",
    "ProtocolError",
    "RevitClient",
    "RevitConnectionError",
    "RevitTimeoutError",
    "Settings",
]
