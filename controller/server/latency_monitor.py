"""
Latency measurement and aggregation.

Three latencies are tracked, all measured on the controller so no clock
synchronisation with the device is required:

- ``ack``       COMMAND sent -> RECEIVED_ACK back. Pure network round trip.
- ``execute``   COMMAND sent -> STATUS back. Network round trip + the time
                the device spent actually performing the action.
- ``heartbeat`` HEARTBEAT sent -> HEARTBEAT_ACK back. Idle-path round trip.

Samples are kept in bounded ring buffers so a long-running controller uses a
fixed amount of memory.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any

# How many samples to retain per (channel, device) pair.
MAX_SAMPLES = 1000

ACK = "ack"
EXECUTE = "execute"
HEARTBEAT = "heartbeat"

CHANNELS = (ACK, EXECUTE, HEARTBEAT)

_lock = threading.Lock()
# {channel: {device_id: deque[float milliseconds]}}
_samples: dict[str, dict[str, deque[float]]] = {c: {} for c in CHANNELS}


def age_since(start_time: float) -> float:
    """Seconds elapsed since ``start_time``, clamped at zero.

    Used for "how stale is this?" displays, not for latency statistics.
    """
    return max(0.0, time.time() - start_time)


def record(channel: str, device_id: str, seconds: float) -> None:
    """Record one latency sample, in seconds, for a device."""
    if channel not in _samples or seconds < 0:
        return
    with _lock:
        bucket = _samples[channel].setdefault(device_id, deque(maxlen=MAX_SAMPLES))
        bucket.append(seconds * 1000.0)


def clear() -> None:
    with _lock:
        for channel in _samples:
            _samples[channel].clear()


def _percentile(ordered: list[float], pct: float) -> float:
    """Nearest-rank percentile of an already-sorted list.

    Uses ``ceil``, the textbook definition: the p-th percentile is the
    smallest value at or below which at least p% of samples fall. Rounding
    instead would use banker's rounding and land a half-rank on the wrong
    side (``round(2.5) == 2``).
    """
    if not ordered:
        return 0.0
    rank = max(1, min(len(ordered), math.ceil(pct / 100.0 * len(ordered))))
    return ordered[rank - 1]


def _summarise(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    count = len(ordered)
    return {
        "count": count,
        "min_ms": round(ordered[0], 3),
        "mean_ms": round(sum(ordered) / count, 3),
        "p50_ms": round(_percentile(ordered, 50), 3),
        "p95_ms": round(_percentile(ordered, 95), 3),
        "p99_ms": round(_percentile(ordered, 99), 3),
        "max_ms": round(ordered[-1], 3),
    }


def stats(channel: str, device_id: str | None = None) -> dict[str, Any] | None:
    """Summary statistics for a channel, optionally narrowed to one device.

    Returns ``None`` when no samples have been collected yet.
    """
    with _lock:
        by_device = _samples.get(channel, {})
        if device_id is not None:
            values = list(by_device.get(device_id, ()))
        else:
            values = [v for bucket in by_device.values() for v in bucket]

    if not values:
        return None
    return _summarise(values)


def all_stats() -> dict[str, dict[str, Any]]:
    """Summary for every channel that has at least one sample."""
    out: dict[str, dict[str, Any]] = {}
    for channel in CHANNELS:
        summary = stats(channel)
        if summary is not None:
            out[channel] = summary
    return out


def device_ids() -> list[str]:
    with _lock:
        seen: set[str] = set()
        for by_device in _samples.values():
            seen.update(by_device)
    return sorted(seen)


def format_table(per_device: bool = False) -> str:
    """Render collected latencies as a fixed-width table for the CLI/README."""
    header = (
        f"{'channel':<10} {'device':<12} {'n':>6} {'min':>9} {'mean':>9} "
        f"{'p50':>9} {'p95':>9} {'p99':>9} {'max':>9}"
    )
    rows = [header, "-" * len(header)]

    def add_row(channel: str, label: str, summary: dict[str, Any]) -> None:
        rows.append(
            f"{channel:<10} {label:<12} {summary['count']:>6} "
            f"{summary['min_ms']:>9.3f} {summary['mean_ms']:>9.3f} "
            f"{summary['p50_ms']:>9.3f} {summary['p95_ms']:>9.3f} "
            f"{summary['p99_ms']:>9.3f} {summary['max_ms']:>9.3f}"
        )

    found = False
    for channel in CHANNELS:
        if per_device:
            for dev in device_ids():
                summary = stats(channel, dev)
                if summary:
                    add_row(channel, dev, summary)
                    found = True
        summary = stats(channel)
        if summary:
            add_row(channel, "ALL", summary)
            found = True

    if not found:
        return "No latency samples yet. Send a command or wait for a heartbeat."
    rows.append("")
    rows.append("All values in milliseconds. Measured controller-side; no clock sync needed.")
    return "\n".join(rows)
