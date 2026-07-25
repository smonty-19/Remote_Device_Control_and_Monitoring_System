"""
Message builders and encode/decode helpers.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from controller.config.server_config import AUTH_TOKEN
from controller.protocol.constants import (
    MSG_AUTH,
    MSG_AUTH_FAIL,
    MSG_AUTH_OK,
    MSG_COMMAND,
    MSG_HEARTBEAT,
    MSG_HEARTBEAT_ACK,
    MSG_RECEIVED_ACK,
    MSG_STATUS,
)


def new_cmd_id() -> str:
    """Generate a short unique command ID."""
    return uuid.uuid4().hex[:8]


def now() -> float:
    """Current Unix timestamp."""
    return time.time()


def encode(msg: dict[str, Any]) -> bytes:
    """Serialize a message dict to newline-terminated UTF-8 bytes."""
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")


def decode(raw: str) -> dict[str, Any]:
    """Deserialize a newline-stripped string back to a message dict."""
    return json.loads(raw.strip())


def make_auth(device_id: str, device_type: str) -> dict[str, Any]:
    return {
        "msg_type": MSG_AUTH,
        "device_id": device_id,
        "device_type": device_type,
        "token": AUTH_TOKEN,
        "timestamp": now(),
    }


def make_auth_ok(device_id: str) -> dict[str, Any]:
    return {
        "msg_type": MSG_AUTH_OK,
        "device_id": device_id,
        "timestamp": now(),
    }


def make_auth_fail(reason: str) -> dict[str, Any]:
    return {
        "msg_type": MSG_AUTH_FAIL,
        "reason": reason,
        "timestamp": now(),
    }


def make_command(device_id: str, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "msg_type": MSG_COMMAND,
        "cmd_id": new_cmd_id(),
        "device_id": device_id,
        "action": action,
        "params": params or {},
        "timestamp": now(),
    }


def make_received_ack(cmd_id: str, device_id: str) -> dict[str, Any]:
    return {
        "msg_type": MSG_RECEIVED_ACK,
        "cmd_id": cmd_id,
        "device_id": device_id,
        "timestamp": now(),
    }


def make_status(
    device_id: str,
    state: dict[str, Any],
    cmd_id: str | None = None,
    success: bool = True,
    message: str | None = None,
) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "msg_type": MSG_STATUS,
        "device_id": device_id,
        "state": state,
        "success": success,
        "timestamp": now(),
    }
    if cmd_id is not None:
        msg["cmd_id"] = cmd_id
    if message is not None:
        msg["message"] = message
    return msg


def make_heartbeat(source: str) -> dict[str, Any]:
    return {
        "msg_type": MSG_HEARTBEAT,
        "source": source,
        "timestamp": now(),
    }


def make_heartbeat_ack(device_id: str) -> dict[str, Any]:
    return {
        "msg_type": MSG_HEARTBEAT_ACK,
        "device_id": device_id,
        "timestamp": now(),
    }