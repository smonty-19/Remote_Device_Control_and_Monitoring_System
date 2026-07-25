"""Protocol serialization and message construction tests."""

from __future__ import annotations

import json

import pytest

from controller.protocol import constants as k
from controller.protocol.message_format import (
    decode,
    encode,
    make_auth,
    make_auth_fail,
    make_auth_ok,
    make_command,
    make_heartbeat,
    make_heartbeat_ack,
    make_received_ack,
    make_status,
    new_cmd_id,
)


def test_encode_decode_roundtrip() -> None:
    msg = make_command("led1", "TURN_ON", {"duration": 2})
    parsed = decode(encode(msg).decode("utf-8"))
    assert parsed["msg_type"] == k.MSG_COMMAND
    assert parsed["device_id"] == "led1"
    assert parsed["action"] == "TURN_ON"
    assert parsed["params"]["duration"] == 2


def test_encoded_frames_are_newline_terminated() -> None:
    """Framing depends on exactly one trailing newline and none inside."""
    raw = encode(make_command("led1", "TURN_ON"))
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1


def test_multiple_frames_split_cleanly() -> None:
    """Two messages in one TCP read must decode as two messages."""
    stream = (encode(make_command("led1", "TURN_ON")) + encode(make_heartbeat("controller")))
    lines = stream.decode("utf-8").strip().split("\n")
    assert len(lines) == 2
    assert decode(lines[0])["msg_type"] == k.MSG_COMMAND
    assert decode(lines[1])["msg_type"] == k.MSG_HEARTBEAT


def test_unicode_survives_the_round_trip() -> None:
    msg = make_status("temp1", {"label": "café ✓ 温度"})
    assert decode(encode(msg).decode("utf-8"))["state"]["label"] == "café ✓ 温度"


def test_command_ids_are_unique() -> None:
    assert len({new_cmd_id() for _ in range(1000)}) == 1000


def test_status_includes_cmd_id_when_provided() -> None:
    msg = make_status("temp1", {"temperature_c": 30.2}, cmd_id="abcd1234", success=True)
    assert msg["cmd_id"] == "abcd1234"
    assert msg["success"] is True
    assert msg["state"]["temperature_c"] == 30.2


def test_status_omits_optional_fields_when_absent() -> None:
    msg = make_status("temp1", {"temperature_c": 30.2})
    assert "cmd_id" not in msg
    assert "message" not in msg


def test_command_defaults_to_empty_params() -> None:
    assert make_command("led1", "TOGGLE")["params"] == {}


@pytest.mark.parametrize(
    "builder,expected",
    [
        (lambda: make_auth("led1", "LED"), k.MSG_AUTH),
        (lambda: make_auth_ok("led1"), k.MSG_AUTH_OK),
        (lambda: make_auth_fail("nope"), k.MSG_AUTH_FAIL),
        (lambda: make_command("led1", "TURN_ON"), k.MSG_COMMAND),
        (lambda: make_received_ack("abcd", "led1"), k.MSG_RECEIVED_ACK),
        (lambda: make_status("led1", {}), k.MSG_STATUS),
        (lambda: make_heartbeat("controller"), k.MSG_HEARTBEAT),
        (lambda: make_heartbeat_ack("led1"), k.MSG_HEARTBEAT_ACK),
    ],
)
def test_every_builder_sets_type_and_timestamp(builder, expected) -> None:
    msg = builder()
    assert msg["msg_type"] == expected
    assert isinstance(msg["timestamp"], float)


def test_auth_carries_the_configured_token() -> None:
    from controller.config.server_config import AUTH_TOKEN

    assert make_auth("led1", "LED")["token"] == AUTH_TOKEN


def test_decode_rejects_malformed_json() -> None:
    with pytest.raises(json.JSONDecodeError):
        decode("{not json")
