# -*- coding: utf-8 -*-
"""The TCP listener and the hand-off to Revit's UI thread.

Two threads matter:

* the *socket threads* (ours) accept clients with a .NET TcpListener and
  read one request line at a time. They never touch the Revit API.
* the *Revit UI thread* runs `_Handler.Execute` whenever Revit is idle and
  an ExternalEvent has been raised. That is the only place the API is used.

A request becomes a `_Job`, goes on a queue, the ExternalEvent is raised,
and the connection's thread waits on the job's Event. If Revit does not get
around to it in time (a modal dialog is up, or it is mid-regeneration for
minutes) the client gets a `busy` error and the job is marked abandoned: a
job still queued is then skipped, but one that Revit has already begun runs
to completion, because the API offers no way to interrupt it. The `busy`
message says so.

Each accepted connection gets its own thread, so a slow command on one
connection cannot stop the Start/Stop/Status buttons (which ping the port)
or a retry from being answered; the handler queue serialises the Revit work.

.NET sockets rather than IronPython's `socket` module: the host runtime is
.NET, its sockets are what pyRevit itself uses, and IronPython's socket
shim has enough sharp edges (accept/timeout semantics) that avoiding it is
the pragmatic choice.
"""
from __future__ import print_function

import codecs
import datetime
import os
import threading
import traceback

import clr

clr.AddReference("System")
from System.IO import StreamReader, StreamWriter  # noqa: E402
from System.Net import IPAddress  # noqa: E402
from System.Net.Sockets import TcpListener  # noqa: E402
from System.Text import UTF8Encoding  # noqa: E402

from Autodesk.Revit.UI import ExternalEvent, ExternalEventRequest, IExternalEventHandler  # noqa: E402

from . import BRIDGE_VERSION, DEFAULT_HOST, DEFAULT_PORT  # noqa: E402
from . import commands  # noqa: E402
from .protocol import PROTOCOL_VERSION, CommandError, encode_error, encode_ok, parse_request  # noqa: E402

clr.AddReference("RevitAPIUI")

DEFAULT_LOG = os.path.join(os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or ".", "RevitMCP", "bridge.log")


class Log(object):
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        try:
            folder = os.path.dirname(path)
            if folder and not os.path.isdir(folder):
                os.makedirs(folder)
        except Exception:
            pass

    def __call__(self, message):
        line = u"%sZ  %s\n" % (datetime.datetime.utcnow().isoformat(), message)
        with self.lock:
            try:
                with codecs.open(self.path, "a", "utf-8") as fh:
                    fh.write(line)
            except Exception:
                pass


class _Job(object):
    def __init__(self, request_id, method, params):
        self.request_id = request_id
        self.method = method
        self.params = params
        self.result = None
        self.error = None
        self.done = threading.Event()
        self.abandoned = False
        self.started = False


class _Handler(IExternalEventHandler):
    """Runs queued jobs on Revit's UI thread. Constructed once, reused for every request."""

    def __init__(self, log):
        self.log = log
        self.queue = []
        self.lock = threading.Lock()

    def push(self, job):
        with self.lock:
            self.queue.append(job)

    def _pop(self):
        with self.lock:
            if not self.queue:
                return None
            return self.queue.pop(0)

    def Execute(self, uiapp):
        while True:
            job = self._pop()
            if job is None:
                return
            try:
                if job.abandoned:
                    self.log("skip abandoned %s %s" % (job.request_id, job.method))
                    continue
                job.started = True
                job.result = commands.dispatch(uiapp, job.method, job.params)
            except CommandError as exc:
                job.error = exc
            except Exception as exc:  # anything the Revit API threw
                job.error = CommandError("revit_error", "%s: %s" % (type(exc).__name__, exc), traceback.format_exc())
            finally:
                job.done.set()

    def abandon_all(self):
        with self.lock:
            pending, self.queue = self.queue, []
        for job in pending:
            job.abandoned = True
            job.error = CommandError("bridge_error", "bridge stopped before the command ran")
            job.done.set()

    def GetName(self):
        return "RevitMCP bridge"


class Bridge(object):
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, log_path=DEFAULT_LOG, handler_timeout=120.0):
        self.host = host
        self.port = int(port)
        self.log = Log(log_path)
        self.handler_timeout = float(handler_timeout)
        self.listener = None
        self.thread = None
        self.handler = None
        self.event = None
        self.running = False
        self.started_at = None
        self.requests_served = 0
        self.clients = set()
        self.clients_lock = threading.Lock()

    # ------------------------------------------------------------------ lifecycle

    def start(self):
        """Must be called from a Revit API context (a pyRevit command or startup script)."""
        if self.running:
            return
        self.handler = _Handler(self.log)
        self.event = ExternalEvent.Create(self.handler)
        self.listener = TcpListener(IPAddress.Parse(self.host), self.port)
        self.listener.Start()
        self.running = True
        self.started_at = datetime.datetime.utcnow()
        self.thread = threading.Thread(target=self._serve, name="RevitMCP-bridge")
        self.thread.daemon = True
        self.thread.start()
        self.log("bridge %s listening on %s:%d (protocol %d)" % (BRIDGE_VERSION, self.host, self.port, PROTOCOL_VERSION))

    def stop(self):
        if not self.running:
            return
        self.running = False
        try:
            self.listener.Stop()
        except Exception:
            pass
        with self.clients_lock:
            clients, self.clients = list(self.clients), set()
        for client in clients:
            try:
                client.Close()  # unblocks a thread parked in ReadLine
            except Exception:
                pass
        if self.handler is not None:
            self.handler.abandon_all()
        if self.event is not None:
            try:
                self.event.Dispose()
            except Exception:
                pass
            self.event = None
        self.log("bridge stopped after %d request(s)" % self.requests_served)

    # ------------------------------------------------------------------ socket thread

    def _serve(self):
        # Nothing may escape this thread: pyRevit routes stderr to a WPF output
        # window that only the UI thread may touch, and an unhandled exception on
        # a worker thread takes the whole Revit process down.
        try:
            while self.running:
                try:
                    client = self.listener.AcceptTcpClient()
                except Exception:
                    if self.running:
                        self.log("accept failed:\n" + traceback.format_exc())
                    break
                with self.clients_lock:
                    self.clients.add(client)
                worker = threading.Thread(target=self._client_thread, args=(client,), name="RevitMCP-client")
                worker.daemon = True
                worker.start()
        except Exception:
            self.log("listener thread died:\n" + traceback.format_exc())
        finally:
            self.running = False

    def _client_thread(self, client):
        try:
            self._handle_client(client)
        except Exception:
            if self.running:
                self.log("client handling failed:\n" + traceback.format_exc())
        finally:
            with self.clients_lock:
                self.clients.discard(client)
            try:
                client.Close()
            except Exception:
                pass

    def _handle_client(self, client):
        stream = client.GetStream()
        utf8 = UTF8Encoding(False)
        reader = StreamReader(stream, utf8)
        writer = StreamWriter(stream, utf8)
        writer.NewLine = "\n"
        while self.running:
            line = reader.ReadLine()
            if line is None:
                return
            if not line.strip():
                continue
            self.requests_served += 1
            reply = self._handle_line(line)
            writer.WriteLine(reply)
            writer.Flush()
            if not self.running:
                return

    def _handle_line(self, line):
        try:
            request = parse_request(line)
        except CommandError as exc:
            self.log("bad request: %s" % exc.message)
            return encode_error(exc.request_id, exc.code, exc.message, exc.details)
        request_id, method, params = request.id, request.method, request.params

        if method == "ping":
            return encode_ok(
                request_id,
                {
                    "pong": True,
                    "bridge_version": BRIDGE_VERSION,
                    "protocol_version": PROTOCOL_VERSION,
                    "requests_served": self.requests_served,
                    "started_at": self.started_at.isoformat() + "Z" if self.started_at else None,
                },
            )
        if method == "shutdown":
            self.log("shutdown requested by client")
            self.stop()
            return encode_ok(request_id, {"stopped": True})

        job = _Job(request_id, method, params)
        # Give up just before the client does, so it is this side that frees the slot
        # and the client sees a real error instead of a socket timeout.
        wait = self.handler_timeout
        if request.timeout is not None and request.timeout > 0:
            wait = max(1.0, min(wait, request.timeout - 1.0))
        self.handler.push(job)
        outcome = self.event.Raise()
        if outcome == ExternalEventRequest.Denied:
            job.abandoned = True
            return encode_error(
                request_id,
                "busy",
                "Revit refused the external event; it is probably showing a modal dialog or a command is active",
            )
        if outcome == ExternalEventRequest.TimedOut:
            job.abandoned = True
            return encode_error(request_id, "busy", "Revit timed out accepting the external event")

        if not job.done.wait(wait):
            job.abandoned = True
            self.log("timeout (%.0fs) waiting for Revit to run %s %s (started=%s)" % (wait, request_id, method, job.started))
            if job.started:
                message = (
                    "Revit is still running %s after %.0fs; it will finish on its own and may still change the "
                    "model. Check the model (revit_status, then list/get) before retrying." % (method, wait)
                )
            else:
                message = (
                    "Revit did not pick up %s within %.0fs, so it was dropped unrun; Revit is probably showing "
                    "a modal dialog or is busy. Dismiss it and retry." % (method, wait)
                )
            return encode_error(request_id, "busy", message)
        if job.error is not None:
            self.log("%s -> %s: %s" % (method, job.error.code, job.error.message))
            return encode_error(request_id, job.error.code, job.error.message, job.error.details)
        self.log("%s ok" % method)
        try:
            return encode_ok(request_id, job.result)
        except (TypeError, ValueError) as exc:
            return encode_error(request_id, "bridge_error", "result of %s is not JSON serialisable: %s" % (method, exc))


# --------------------------------------------------------------------------- module-level singleton
#
# pyRevit gives each command its own engine, so a module global here is not
# shared between the Start and Stop buttons. The Stop button therefore does not
# reach for this object: it connects to the port and sends a `shutdown` request,
# which works from any process. This singleton only serves the engine that
# started the bridge (typically startup.py or the Start button).

_bridge = None


def get_bridge():
    return _bridge


def start(host=None, port=None, log_path=None):
    global _bridge
    if _bridge is not None and _bridge.running:
        return _bridge
    _bridge = Bridge(
        host=host or os.environ.get("REVIT_MCP_HOST") or DEFAULT_HOST,
        port=int(port or os.environ.get("REVIT_MCP_PORT") or DEFAULT_PORT),
        log_path=log_path or DEFAULT_LOG,
    )
    _bridge.start()
    _keep_alive(_bridge)
    return _bridge


def _keep_alive(bridge):
    """Root the bridge in pyRevit's AppDomain-level store so engine cleanup cannot collect it."""
    try:
        from pyrevit.coreutils import envvars

        envvars.set_pyrevit_env_var("REVITMCP_BRIDGE", bridge)
    except Exception:
        pass


def stop():
    global _bridge
    if _bridge is not None:
        _bridge.stop()
        _bridge = None
    try:
        from pyrevit.coreutils import envvars

        envvars.set_pyrevit_env_var("REVITMCP_BRIDGE", None)
    except Exception:
        pass
