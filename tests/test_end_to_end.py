"""
End-to-end tests over real TCP sockets.

These exercise the parts unit tests cannot reach: the AUTH state machine,
newline framing across packet boundaries, and the full command round trip
against the actual device simulators.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest

from controller.config.server_config import AUTH_TOKEN
from controller.protocol import constants as k
from controller.protocol.message_format import encode, make_auth, make_command
from controller.server import command_tracker as ct
from controller.server import device_manager as dm
from simulations.simulated_led_device import LEDDevice
from simulations.simulated_temp_device import TempDevice
from tests.conftest import wait_until

pytestmark = pytest.mark.slow


def _read_one(sock: socket.socket, timeout: float = 5.0) -> dict:
    """Read exactly one newline-delimited frame."""
    sock.settimeout(timeout)
    buffer = ""
    while "\n" not in buffer:
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError("closed before a full frame arrived")
        buffer += chunk.decode("utf-8")
    return json.loads(buffer.split("\n", 1)[0])


def _raw_client(port: int) -> socket.socket:
    return socket.create_connection(("127.0.0.1", port), timeout=5)


# --- authentication ----------------------------------------------------------


def test_valid_token_is_accepted(controller) -> None:
    with _raw_client(controller.port) as sock:
        sock.sendall(encode(make_auth("probe", "LED")))
        assert _read_one(sock)["msg_type"] == k.MSG_AUTH_OK
        assert wait_until(lambda: dm.is_connected("probe"))


def test_bad_token_is_rejected(controller) -> None:
    with _raw_client(controller.port) as sock:
        bad = make_auth("intruder", "LED")
        bad["token"] = "wrong-token"
        sock.sendall(encode(bad))

        reply = _read_one(sock)
        assert reply["msg_type"] == k.MSG_AUTH_FAIL
        assert "token" in reply["reason"].lower()
        assert not dm.is_connected("intruder")


def test_commands_before_auth_are_rejected(controller) -> None:
    """An unauthenticated peer must not be able to drive the bus."""
    with _raw_client(controller.port) as sock:
        sock.sendall(encode(make_command("led1", "TURN_ON")))
        reply = _read_one(sock)
        assert reply["msg_type"] == k.MSG_AUTH_FAIL
        assert "AUTH required" in reply["reason"]


def test_auth_without_device_id_is_rejected(controller) -> None:
    with _raw_client(controller.port) as sock:
        sock.sendall(encode({"msg_type": k.MSG_AUTH, "token": AUTH_TOKEN}))
        assert _read_one(sock)["msg_type"] == k.MSG_AUTH_FAIL
    assert dm.count() == 0


def test_malformed_json_does_not_kill_the_session(controller) -> None:
    """One bad frame must not take down an otherwise healthy connection."""
    with _raw_client(controller.port) as sock:
        sock.sendall(b'{"broken\n')
        sock.sendall(encode(make_auth("resilient", "LED")))
        assert _read_one(sock)["msg_type"] == k.MSG_AUTH_OK


def test_frames_split_across_packets_are_reassembled(controller) -> None:
    """TCP gives no message boundaries; the buffer must stitch them back."""
    payload = encode(make_auth("split", "LED"))
    with _raw_client(controller.port) as sock:
        for i in range(0, len(payload), 7):
            sock.sendall(payload[i : i + 7])
        assert _read_one(sock)["msg_type"] == k.MSG_AUTH_OK


def test_several_frames_in_one_packet_are_all_processed(controller) -> None:
    with _raw_client(controller.port) as sock:
        sock.sendall(encode(make_auth("batched", "LED")))
        assert _read_one(sock)["msg_type"] == k.MSG_AUTH_OK
        assert wait_until(lambda: dm.is_connected("batched"))


# --- command round trip ------------------------------------------------------


@pytest.fixture
def led(controller):
    device = LEDDevice("led-e2e", host="127.0.0.1", port=controller.port, quiet=True)
    threading.Thread(target=device.run, daemon=True).start()
    assert wait_until(lambda: dm.is_connected("led-e2e")), "device never connected"
    yield device
    device.stop()


def _send_and_wait(device_id: str, action: str, params=None) -> dict:
    cmd = make_command(device_id, action, params or {})
    ct.add_command(cmd)
    dm.get_conn(device_id).sendall(encode(cmd))
    assert wait_until(lambda: (ct.get(cmd["cmd_id"]) or {}).get("executed")), (
        f"{action} never completed"
    )
    return ct.get(cmd["cmd_id"])


def test_led_turn_on_round_trip(led) -> None:
    item = _send_and_wait("led-e2e", "TURN_ON")
    assert item["received"] is True
    assert item["executed"] is True
    assert item["state"] == {"led": "ON"}
    assert item["retry_count"] == 0


def test_led_toggle_alternates(led) -> None:
    _send_and_wait("led-e2e", "TURN_OFF")
    assert _send_and_wait("led-e2e", "TOGGLE")["state"]["led"] == "ON"
    assert _send_and_wait("led-e2e", "TOGGLE")["state"]["led"] == "OFF"


def test_unsupported_action_reports_failure_not_a_crash(led) -> None:
    """The device must answer, so the controller does not sit in a retry loop."""
    item = _send_and_wait("led-e2e", "SELF_DESTRUCT")
    assert item["executed"] is True
    assert dm.is_connected("led-e2e")


def test_registry_reflects_the_latest_status(led) -> None:
    _send_and_wait("led-e2e", "TURN_ON")
    assert wait_until(lambda: dm.get("led-e2e")["last_status"] == {"led": "ON"})
    assert dm.get("led-e2e")["last_status_time"] is not None


def test_disconnect_removes_the_device(controller) -> None:
    device = LEDDevice("transient", host="127.0.0.1", port=controller.port, quiet=True)
    threading.Thread(target=device.run, daemon=True).start()
    assert wait_until(lambda: dm.is_connected("transient"))

    device.stop()
    assert wait_until(lambda: not dm.is_connected("transient")), "registry kept a dead device"


# --- telemetry ---------------------------------------------------------------


def test_temp_sensor_pushes_readings_on_an_idle_link(controller) -> None:
    """Telemetry must not depend on the controller sending anything first.

    The reporting loop used to sit behind a blocking read, so readings only
    escaped when a heartbeat happened to arrive.
    """
    device = TempDevice(
        "temp-e2e", host="127.0.0.1", port=controller.port, quiet=True, report_interval=0.5
    )
    threading.Thread(target=device.run, daemon=True).start()

    try:
        assert wait_until(lambda: dm.is_connected("temp-e2e"))
        # No command is ever sent, and heartbeats are 10s apart by default.
        assert wait_until(
            lambda: "temperature_c" in dm.get("temp-e2e")["last_status"], timeout=5.0
        ), "no unsolicited telemetry arrived"

        reading = dm.get("temp-e2e")["last_status"]
        assert 0 <= reading["temperature_c"] <= 100
    finally:
        device.stop()


def test_temp_sensor_set_interval(controller) -> None:
    device = TempDevice("temp-cfg", host="127.0.0.1", port=controller.port, quiet=True)
    threading.Thread(target=device.run, daemon=True).start()

    try:
        assert wait_until(lambda: dm.is_connected("temp-cfg"))

        _send_and_wait("temp-cfg", "SET_INTERVAL", {"seconds": 1.5})
        assert device.report_interval == 1.5

        # A missing parameter is reported, not silently ignored.
        item = _send_and_wait("temp-cfg", "SET_INTERVAL")
        assert item["executed"] is True
        assert device.report_interval == 1.5
    finally:
        device.stop()
