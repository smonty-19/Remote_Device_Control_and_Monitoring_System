"""
Single source of truth for all runtime configuration.

Every value can be overridden with an environment variable before the process
starts; see ``.env.example`` for the full list.
"""

from __future__ import annotations

import os


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# --- Network -----------------------------------------------------------------
HOST = os.getenv("IOT_HOST", "127.0.0.1")
PORT = _int_env("IOT_PORT", 9000)
BACKLOG = _int_env("IOT_BACKLOG", 32)
RECV_BUFFER_BYTES = _int_env("IOT_RECV_BUFFER_BYTES", 4096)

# --- Authentication ----------------------------------------------------------
AUTH_TOKEN = os.getenv("IOT_AUTH_TOKEN", "iot-secret-2026")
AUTH_TIMEOUT_SEC = _float_env("IOT_AUTH_TIMEOUT_SEC", 5)

# --- Liveness ----------------------------------------------------------------
HEARTBEAT_EVERY_SEC = _float_env("IOT_HEARTBEAT_EVERY_SEC", 10)
HEARTBEAT_TIMEOUT_SEC = _float_env("IOT_HEARTBEAT_TIMEOUT_SEC", 5)

# --- Command delivery --------------------------------------------------------
COMMAND_ACK_TIMEOUT_SEC = _float_env("IOT_COMMAND_ACK_TIMEOUT_SEC", 5)
COMMAND_MAX_RETRIES = _int_env("IOT_COMMAND_MAX_RETRIES", 3)
COMMAND_RETRY_INTERVAL_SEC = _float_env("IOT_COMMAND_RETRY_INTERVAL_SEC", 3)

# --- Housekeeping ------------------------------------------------------------
PRUNE_INTERVAL_SEC = _float_env("IOT_PRUNE_INTERVAL_SEC", 60)

# Disable ANSI colour codes (useful when piping logs to a file).
NO_COLOR = os.getenv("IOT_NO_COLOR", "").lower() in {"1", "true", "yes"}
