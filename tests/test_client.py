"""RevitClient against a fake bridge that only speaks the protocol."""

import pytest

from revit_mcp.client import (
    BridgeError,
    ProtocolError,
    RevitClient,
    RevitConnectionError,
    RevitTimeoutError,
    Settings,
)
from tests.fakebridge import GARBAGE, NO_REPLY, FakeError


def test_call_round_trips_method_params_and_result(fake_bridge, client_for):
    b = fake_bridge(lambda m, p: {"echo": m, "params": p})
    out = client_for(b).call("get_element", {"element_id": 7})
    assert out == {"echo": "get_element", "params": {"element_id": 7}}
    assert b.requests[0]["method"] == "get_element"
    assert b.requests[0]["v"] == 1
    assert b.requests[0]["timeout"] == 5.0


def test_bridge_error_is_raised_with_code_and_details(fake_bridge, client_for):
    def handler(m, p):
        raise FakeError("revit_error", "InvalidOperationException: nope", "Traceback ...")

    with pytest.raises(BridgeError) as excinfo:
        client_for(fake_bridge(handler)).call("set_parameter", {})
    assert excinfo.value.code == "revit_error"
    assert excinfo.value.details == "Traceback ..."


def test_connection_refused_is_actionable():
    client = RevitClient(Settings(host="127.0.0.1", port=1, timeout=1))
    with pytest.raises(RevitConnectionError) as excinfo:
        client.call("ping")
    assert "Start Bridge" in str(excinfo.value)


def test_timeout_when_bridge_never_answers(fake_bridge, client_for):
    b = fake_bridge(lambda m, p: NO_REPLY if m == "slow" else {})
    with pytest.raises((RevitTimeoutError, RevitConnectionError)):
        client_for(b, timeout=0.3).call("slow")


def test_garbage_reply_is_a_protocol_error(fake_bridge, client_for):
    with pytest.raises(ProtocolError):
        client_for(fake_bridge(lambda m, p: GARBAGE)).call("x")


def test_large_payload_survives_chunked_reads(fake_bridge, client_for):
    big = {"blob": "x" * 2_000_000, "items": list(range(50_000))}
    out = client_for(fake_bridge(lambda m, p: big)).call("big")
    assert out == big


def test_unicode_round_trip(fake_bridge, client_for):
    text = "Ściana żelbetowa 300 – Ø12 ✓"
    out = client_for(fake_bridge(lambda m, p: {"got": p["value"]})).call("set_parameter", {"value": text})
    assert out == {"got": text}


def test_each_call_uses_a_fresh_connection(fake_bridge, client_for):
    b = fake_bridge(lambda m, p: p)
    c = client_for(b)
    for i in range(5):
        assert c.call("n", {"i": i}) == {"i": i}
    assert [r["params"]["i"] for r in b.requests] == [0, 1, 2, 3, 4]


def test_settings_from_env():
    s = Settings.from_env({"REVIT_MCP_HOST": "10.0.0.5", "REVIT_MCP_PORT": "9000", "REVIT_MCP_TIMEOUT": "7.5"})
    assert (s.host, s.port, s.timeout) == ("10.0.0.5", 9000, 7.5)
    assert Settings.from_env({}) == Settings()
