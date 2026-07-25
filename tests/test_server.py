"""Device registry and command tracker tests."""

from __future__ import annotations

import time

from controller.server import command_tracker as ct
from controller.server import device_manager as dm


class DummyConn:
    def __init__(self):
        self.sent = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)


def _cmd(cmd_id="abcd1234", device_id="led-1", action="TURN_ON"):
    return {
        "cmd_id": cmd_id,
        "device_id": device_id,
        "action": action,
        "params": {},
        "timestamp": time.time(),
    }


# --- device registry ---------------------------------------------------------


def test_register_and_unregister() -> None:
    conn = DummyConn()
    dm.register("led-1", conn, "LED", ("127.0.0.1", 10001))

    assert dm.is_connected("led-1")
    assert dm.count() == 1
    info = dm.get("led-1")
    assert info["type"] == "LED"
    assert info["alive"] is True

    assert dm.unregister("led-1") is True
    assert not dm.is_connected("led-1")
    assert dm.unregister("led-1") is False


def test_get_returns_a_copy() -> None:
    """Callers must not be able to mutate registry state by accident."""
    dm.register("led-1", DummyConn(), "LED", ("127.0.0.1", 1))
    dm.get("led-1")["type"] = "TAMPERED"
    assert dm.get("led-1")["type"] == "LED"


def test_reconnect_does_not_evict_the_new_connection() -> None:
    """The old handler thread must not unregister a device that reconnected.

    Without the connection check, a slow-unwinding handler would kill the
    healthy socket that replaced it.
    """
    old_conn, new_conn = DummyConn(), DummyConn()
    dm.register("led-1", old_conn, "LED", ("127.0.0.1", 1))
    dm.register("led-1", new_conn, "LED", ("127.0.0.1", 2))

    assert dm.unregister("led-1", old_conn) is False
    assert dm.is_connected("led-1")
    assert dm.get_conn("led-1") is new_conn

    assert dm.unregister("led-1", new_conn) is True
    assert not dm.is_connected("led-1")


def test_status_and_staleness_updates() -> None:
    dm.register("temp-1", DummyConn(), "TEMP_SENSOR", ("127.0.0.1", 1))
    dm.update_status("temp-1", {"temperature_c": 22.5})
    assert dm.get("temp-1")["last_status"]["temperature_c"] == 22.5

    dm.mark_stale("temp-1")
    assert dm.get("temp-1")["alive"] is False

    dm.update_heartbeat("temp-1")
    assert dm.get("temp-1")["alive"] is True


def test_updates_to_unknown_devices_are_ignored() -> None:
    dm.update_status("ghost", {"x": 1})
    dm.update_heartbeat("ghost")
    dm.mark_stale("ghost")
    assert dm.get_conn("ghost") is None
    assert dm.count() == 0


# --- command tracker ---------------------------------------------------------


def test_add_and_mark_lifecycle() -> None:
    ct.add_command(_cmd())
    assert ct.get("abcd1234") is not None

    ct.mark_received("abcd1234")
    ct.mark_executed("abcd1234", {"led": "ON"})

    item = ct.get("abcd1234")
    assert item["received"] is True
    assert item["executed"] is True
    assert item["state"] == {"led": "ON"}
    assert item["ack_latency"] is not None
    assert item["execute_latency"] is not None


def test_pending_items_excludes_finished_commands() -> None:
    """The retry loop iterates this; finished work must not be resent."""
    ct.add_command(_cmd("aaa"))
    ct.add_command(_cmd("bbb"))
    ct.add_command(_cmd("ccc"))

    ct.mark_executed("bbb")
    ct.mark_failed("ccc", "max retries reached")

    assert [k for k, _ in ct.pending_items()] == ["aaa"]
    # snapshot() still shows everything, for the operator's `pending` view.
    assert set(ct.snapshot()) == {"aaa", "bbb", "ccc"}


def test_counts_summarise_delivery_state() -> None:
    ct.add_command(_cmd("aaa"))
    ct.add_command(_cmd("bbb"))
    ct.add_command(_cmd("ccc"))
    ct.mark_executed("aaa")
    ct.mark_failed("bbb", "device disconnected")
    ct.bump_retry("ccc")

    counts = ct.counts()
    assert counts["total"] == 3
    assert counts["executed"] == 1
    assert counts["failed"] == 1
    assert counts["in_flight"] == 1
    assert counts["retried"] == 1


def test_retry_increments_and_reschedules() -> None:
    ct.add_command(_cmd())
    before = ct.get("abcd1234")["last_sent"]
    time.sleep(0.01)
    ct.bump_retry("abcd1234")

    item = ct.get("abcd1234")
    assert item["retry_count"] == 1
    assert item["last_sent"] >= before


def test_prune_drops_only_old_finished_commands() -> None:
    ct.add_command(_cmd("old"))
    ct.add_command(_cmd("recent"))
    ct.add_command(_cmd("inflight"))
    ct.mark_executed("old")
    ct.mark_executed("recent")

    # Backdate one completion past the retention window.
    ct._pending["old"]["executed_at"] = time.time() - ct.COMPLETED_RETENTION_SEC - 1

    assert ct.prune() == 1
    assert set(ct.snapshot()) == {"recent", "inflight"}


def test_prune_enforces_a_hard_ceiling() -> None:
    """In-flight commands alone must not be able to exhaust memory."""
    for i in range(ct.MAX_TRACKED + 10):
        ct.add_command(_cmd(f"cmd{i}"))
    ct.prune()
    assert len(ct.snapshot()) == ct.MAX_TRACKED
