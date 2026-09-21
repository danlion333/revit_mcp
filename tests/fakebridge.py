"""A fake bridge for tests.

`FakeBridge` is a TCP server that speaks the bridge wire protocol exactly, and
nothing else: it proves the MCP side's framing, error mapping and tool plumbing.
It does not, and cannot, prove anything about Revit. The Revit half is verified
by `tests/e2e/test_live_revit.py` against a real Revit session.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Callable
from typing import Any

Handler = Callable[[str, dict[str, Any]], Any]


class FakeBridge:
    """Newline-delimited JSON server; `handler(method, params)` returns a result or raises."""

    def __init__(self, handler: Handler):
        self.handler = handler
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.requests: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        self.sock.settimeout(0.1)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn: socket.socket) -> None:
        with conn, conn.makefile("rb") as reader:
            for raw in reader:
                req = json.loads(raw.decode("utf-8"))
                self.requests.append(req)
                try:
                    result = self.handler(req["method"], req.get("params", {}))
                    if result is NO_REPLY:
                        return
                    if result is GARBAGE:
                        conn.sendall(b"this is not json\n")
                        continue
                    reply = {"id": req["id"], "ok": True, "result": result}
                except FakeError as exc:
                    reply = {"id": req["id"], "ok": False, "error": exc.payload}
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))

    def close(self) -> None:
        self._stop.set()
        self.sock.close()
        self.thread.join(timeout=2)


NO_REPLY = object()  # handler returns this: close without answering
GARBAGE = object()  # handler returns this: answer with a non-JSON line


class FakeError(Exception):
    def __init__(self, code: str, message: str, details: str | None = None):
        super().__init__(message)
        self.payload = {"code": code, "message": message, "details": details}
