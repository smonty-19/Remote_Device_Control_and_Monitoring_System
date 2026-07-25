"""
Controller entry point.

Starts the TCP listener plus three background loops (heartbeat, command
retry, housekeeping) and then runs the operator CLI on the main thread. The
CLI shares this process's device registry, which is why it lives here rather
than in a separate program.
"""

from __future__ import annotations

import socket
import threading
import time

from controller.cli.cli import cli_loop
from controller.config.server_config import (
    BACKLOG,
    COMMAND_ACK_TIMEOUT_SEC,
    COMMAND_MAX_RETRIES,
    COMMAND_RETRY_INTERVAL_SEC,
    HEARTBEAT_EVERY_SEC,
    HEARTBEAT_TIMEOUT_SEC,
    HOST,
    PORT,
    PRUNE_INTERVAL_SEC,
)
from controller.protocol.message_format import encode, make_heartbeat
from controller.server import device_manager as dm
from controller.server.command_tracker import (
    bump_retry,
    mark_failed,
    pending_items,
    prune,
)
from controller.server.connection_handler import handle_device
from controller.server.logger import log

_shutdown = threading.Event()


def _send(conn: socket.socket, msg: dict) -> bool:
    try:
        conn.sendall(encode(msg))
        return True
    except OSError:
        return False


def _accept_loop(server_sock: socket.socket) -> None:
    while not _shutdown.is_set():
        try:
            conn, addr = server_sock.accept()
        except OSError:
            # Listener closed during shutdown.
            return
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(
            target=handle_device, args=(conn, addr), daemon=True, name=f"device-{addr[1]}"
        ).start()


def ping_device(dev_id: str, conn: socket.socket) -> bool:
    """Send one heartbeat and arm its round-trip timer.

    The timer is armed *before* the send, not after. Over loopback the device
    can reply and the session thread can process the ack while this thread is
    still inside ``sendall``. Arming afterwards loses that sample and leaves a
    pending ping that nothing will ever answer, so the *next* ack gets
    measured against a stale timestamp and reports one full heartbeat
    interval as its RTT.
    """
    dm.mark_heartbeat_sent(dev_id)
    if _send(conn, make_heartbeat("controller")):
        return True
    dm.clear_heartbeat_sent(dev_id)
    return False


def _heartbeat_loop() -> None:
    while not _shutdown.wait(HEARTBEAT_EVERY_SEC):
        for dev_id, info in dm.all_devices():
            now = time.time()
            if not ping_device(dev_id, info["conn"]):
                # The socket is gone; drop it now instead of waiting for the
                # heartbeat timeout to notice.
                if dm.unregister(dev_id, info["conn"]):
                    log("DISC", f"{dev_id} dropped (send failed)", "red")
                continue

            elapsed = now - info["last_heartbeat"]
            if elapsed > HEARTBEAT_EVERY_SEC + HEARTBEAT_TIMEOUT_SEC:
                dm.mark_stale(dev_id)
                log("HB", f"{dev_id} missed heartbeat ({elapsed:.1f}s)", "red")


def _retry_loop() -> None:
    while not _shutdown.wait(COMMAND_RETRY_INTERVAL_SEC):
        for cmd_id, info in pending_items():
            if time.time() - info["last_sent"] < COMMAND_ACK_TIMEOUT_SEC:
                continue

            if info["retry_count"] >= COMMAND_MAX_RETRIES:
                mark_failed(cmd_id, "max retries reached")
                log("CMD", f"{cmd_id} failed: max retries reached", "red")
                continue

            conn = dm.get_conn(info["device_id"])
            if conn is None:
                mark_failed(cmd_id, "device disconnected")
                log("CMD", f"{cmd_id} failed: {info['device_id']} disconnected", "red")
                continue

            if _send(conn, info["command"]):
                bump_retry(cmd_id)
                log(
                    "CMD",
                    f"retry {info['retry_count'] + 1}/{COMMAND_MAX_RETRIES} "
                    f"of {cmd_id} to {info['device_id']}",
                    "yellow",
                )


def _housekeeping_loop() -> None:
    while not _shutdown.wait(PRUNE_INTERVAL_SEC):
        removed = prune()
        if removed:
            log("GC", f"pruned {removed} completed command(s)", "magenta")


def start_background_threads(server_sock: socket.socket) -> None:
    for target, name in (
        (lambda: _accept_loop(server_sock), "accept"),
        (_heartbeat_loop, "heartbeat"),
        (_retry_loop, "retry"),
        (_housekeeping_loop, "housekeeping"),
    ):
        threading.Thread(target=target, daemon=True, name=name).start()


def bind_listener(host: str = HOST, port: int = PORT) -> socket.socket:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(BACKLOG)
    return server


def main() -> None:
    try:
        server = bind_listener()
    except OSError as exc:
        log("BOOT", f"Cannot bind {HOST}:{PORT} - {exc}", "red")
        raise SystemExit(1)

    log("BOOT", f"Controller listening on {HOST}:{PORT}", "green")
    start_background_threads(server)

    try:
        cli_loop()
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown.set()
        log("BOOT", "Shutting down", "green")
        try:
            server.close()
        except OSError:
            pass


if __name__ == "__main__":
    main()
