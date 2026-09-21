"""The wire format, encoded and decoded without any socket."""

import json

import pytest

from revit_mcp.protocol import PROTOCOL_VERSION, BridgeError, ProtocolError, Request, Response


def test_request_is_one_json_line_with_version_and_id():
    raw = Request(method="get_element", params={"element_id": 42}).encode()
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1
    obj = json.loads(raw)
    assert obj["v"] == PROTOCOL_VERSION
    assert obj["method"] == "get_element"
    assert obj["params"] == {"element_id": 42}
    assert isinstance(obj["id"], str) and obj["id"]


def test_request_carries_client_timeout_only_when_given():
    assert "timeout" not in json.loads(Request(method="ping").encode())
    assert json.loads(Request(method="ping", timeout=12.5).encode())["timeout"] == 12.5


def test_request_ids_are_unique():
    assert Request(method="ping").id != Request(method="ping").id


def test_request_escapes_non_ascii_and_round_trips():
    raw = Request(method="set_parameter", params={"value": "Stahlbeton – Wand"}).encode()
    assert raw.isascii()  # the IronPython side must never see raw non-ASCII bytes
    assert json.loads(raw)["params"]["value"] == "Stahlbeton – Wand"


def test_success_response_decodes_result():
    r = Response.decode(b'{"id": "abc", "ok": true, "result": {"count": 3}}\n')
    assert r.ok and r.id == "abc" and r.result == {"count": 3}
    assert r.unwrap() == {"count": 3}


def test_error_response_decodes_into_bridge_error():
    r = Response.decode(
        '{"id": "abc", "ok": false, "error": {"code": "not_found", "message": "no element 9", "details": null}}'
    )
    assert not r.ok
    assert isinstance(r.error, BridgeError)
    assert r.error.code == "not_found"
    with pytest.raises(BridgeError) as excinfo:
        r.unwrap()
    assert excinfo.value.message == "no element 9"
    assert "not_found" in str(excinfo.value)


def test_error_without_structured_payload_is_still_an_error():
    r = Response.decode('{"id": "x", "ok": false, "error": "boom"}')
    assert r.error is not None and r.error.code == "bridge_error" and "boom" in r.error.message


@pytest.mark.parametrize(
    "line",
    [b"", b"   \n", b"not json\n", b"[1,2,3]\n", b'{"id": "x"}\n', b"\xff\xfe\n"],
)
def test_malformed_lines_raise_protocol_error(line):
    with pytest.raises(ProtocolError):
        Response.decode(line)
