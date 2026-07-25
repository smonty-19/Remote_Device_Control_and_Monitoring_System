"""
Shared behaviour for software device simulators.

Handles the parts that are identical for every device type: connecting and
reconnecting, newline framing over TCP, the AUTH handshake, heartbeat
replies, and dispatching COMMAND messages. Subclasses implement the two
device-specific hooks, :meth:`execute` and :meth:`tick`.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Any

from controller.config.server_config import HOST, PORT
from controller.protocol.constants import (
    MSG_AUTH_FAIL,
    MSG_AUTH_OK,
    MSG_COMMAND,
    MSG_HEARTBEAT,
)
from controller.protocol.message_format import (
    decode,
    encode,
    make_auth,
    make_heartbeat_ack,
    make_received_ack,
    make_status,
)

RECONNECT_DELAY_SEC = 2.0
# Bounds how long a blocking read waits, so `tick` still runs on an idle link.
POLL_INTERVAL_SEC = 0.2


class SimulatedDevice:
    """Base class for a device that speaks the controller protocol."""

    device_type = "GENERIC"

    def __init__(self, device_id: str, host: str = HOST, port: int = PORT, quiet: bool = False):
        self.device_id = device_id
        self.host = host
        self.port = port
        self.quiet = quiet
        self.sock: socket.socket | None = None
        self.running = True
        self.authenticated = False
        self._send_lock = threading.Lock()

    # -- device-specific hooks -------------------------------------------------

    def execute(self, action: str, params: dict[str, Any]) -> tuple[bool, str]:
        """Perform ``action``. Return ``(success, message)``."""
        raise NotImplementedError

    def current_state(self) -> dict[str, Any]:
        """The state dict reported back to the controller."""
        return {}

    def tick(self) -> None:
        """Called regularly whether or not traffic arrived. Optional."""

    # -- plumbing --------------------------------------------------------------

    def say(self, message: str) -> None:
        if not self.quiet:
            print(f"[{self.device_id}] {message}", flush=True)

    def send(self, msg: dict) -> None:
        with self._send_lock:
            if self.sock is not None:
                self.sock.sendall(encode(msg))

    def run(self) -> None:
        """Connect, and keep reconnecting until stopped."""
        self.say(f"connecting to {self.host}:{self.port} ...")
        while self.running:
            try:
                self._connect_once()
            except (ConnectionError, OSError) as exc:
                self.say(f"connection lost ({exc}); retrying in {RECONNECT_DELAY_SEC:.0f}s")
            except KeyboardInterrupt:
                self.running = False
                break
            finally:
                self._close()

            if self.running:
                time.sleep(RECONNECT_DELAY_SEC)

    def stop(self) -> None:
        self.running = False
        self._close()

    def _close(self) -> None:
        self.authenticated = False
        with self._send_lock:
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None

    def _connect_once(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=5)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # A bounded timeout keeps `tick` running when the controller is silent.
        sock.settimeout(POLL_INTERVAL_SEC)
        self.sock = sock
        self.send(make_auth(self.device_id, self.device_type))
        self._listen_loop()

    def _listen_loop(self) -> None:
        assert self.sock is not None
        buffer = ""

        while self.running:
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                # Idle link: no data is normal, keep the device's own work going.
                self.tick()
                continue

            if not data:
                raise ConnectionError("controller closed the connection")

            buffer += data.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    self._handle(decode(line))
                except ValueError:
                    self.say(f"ignoring malformed frame: {line[:80]!r}")

            self.tick()

    def _handle(self, msg: dict) -> None:
        msg_type = msg.get("msg_type")

        if msg_type == MSG_AUTH_OK:
            self.authenticated = True
            self.say("authenticated")
        elif msg_type == MSG_AUTH_FAIL:
            # Retrying with the same rejected credentials would spin forever.
            reason = msg.get("reason", "unknown reason")
            self.say(f"authentication rejected: {reason}")
            self.running = False
        elif msg_type == MSG_HEARTBEAT:
            self.send(make_heartbeat_ack(self.device_id))
        elif msg_type == MSG_COMMAND:
            self._handle_command(msg)

    def _handle_command(self, msg: dict) -> None:
        cmd_id = msg.get("cmd_id", "")
        action = msg.get("action", "")
        params = msg.get("params") or {}

        # Ack first so the controller can separate transport latency from
        # however long the action itself takes.
        self.send(make_received_ack(cmd_id, self.device_id))

        try:
            success, message = self.execute(action, params)
        except Exception as exc:
            success, message = False, f"device error: {exc}"

        self.send(
            make_status(
                self.device_id,
                self.current_state(),
                cmd_id=cmd_id,
                success=success,
                message=message,
            )
        )
