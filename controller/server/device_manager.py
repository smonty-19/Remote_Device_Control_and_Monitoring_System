"""
Thread-safe device registry.

Every entry holds the live socket for a device plus the metadata the CLI and
the monitoring loops need. All access goes through the module lock; callers
receive shallow copies so they can never mutate registry state by accident.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_registry: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def clear() -> None:
    with _lock:
        _registry.clear()


def register(device_id: str, conn, device_type: str, addr: tuple[str, int]) -> None:
    """Add or replace a device in the registry."""
    with _lock:
        _registry[device_id] = {
            "conn": conn,
            "type": device_type,
            "addr": addr,
            "connected_at": time.time(),
            "last_heartbeat": time.time(),
            "heartbeat_sent_at": None,
            "alive": True,
            "last_status": {},
            "last_status_time": None,
        }


def unregister(device_id: str, conn=None) -> bool:
    """Remove a device.

    When ``conn`` is given the entry is only removed if it still holds that
    exact socket. This matters when a device reconnects before the old
    handler thread has finished unwinding: without the check, the dying
    thread would evict the healthy new connection.
    """
    with _lock:
        info = _registry.get(device_id)
        if info is None:
            return False
        if conn is not None and info["conn"] is not conn:
            return False
        del _registry[device_id]
        return True


def get(device_id: str) -> dict[str, Any] | None:
    with _lock:
        info = _registry.get(device_id)
        return dict(info) if info else None


def all_devices() -> list[tuple[str, dict[str, Any]]]:
    with _lock:
        return [(device_id, dict(info)) for device_id, info in _registry.items()]


def count() -> int:
    with _lock:
        return len(_registry)


def is_connected(device_id: str) -> bool:
    with _lock:
        return device_id in _registry


def mark_heartbeat_sent(device_id: str) -> None:
    """Remember when the controller pinged a device, for RTT measurement.

    Timed on the monotonic clock: wall-clock time is only millisecond-grained
    on Windows and can jump if the system clock is adjusted.
    """
    with _lock:
        if device_id in _registry:
            _registry[device_id]["heartbeat_sent_at"] = time.perf_counter()


def clear_heartbeat_sent(device_id: str) -> None:
    """Disarm a pending ping, e.g. when the send that would follow it failed."""
    with _lock:
        if device_id in _registry:
            _registry[device_id]["heartbeat_sent_at"] = None


def update_heartbeat(device_id: str) -> float | None:
    """Record a HEARTBEAT_ACK and return the round-trip time in seconds.

    Returns ``None`` when no ping is outstanding, so an unsolicited ack never
    contributes a bogus sample.
    """
    with _lock:
        info = _registry.get(device_id)
        if info is None:
            return None

        info["last_heartbeat"] = time.time()
        info["alive"] = True

        sent_at = info.get("heartbeat_sent_at")
        if sent_at is None:
            return None
        info["heartbeat_sent_at"] = None
        return max(0.0, time.perf_counter() - sent_at)


def update_status(device_id: str, state: dict[str, Any]) -> None:
    with _lock:
        if device_id in _registry:
            _registry[device_id]["last_status"] = state
            _registry[device_id]["last_status_time"] = time.time()


def mark_stale(device_id: str) -> None:
    with _lock:
        if device_id in _registry:
            _registry[device_id]["alive"] = False


def get_conn(device_id: str):
    with _lock:
        info = _registry.get(device_id)
        return info["conn"] if info else None
