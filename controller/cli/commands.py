"""CLI command handlers."""

from __future__ import annotations

import json
import time
from typing import Any

from controller.protocol.message_format import encode, make_command
from controller.server import command_tracker as ct
from controller.server import device_manager as dm
from controller.server import latency_monitor as lm
from controller.server.logger import log


def _coerce(value: str) -> Any:
    """Turn a CLI string into the most specific JSON type that fits."""
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_params(items: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            print(f"Ignoring malformed parameter {item!r}; expected KEY=VALUE.")
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            print(f"Ignoring parameter with empty key: {item!r}")
            continue
        params[key] = _coerce(value.strip())
    return params


def list_devices() -> None:
    devices = dm.all_devices()
    if not devices:
        print("No connected devices.")
        return

    print(f"{'device_id':<12} {'type':<14} {'alive':<6} {'hb_age':>8}  last_status")
    print("-" * 72)
    for device_id, info in sorted(devices):
        age = lm.age_since(info["last_heartbeat"])
        alive = "yes" if info["alive"] else "NO"
        print(
            f"{device_id:<12} {info['type']:<14} {alive:<6} {age:>7.1f}s  "
            f"{info['last_status']}"
        )


def show_status(device_id: str) -> None:
    info = dm.get(device_id)
    if not info:
        print(f"{device_id} is not connected.")
        return

    print(f"Device:        {device_id}")
    print(f"Type:          {info['type']}")
    print(f"Alive:         {info['alive']}")
    print(f"Address:       {info['addr'][0]}:{info['addr'][1]}")
    print(f"Connected for: {lm.age_since(info['connected_at']):.1f}s")
    print(f"Heartbeat age: {lm.age_since(info['last_heartbeat']):.1f}s")
    print(f"Last status:   {info['last_status']}")

    for channel in lm.CHANNELS:
        summary = lm.stats(channel, device_id)
        if summary:
            print(
                f"  {channel:<10} n={summary['count']:<5} "
                f"mean={summary['mean_ms']:.3f}ms p95={summary['p95_ms']:.3f}ms"
            )


def send_command(device_id: str, action: str, params: dict[str, Any]) -> str | None:
    """Send one command. Returns its cmd_id, or None if it could not be sent."""
    conn = dm.get_conn(device_id)
    if conn is None:
        print(f"{device_id} is not connected.")
        return None

    cmd = make_command(device_id, action, params)
    ct.add_command(cmd)

    try:
        conn.sendall(encode(cmd))
    except OSError as exc:
        ct.mark_failed(cmd["cmd_id"], f"send failed: {exc}")
        print(f"Failed to send command: {exc}")
        return None

    log("CMD", f"sent {cmd['cmd_id']} to {device_id} ({action})", "green")
    print(f"Command sent. cmd_id={cmd['cmd_id']}")
    return cmd["cmd_id"]


def show_pending() -> None:
    items = ct.snapshot()
    if not items:
        print("No tracked commands.")
        return

    print(
        f"{'cmd_id':<10} {'device':<12} {'action':<14} {'recv':<5} {'exec':<5} "
        f"{'retry':>5} {'ack_ms':>9} {'exec_ms':>9}  note"
    )
    print("-" * 92)
    for cmd_id, info in sorted(items.items(), key=lambda kv: kv[1]["timestamp"]):
        ack = f"{info['ack_latency'] * 1000:.3f}" if info["ack_latency"] else "-"
        exe = f"{info['execute_latency'] * 1000:.3f}" if info["execute_latency"] else "-"
        note = info["failure_reason"] or ""
        print(
            f"{cmd_id:<10} {info['device_id']:<12} {info['command']['action']:<14} "
            f"{str(info['received']):<5} {str(info['executed']):<5} "
            f"{info['retry_count']:>5} {ack:>9} {exe:>9}  {note}"
        )


def show_stats() -> None:
    counts = ct.counts()
    print(f"Connected devices: {dm.count()}")
    print(f"Commands tracked:  {counts['total']}")
    print(f"  executed:        {counts['executed']}")
    print(f"  in flight:       {counts['in_flight']}")
    print(f"  failed:          {counts['failed']}")
    print(f"  needed a retry:  {counts['retried']}")
    if counts["total"]:
        rate = 100.0 * counts["executed"] / counts["total"]
        print(f"  success rate:    {rate:.1f}%")


def show_latency(per_device: bool = False, reset: bool = False, as_json: bool = False) -> None:
    if reset:
        lm.clear()
        print("Latency samples cleared.")
        return

    if as_json:
        print(json.dumps(lm.all_stats(), indent=2))
        return

    print(lm.format_table(per_device=per_device))


def run_bench(device_id: str, action: str, count: int, interval: float) -> None:
    """Fire ``count`` commands at one device and summarise the result."""
    if not dm.is_connected(device_id):
        print(f"{device_id} is not connected.")
        return
    if count < 1:
        print("Count must be at least 1.")
        return

    print(f"Sending {count} x {action} to {device_id} (interval {interval}s) ...")
    sent: list[str] = []
    started = time.time()

    for _ in range(count):
        conn = dm.get_conn(device_id)
        if conn is None:
            print("Device disconnected mid-run; stopping.")
            break

        cmd = make_command(device_id, action, {})
        ct.add_command(cmd)
        try:
            conn.sendall(encode(cmd))
        except OSError as exc:
            ct.mark_failed(cmd["cmd_id"], f"send failed: {exc}")
            print(f"Send failed: {exc}")
            break
        sent.append(cmd["cmd_id"])
        if interval > 0:
            time.sleep(interval)

    # Give the tail of the run a moment to come back before summarising.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if all((ct.get(c) or {}).get("executed") for c in sent):
            break
        time.sleep(0.05)

    elapsed = time.time() - started
    done = sum(1 for c in sent if (ct.get(c) or {}).get("executed"))
    print(f"\nSent {len(sent)}, completed {done}, in {elapsed:.2f}s")
    if elapsed > 0:
        print(f"Throughput: {done / elapsed:.1f} commands/sec")
    print()
    print(lm.format_table())


def run_command(args) -> None:
    if args.subcommand == "list":
        list_devices()
    elif args.subcommand == "status":
        show_status(args.device_id)
    elif args.subcommand == "send":
        send_command(args.device_id, args.action, _parse_params(args.param))
    elif args.subcommand == "pending":
        show_pending()
    elif args.subcommand == "stats":
        show_stats()
    elif args.subcommand == "latency":
        show_latency(per_device=args.per_device, reset=args.reset, as_json=args.json)
    elif args.subcommand == "bench":
        run_bench(args.device_id, args.action, args.count, args.interval)
    else:
        print("Unknown command.")
