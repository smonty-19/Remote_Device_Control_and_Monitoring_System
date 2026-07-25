"""
Latency measurement tests.

Split into two layers:

- Unit tests over the statistics themselves, which are deterministic.
- End-to-end tests that drive real sockets and assert the pipeline produces
  plausible samples. These assert on *shape*, not on absolute timings, since
  wall-clock thresholds make for tests that fail on loaded CI machines.
"""

from __future__ import annotations

import threading
import time

import pytest

from controller.protocol.message_format import encode, make_command
from controller.server import command_tracker as ct
from controller.server import device_manager as dm
from controller.server import latency_monitor as lm
from simulations.simulated_led_device import LEDDevice
from tests.conftest import wait_until


# --- statistics --------------------------------------------------------------


def test_no_samples_yields_none() -> None:
    assert lm.stats(lm.ACK) is None
    assert lm.all_stats() == {}


def test_summary_statistics_are_correct() -> None:
    # 1..100 ms, so every percentile has an exact expected value.
    for i in range(1, 101):
        lm.record(lm.ACK, "dev", i / 1000.0)

    s = lm.stats(lm.ACK)
    assert s["count"] == 100
    assert s["min_ms"] == pytest.approx(1.0)
    assert s["max_ms"] == pytest.approx(100.0)
    assert s["mean_ms"] == pytest.approx(50.5)
    assert s["p50_ms"] == pytest.approx(50.0)
    assert s["p95_ms"] == pytest.approx(95.0)
    assert s["p99_ms"] == pytest.approx(99.0)


def test_percentiles_are_order_independent() -> None:
    for value in [5, 1, 4, 2, 3]:
        lm.record(lm.EXECUTE, "dev", value / 1000.0)
    s = lm.stats(lm.EXECUTE)
    assert s["min_ms"] == pytest.approx(1.0)
    assert s["max_ms"] == pytest.approx(5.0)
    assert s["p50_ms"] == pytest.approx(3.0)


def test_samples_are_separated_by_device_and_channel() -> None:
    lm.record(lm.ACK, "led1", 0.001)
    lm.record(lm.ACK, "led2", 0.009)
    lm.record(lm.EXECUTE, "led1", 0.005)

    assert lm.stats(lm.ACK, "led1")["count"] == 1
    assert lm.stats(lm.ACK, "led2")["mean_ms"] == pytest.approx(9.0)
    # Omitting device_id aggregates across devices.
    assert lm.stats(lm.ACK)["count"] == 2
    assert lm.stats(lm.EXECUTE)["count"] == 1
    assert lm.device_ids() == ["led1", "led2"]


def test_sample_buffer_is_bounded() -> None:
    """A long-running controller must not grow without bound."""
    for i in range(lm.MAX_SAMPLES + 500):
        lm.record(lm.ACK, "dev", i / 1000.0)
    assert lm.stats(lm.ACK)["count"] == lm.MAX_SAMPLES


def test_negative_and_unknown_samples_are_rejected() -> None:
    lm.record(lm.ACK, "dev", -1.0)
    lm.record("not-a-channel", "dev", 0.5)
    assert lm.all_stats() == {}


def test_format_table_handles_empty_and_populated() -> None:
    assert "No latency samples" in lm.format_table()
    lm.record(lm.ACK, "dev", 0.002)
    table = lm.format_table(per_device=True)
    assert "ack" in table and "dev" in table and "p95" in table


# --- tracker integration -----------------------------------------------------


def test_tracker_feeds_the_latency_monitor() -> None:
    cmd = make_command("led1", "TURN_ON")
    ct.add_command(cmd)

    ack = ct.mark_received(cmd["cmd_id"])
    execute = ct.mark_executed(cmd["cmd_id"], {"led": "ON"})

    assert ack is not None and ack >= 0
    assert execute is not None and execute >= ack
    assert lm.stats(lm.ACK, "led1")["count"] == 1
    assert lm.stats(lm.EXECUTE, "led1")["count"] == 1


def test_duplicate_acks_do_not_double_count() -> None:
    """A device that retransmits must not skew the distribution."""
    cmd = make_command("led1", "TURN_ON")
    ct.add_command(cmd)

    assert ct.mark_received(cmd["cmd_id"]) is not None
    assert ct.mark_received(cmd["cmd_id"]) is None
    assert ct.mark_executed(cmd["cmd_id"]) is not None
    assert ct.mark_executed(cmd["cmd_id"]) is None

    assert lm.stats(lm.ACK, "led1")["count"] == 1
    assert lm.stats(lm.EXECUTE, "led1")["count"] == 1


def test_acks_for_unknown_commands_are_ignored() -> None:
    assert ct.mark_received("deadbeef") is None
    assert ct.mark_executed("deadbeef") is None
    assert lm.all_stats() == {}


def test_retry_resets_the_latency_clock() -> None:
    """Latency is measured from the most recent send, not the first."""
    cmd = make_command("led1", "TURN_ON")
    ct.add_command(cmd)
    time.sleep(0.05)
    ct.bump_retry(cmd["cmd_id"])

    latency = ct.mark_received(cmd["cmd_id"])
    assert latency < 0.05


# --- end to end --------------------------------------------------------------


@pytest.mark.slow
def test_latency_measured_over_a_real_socket(controller) -> None:
    """Full path: controller -> TCP -> device -> TCP -> controller."""
    device = LEDDevice("e2e-led", host="127.0.0.1", port=controller.port, quiet=True)
    threading.Thread(target=device.run, daemon=True).start()

    try:
        assert wait_until(lambda: dm.is_connected("e2e-led")), "device never connected"

        sent = []
        for _ in range(20):
            cmd = make_command("e2e-led", "TOGGLE")
            ct.add_command(cmd)
            dm.get_conn("e2e-led").sendall(encode(cmd))
            assert wait_until(
                lambda c=cmd["cmd_id"]: (ct.get(c) or {}).get("executed")
            ), "command never completed"
            sent.append(cmd["cmd_id"])

        ack = lm.stats(lm.ACK, "e2e-led")
        execute = lm.stats(lm.EXECUTE, "e2e-led")

        assert ack["count"] == 20
        assert execute["count"] == 20
        # Ordering invariants hold regardless of how fast the machine is.
        assert 0 < ack["min_ms"] <= ack["p50_ms"] <= ack["p95_ms"] <= ack["max_ms"]
        assert execute["p50_ms"] >= ack["p50_ms"]
        # A loopback round trip that takes over a second means something broke.
        assert execute["p95_ms"] < 1000
    finally:
        device.stop()


@pytest.mark.slow
def test_heartbeat_round_trip_is_recorded(controller) -> None:
    from controller.protocol.message_format import make_heartbeat

    device = LEDDevice("hb-led", host="127.0.0.1", port=controller.port, quiet=True)
    threading.Thread(target=device.run, daemon=True).start()

    try:
        assert wait_until(lambda: dm.is_connected("hb-led"))

        for _ in range(5):
            dm.mark_heartbeat_sent("hb-led")
            dm.get_conn("hb-led").sendall(encode(make_heartbeat("controller")))
            time.sleep(0.05)

        # The connection handler records the RTT when HEARTBEAT_ACK arrives.
        assert wait_until(lambda: lm.stats(lm.HEARTBEAT, "hb-led") is not None)
        assert lm.stats(lm.HEARTBEAT, "hb-led")["min_ms"] > 0
    finally:
        device.stop()


def test_unsolicited_heartbeat_ack_produces_no_sample() -> None:
    """An ack with no outstanding ping would otherwise record a bogus RTT."""
    dm.register("led1", object(), "LED", ("127.0.0.1", 1234))
    assert dm.update_heartbeat("led1") is None
    dm.mark_heartbeat_sent("led1")
    assert dm.update_heartbeat("led1") is not None
    # The pending ping is consumed, so a second ack yields nothing.
    assert dm.update_heartbeat("led1") is None


class _InstantAckConn:
    """A peer that acks inside ``sendall``, before the send call returns.

    Models the loopback case where the device's reply is processed by the
    session thread while the heartbeat thread is still inside its send.
    """

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.rtts: list[float | None] = []

    def sendall(self, data: bytes) -> None:
        self.rtts.append(dm.update_heartbeat(self.device_id))


def test_heartbeat_timer_is_armed_before_the_send() -> None:
    """Regression: an ack that lands during the send must still be measured.

    Arming after the send loses this sample and strands a pending ping, so
    the next ack is measured against a stale timestamp and reports one whole
    heartbeat interval as its RTT.
    """
    from controller.server.server import ping_device

    conn = _InstantAckConn("fast-led")
    dm.register("fast-led", conn, "LED", ("127.0.0.1", 1))

    assert ping_device("fast-led", conn) is True

    # The in-flight ack was measured rather than dropped...
    assert conn.rtts == [pytest.approx(0, abs=0.5)]
    assert conn.rtts[0] is not None
    # ...and no pending ping was left behind to poison the next round.
    assert dm.get("fast-led")["heartbeat_sent_at"] is None


def test_stranded_ping_cannot_report_a_full_interval() -> None:
    """Two ping/ack rounds must not produce an interval-sized sample."""
    from controller.server.server import ping_device

    conn = _InstantAckConn("fast-led")
    dm.register("fast-led", conn, "LED", ("127.0.0.1", 1))

    ping_device("fast-led", conn)
    time.sleep(0.05)  # stands in for the gap between heartbeat cycles
    ping_device("fast-led", conn)

    assert all(rtt is not None for rtt in conn.rtts)
    # With the bug the second sample would be ~the sleep above, not ~0.
    assert max(conn.rtts) < 0.01


def test_failed_send_disarms_the_timer() -> None:
    """A ping that never left must not be measured when an ack arrives later."""
    from controller.server.server import ping_device

    class DeadConn:
        def sendall(self, data: bytes) -> None:
            raise OSError("broken pipe")

    dm.register("dead-led", DeadConn(), "LED", ("127.0.0.1", 1))
    assert ping_device("dead-led", DeadConn()) is False
    assert dm.get("dead-led")["heartbeat_sent_at"] is None
    assert dm.update_heartbeat("dead-led") is None
