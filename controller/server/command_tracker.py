"""
In-flight command bookkeeping.

Tracks every COMMAND the controller sends until the device acknowledges and
executes it, drives the retry loop, and feeds send->ack / send->execute
samples to the latency monitor.

Completed commands are retained briefly so the operator can still inspect
them with ``pending``, then pruned so a long-running controller does not grow
without bound.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from controller.server import latency_monitor as lm

# Keep finished commands visible this long before pruning them.
COMPLETED_RETENTION_SEC = 300
# Hard ceiling on tracked commands, regardless of age.
MAX_TRACKED = 5000

_lock = threading.Lock()
_pending: dict[str, dict[str, Any]] = {}


def clear() -> None:
    with _lock:
        _pending.clear()


def add_command(cmd: dict[str, Any]) -> None:
    now = time.time()
    with _lock:
        _pending[cmd["cmd_id"]] = {
            "command": cmd,
            "device_id": cmd["device_id"],
            "timestamp": now,
            "last_sent": now,
            # Wall-clock time is what the retry loop and the UI want, but it is
            # only millisecond-resolution on Windows and can jump if the system
            # clock is adjusted. Latency is timed off the monotonic clock.
            "last_sent_perf": time.perf_counter(),
            "retry_count": 0,
            "received": False,
            "executed": False,
            "failed": False,
            "state": None,
            "failure_reason": None,
            "received_at": None,
            "executed_at": None,
            "failed_at": None,
            "ack_latency": None,
            "execute_latency": None,
        }


def mark_received(cmd_id: str) -> float | None:
    """Record a RECEIVED_ACK and return the send->ack latency in seconds."""
    with _lock:
        item = _pending.get(cmd_id)
        if item is None or item["received"]:
            return None

        item["received"] = True
        item["received_at"] = time.time()
        latency = max(0.0, time.perf_counter() - item["last_sent_perf"])
        item["ack_latency"] = latency
        device_id = item["device_id"]

    lm.record(lm.ACK, device_id, latency)
    return latency


def mark_executed(cmd_id: str, state: dict[str, Any] | None = None) -> float | None:
    """Record a STATUS reply and return the send->execute latency in seconds."""
    with _lock:
        item = _pending.get(cmd_id)
        if item is None or item["executed"]:
            return None

        item["executed"] = True
        item["executed_at"] = time.time()
        latency = max(0.0, time.perf_counter() - item["last_sent_perf"])
        item["execute_latency"] = latency
        device_id = item["device_id"]
        if state is not None:
            item["state"] = state

    lm.record(lm.EXECUTE, device_id, latency)
    return latency


def mark_failed(cmd_id: str, reason: str) -> None:
    with _lock:
        item = _pending.get(cmd_id)
        if item is not None:
            item["failed"] = True
            item["failure_reason"] = reason
            item["failed_at"] = time.time()


def bump_retry(cmd_id: str) -> None:
    with _lock:
        item = _pending.get(cmd_id)
        if item is not None:
            item["retry_count"] += 1
            item["last_sent"] = time.time()
            item["last_sent_perf"] = time.perf_counter()


def get(cmd_id: str) -> dict[str, Any] | None:
    with _lock:
        item = _pending.get(cmd_id)
        return dict(item) if item else None


def snapshot() -> dict[str, dict[str, Any]]:
    with _lock:
        return {k: dict(v) for k, v in _pending.items()}


def pending_items() -> list[tuple[str, dict[str, Any]]]:
    """Commands still awaiting execution or a terminal failure."""
    with _lock:
        return [
            (k, dict(v))
            for k, v in _pending.items()
            if not v["executed"] and not v["failed"]
        ]


def counts() -> dict[str, int]:
    with _lock:
        total = len(_pending)
        executed = sum(1 for v in _pending.values() if v["executed"])
        failed = sum(1 for v in _pending.values() if v["failed"])
        retried = sum(1 for v in _pending.values() if v["retry_count"] > 0)
    return {
        "total": total,
        "executed": executed,
        "failed": failed,
        "retried": retried,
        "in_flight": total - executed - failed,
    }


def prune(now: float | None = None) -> int:
    """Drop finished commands older than the retention window.

    Returns the number of entries removed.
    """
    now = time.time() if now is None else now
    with _lock:
        stale = [
            cmd_id
            for cmd_id, item in _pending.items()
            if (item["executed"] or item["failed"])
            and now - (item["executed_at"] or item["failed_at"] or item["timestamp"])
            > COMPLETED_RETENTION_SEC
        ]
        for cmd_id in stale:
            del _pending[cmd_id]

        # Safety valve: if the retention window alone cannot keep the table
        # bounded (e.g. a flood of in-flight commands), drop the oldest.
        overflow = len(_pending) - MAX_TRACKED
        if overflow > 0:
            oldest = sorted(_pending, key=lambda k: _pending[k]["timestamp"])
            for cmd_id in oldest[:overflow]:
                del _pending[cmd_id]
            return len(stale) + overflow

    return len(stale)
