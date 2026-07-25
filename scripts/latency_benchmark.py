"""
End-to-end latency and throughput benchmark.

Starts a controller listener in-process, attaches N simulated devices over
real TCP sockets, and measures the full command path: JSON encode, kernel
send, device parse, device work, device reply, controller parse.

Two phases are measured separately, because conflating them is the most
common way to report a misleading latency number:

1. **Latency (closed loop)** - one command in flight at a time. Each send
   waits for its own STATUS before the next goes out, so the samples contain
   no queueing delay and describe what a single operator action costs.

2. **Throughput (open loop)** - commands pushed as fast as they will go. This
   saturates the pipe on purpose, so its latencies include queueing and are
   reported separately as "under load".

All timing is controller-side against ``time.perf_counter``, so no clock
synchronisation with the device is required.

    python scripts/latency_benchmark.py
    python scripts/latency_benchmark.py --devices 4 --samples 500

The numbers quoted in README.md came from this configuration, with the raw
output committed as latency.json:

    python scripts/latency_benchmark.py --devices 2 --samples 300 --throughput-commands 2000 --json latency.json

Re-run that to refresh both, then paste the new tables into the README's
Latency section so the two cannot drift apart.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import threading
import time
from datetime import datetime, timezone

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controller.protocol.message_format import encode, make_command  # noqa: E402
from controller.server import command_tracker as ct  # noqa: E402
from controller.server import device_manager as dm  # noqa: E402
from controller.server import latency_monitor as lm  # noqa: E402
from controller.server import logger  # noqa: E402
from controller.server import server as srv  # noqa: E402
from controller.server.connection_handler import handle_device  # noqa: E402
from simulations.simulated_led_device import LEDDevice  # noqa: E402

BENCH_PORT_DEFAULT = 9111

_CHANNEL_MEANING = {
    "ack": "COMMAND -> RECEIVED_ACK (transport round trip)",
    "execute": "COMMAND -> STATUS (round trip + device work)",
    "heartbeat": "HEARTBEAT -> HEARTBEAT_ACK (idle path)",
}


# --- harness -----------------------------------------------------------------


def _accept_forever(listener: socket.socket, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            conn, addr = listener.accept()
        except OSError:
            return
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(target=handle_device, args=(conn, addr), daemon=True).start()


def _wait_until(predicate, timeout: float) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.002)
    return predicate()


def _executed(cmd_id: str) -> bool:
    return bool((ct.get(cmd_id) or {}).get("executed"))


def _send_one(device_id: str) -> str | None:
    conn = dm.get_conn(device_id)
    if conn is None:
        return None
    cmd = make_command(device_id, "TOGGLE", {})
    ct.add_command(cmd)
    try:
        conn.sendall(encode(cmd))
    except OSError:
        ct.mark_failed(cmd["cmd_id"], "send failed")
        return None
    return cmd["cmd_id"]


# --- phases ------------------------------------------------------------------


def _phase_closed_loop(device_ids: list[str], samples: int, timeout: float) -> dict:
    """One command in flight at a time: pure request-response latency."""
    sent = completed = 0
    started = time.perf_counter()

    for i in range(samples):
        cmd_id = _send_one(device_ids[i % len(device_ids)])
        if cmd_id is None:
            continue
        sent += 1
        if _wait_until(lambda c=cmd_id: _executed(c), timeout):
            completed += 1

    elapsed = time.perf_counter() - started
    return {
        "commands_sent": sent,
        "commands_completed": completed,
        "wall_clock_sec": round(elapsed, 3),
        "success_rate_pct": round(100.0 * completed / sent, 2) if sent else 0.0,
        "latency": lm.all_stats(),
    }


def _phase_open_loop(device_ids: list[str], count: int, timeout: float) -> dict:
    """Commands pushed without waiting: saturation throughput."""
    started = time.perf_counter()
    sent_ids = [
        cid
        for i in range(count)
        if (cid := _send_one(device_ids[i % len(device_ids)])) is not None
    ]
    _wait_until(lambda: all(_executed(c) for c in sent_ids), timeout)
    elapsed = time.perf_counter() - started

    completed = sum(1 for c in sent_ids if _executed(c))
    return {
        "commands_sent": len(sent_ids),
        "commands_completed": completed,
        "wall_clock_sec": round(elapsed, 3),
        "throughput_cmd_per_sec": round(completed / elapsed, 1) if elapsed else 0.0,
        "success_rate_pct": round(100.0 * completed / len(sent_ids), 2) if sent_ids else 0.0,
        "latency_under_load": lm.all_stats(),
    }


def run_benchmark(
    devices: int = 2,
    samples: int = 200,
    throughput_commands: int = 1000,
    warmup: int = 50,
    port: int = BENCH_PORT_DEFAULT,
    timeout: float = 10.0,
    quiet: bool = True,
) -> dict:
    """Run both phases and return a result dict."""
    logger.set_muted(quiet)
    lm.clear()
    ct.clear()
    dm.clear()

    stop = threading.Event()
    listener = srv.bind_listener("127.0.0.1", port)
    threading.Thread(target=_accept_forever, args=(listener, stop), daemon=True).start()

    sims = [
        LEDDevice(f"bench-led-{i}", host="127.0.0.1", port=port, quiet=True)
        for i in range(devices)
    ]
    for sim in sims:
        threading.Thread(target=sim.run, daemon=True).start()

    try:
        if not _wait_until(lambda: dm.count() >= devices, 10.0):
            raise RuntimeError(f"only {dm.count()}/{devices} devices connected in time")

        device_ids = sorted(d for d, _ in dm.all_devices())

        # Warm up and discard: the first exchanges pay for lazy imports, TCP
        # slow start and cold branch predictors, which would distort the tail.
        for i in range(warmup):
            cmd_id = _send_one(device_ids[i % len(device_ids)])
            if cmd_id:
                _wait_until(lambda c=cmd_id: _executed(c), timeout)

        lm.clear()
        ct.clear()
        latency = _phase_closed_loop(device_ids, samples, timeout)

        lm.clear()
        ct.clear()
        throughput = _phase_open_loop(device_ids, throughput_commands, timeout * 3)

        return {
            "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
            "environment": {
                "python": platform.python_version(),
                "platform": f"{platform.system()} {platform.release()}",
                "machine": platform.machine(),
                "transport": "TCP loopback (127.0.0.1)",
                "devices": devices,
                "warmup_commands": warmup,
            },
            "latency_closed_loop": latency,
            "throughput_open_loop": throughput,
        }
    finally:
        stop.set()
        for sim in sims:
            sim.stop()
        try:
            listener.close()
        except OSError:
            pass
        logger.set_muted(False)


# --- reporting ---------------------------------------------------------------


def _table(latency: dict, indent: str = "  ") -> str:
    if not latency:
        return f"{indent}No latency samples collected."
    header = (
        f"{indent}{'channel':<10} {'n':>6} {'min':>9} {'mean':>9} "
        f"{'p50':>9} {'p95':>9} {'p99':>9} {'max':>9}"
    )
    rows = [header, indent + "-" * (len(header) - len(indent))]
    for channel, s in latency.items():
        rows.append(
            f"{indent}{channel:<10} {s['count']:>6} {s['min_ms']:>9.3f} "
            f"{s['mean_ms']:>9.3f} {s['p50_ms']:>9.3f} {s['p95_ms']:>9.3f} "
            f"{s['p99_ms']:>9.3f} {s['max_ms']:>9.3f}"
        )
    return "\n".join(rows)


def format_report(result: dict) -> str:
    env = result["environment"]
    lat = result["latency_closed_loop"]
    thr = result["throughput_open_loop"]
    rule = "=" * 78

    lines = [
        rule,
        "  Remote Device Control - end-to-end benchmark",
        rule,
        f"  Generated   : {result['generated_utc']}",
        f"  Environment : Python {env['python']} on {env['platform']} ({env['machine']})",
        f"  Transport   : {env['transport']}, {env['devices']} simulated device(s)",
        f"  Warm-up     : {env['warmup_commands']} commands, discarded",
        "",
        "-" * 78,
        "  PHASE 1  Latency - closed loop, one command in flight at a time",
        "-" * 78,
        f"  Completed   : {lat['commands_completed']}/{lat['commands_sent']} "
        f"({lat['success_rate_pct']}%) in {lat['wall_clock_sec']}s",
        "",
        _table(lat["latency"]),
        "",
        "-" * 78,
        "  PHASE 2  Throughput - open loop, pipe saturated",
        "-" * 78,
        f"  Completed   : {thr['commands_completed']}/{thr['commands_sent']} "
        f"({thr['success_rate_pct']}%) in {thr['wall_clock_sec']}s",
        f"  Throughput  : {thr['throughput_cmd_per_sec']} commands/sec",
        "",
        _table(thr["latency_under_load"]),
        "",
        rule,
        "  All latencies in milliseconds, measured controller-side.",
    ]
    for channel, meaning in _CHANNEL_MEANING.items():
        if channel in lat["latency"] or channel in thr["latency_under_load"]:
            lines.append(f"    {channel:<10} = {meaning}")
    lines.append(
        "  Phase 2 latencies include queueing delay by design - compare only\n"
        "  against phase 1, never instead of it."
    )
    lines.append(rule)
    return "\n".join(lines)


def _md_table(latency: dict) -> list[str]:
    rows = [
        "| Channel | n | min | mean | p50 | p95 | p99 | max |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for channel, s in latency.items():
        rows.append(
            f"| `{channel}` | {s['count']} | {s['min_ms']} | {s['mean_ms']} | "
            f"{s['p50_ms']} | {s['p95_ms']} | {s['p99_ms']} | {s['max_ms']} |"
        )
    return rows


def format_markdown(result: dict) -> str:
    env = result["environment"]
    lat = result["latency_closed_loop"]
    thr = result["throughput_open_loop"]

    lines = [
        "# Latency Benchmark",
        "",
        f"Generated `{result['generated_utc']}` by "
        "`python scripts/latency_benchmark.py --markdown latency.md`.",
        "",
        "## Environment",
        "",
        "| | |",
        "|---|---|",
        f"| Python | {env['python']} |",
        f"| Platform | {env['platform']} ({env['machine']}) |",
        f"| Transport | {env['transport']} |",
        f"| Devices | {env['devices']} simulated |",
        f"| Warm-up | {env['warmup_commands']} commands, discarded |",
        "",
        "## Phase 1 — Latency (closed loop)",
        "",
        "One command in flight at a time; each send waits for its own `STATUS` "
        "before the next goes out. No queueing delay, so these numbers describe "
        "what a single operator action actually costs.",
        "",
        f"Completed **{lat['commands_completed']}/{lat['commands_sent']}** "
        f"({lat['success_rate_pct']}%) in {lat['wall_clock_sec']} s.",
        "",
        *_md_table(lat["latency"]),
        "",
        "All values in milliseconds.",
        "",
        "## Phase 2 — Throughput (open loop)",
        "",
        "Commands pushed as fast as they will go, saturating the pipe on purpose.",
        "",
        f"Completed **{thr['commands_completed']}/{thr['commands_sent']}** "
        f"({thr['success_rate_pct']}%) in {thr['wall_clock_sec']} s — "
        f"**{thr['throughput_cmd_per_sec']} commands/sec**.",
        "",
        *_md_table(thr["latency_under_load"]),
        "",
        "> These latencies include queueing delay by design. Compare them "
        "against phase 1, never instead of it.",
        "",
        "## Channel meanings",
        "",
    ]
    for channel, meaning in _CHANNEL_MEANING.items():
        lines.append(f"- **`{channel}`** — {meaning}")
    lines += [
        "",
        "## Caveats",
        "",
        "- Measured over TCP loopback, so these figures isolate protocol and "
        "controller overhead. A real deployment adds physical network time "
        "(typically 1–30 ms on LAN/Wi-Fi) on top.",
        "- The devices are Python simulators. Real firmware adds its own "
        "processing time, which shows up in `execute` but not in `ack`.",
        "- Timed with `time.perf_counter()` on the controller only, so no clock "
        "synchronisation between controller and device is required.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end latency and throughput benchmark",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--devices", type=int, default=2, help="Simulated devices to attach")
    parser.add_argument("--samples", type=int, default=200, help="Closed-loop latency samples")
    parser.add_argument(
        "--throughput-commands", type=int, default=1000, help="Open-loop commands"
    )
    parser.add_argument("--warmup", type=int, default=50, help="Warm-up commands to discard")
    parser.add_argument("--port", type=int, default=BENCH_PORT_DEFAULT, help="Port to bind")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-command timeout")
    parser.add_argument("--verbose", action="store_true", help="Show controller logs")
    parser.add_argument("--json", metavar="PATH", help="Write raw results as JSON")
    parser.add_argument("--markdown", metavar="PATH", help="Write a Markdown report")
    args = parser.parse_args()

    result = run_benchmark(
        devices=args.devices,
        samples=args.samples,
        throughput_commands=args.throughput_commands,
        warmup=args.warmup,
        port=args.port,
        timeout=args.timeout,
        quiet=not args.verbose,
    )

    print(format_report(result))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
        print(f"\nWrote JSON to {args.json}")

    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(format_markdown(result))
        print(f"Wrote Markdown to {args.markdown}")


if __name__ == "__main__":
    main()
