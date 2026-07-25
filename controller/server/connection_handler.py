"""
Per-connection device session handler.

One thread per connected device. The session is a strict state machine: a
connection must present a valid AUTH message before any other message type is
accepted, otherwise it is closed immediately.
"""

from __future__ import annotations

import socket

from controller.config.server_config import AUTH_TIMEOUT_SEC, AUTH_TOKEN, RECV_BUFFER_BYTES
from controller.protocol.constants import (
    MSG_AUTH,
    MSG_COMMAND,
    MSG_HEARTBEAT,
    MSG_HEARTBEAT_ACK,
    MSG_RECEIVED_ACK,
    MSG_STATUS,
)
from controller.protocol.message_format import (
    decode,
    encode,
    make_auth_fail,
    make_auth_ok,
)
from controller.server import device_manager as dm
from controller.server import latency_monitor as lm
from controller.server.command_tracker import mark_executed, mark_received
from controller.server.logger import log


def _recv_lines(conn: socket.socket):
    """Yield newline-delimited frames from a stream socket.

    TCP gives no message boundaries, so partial frames are buffered until a
    newline arrives and a single read may carry several complete messages.
    """
    buffer = ""
    while True:
        chunk = conn.recv(RECV_BUFFER_BYTES)
        if not chunk:
            return
        buffer += chunk.decode("utf-8", errors="replace")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if line:
                yield line


def _reject(conn: socket.socket, reason: str, addr) -> None:
    log("AUTH", f"rejected {addr[0]}:{addr[1]} - {reason}", "red")
    try:
        conn.sendall(encode(make_auth_fail(reason)))
    except OSError:
        pass


def handle_device(conn: socket.socket, addr) -> None:
    device_id = None
    authenticated = False

    try:
        # Unauthenticated sockets get a short deadline so a peer that connects
        # and never speaks cannot hold a thread open indefinitely.
        conn.settimeout(AUTH_TIMEOUT_SEC)

        for raw_line in _recv_lines(conn):
            try:
                msg = decode(raw_line)
            except ValueError as exc:
                log("WARN", f"Bad JSON from {addr}: {exc}", "yellow")
                continue

            if not isinstance(msg, dict):
                log("WARN", f"Non-object message from {addr}", "yellow")
                continue

            msg_type = msg.get("msg_type")

            if not authenticated:
                if msg_type != MSG_AUTH:
                    _reject(conn, "AUTH required first", addr)
                    break
                if msg.get("token") != AUTH_TOKEN:
                    _reject(conn, "Invalid token", addr)
                    break

                device_id = msg.get("device_id")
                if not device_id or not isinstance(device_id, str):
                    _reject(conn, "Missing or invalid device_id", addr)
                    device_id = None
                    break

                device_type = msg.get("device_type", "UNKNOWN")
                dm.register(device_id, conn, device_type, addr)
                conn.sendall(encode(make_auth_ok(device_id)))
                authenticated = True
                conn.settimeout(None)
                log("AUTH", f"{device_id} ({device_type}) connected from {addr[0]}:{addr[1]}", "green")
                continue

            if msg_type == MSG_HEARTBEAT_ACK:
                rtt = dm.update_heartbeat(msg.get("device_id") or device_id)
                if rtt is not None:
                    lm.record(lm.HEARTBEAT, device_id, rtt)
                continue

            if msg_type == MSG_RECEIVED_ACK:
                cmd_id = msg.get("cmd_id")
                latency = mark_received(cmd_id) if cmd_id else None
                suffix = f" in {latency * 1000:.1f}ms" if latency is not None else ""
                log("ACK", f"{device_id} received {cmd_id}{suffix}", "yellow")
                continue

            if msg_type == MSG_STATUS:
                dev_id = msg.get("device_id") or device_id
                state = msg.get("state", {})
                dm.update_status(dev_id, state)

                cmd_id = msg.get("cmd_id")
                latency = mark_executed(cmd_id, state) if cmd_id else None
                suffix = f" ({latency * 1000:.1f}ms)" if latency is not None else ""
                log("STAT", f"{dev_id} -> {state}{suffix}", "blue")
                continue

            if msg_type == MSG_COMMAND:
                log("WARN", f"Devices should not send COMMAND messages: {msg}", "yellow")
                continue

            if msg_type == MSG_HEARTBEAT:
                continue

            log("WARN", f"Unknown message from {device_id or addr}: {msg}", "yellow")

    except socket.timeout:
        log("AUTH", f"Authentication timed out from {addr[0]}:{addr[1]}", "red")
    except (ConnectionError, OSError) as exc:
        log("DISC", f"{device_id or addr}: {exc}", "red")
    except Exception as exc:  # never let one bad session kill the thread silently
        log("ERR", f"{device_id or addr}: {exc!r}", "red")
    finally:
        # Only evict the registry entry if it still points at *this* socket;
        # a reconnect may already have installed a newer one.
        if device_id and dm.unregister(device_id, conn):
            log("DISC", f"{device_id} disconnected", "red")
        try:
            conn.close()
        except OSError:
            pass
