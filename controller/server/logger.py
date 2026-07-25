"""
Timestamped, colourised console logging.

Writes are serialised behind a lock because the accept, heartbeat, retry and
per-device threads all log concurrently and unsynchronised ``print`` calls
interleave mid-line.
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime

from controller.config.server_config import NO_COLOR

_RESET = "\033[0m"
_COLORS = {
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "magenta": "\033[35m",
}

_print_lock = threading.Lock()
_muted = False


def set_muted(muted: bool) -> None:
    """Silence all log output.

    Used by the benchmark, where thousands of per-message lines would both
    bury the report and distort the timings being measured.
    """
    global _muted
    _muted = muted


def _enable_windows_ansi() -> bool:
    """Turn on virtual-terminal processing so ANSI codes render on Windows."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # -11 = STD_OUTPUT_HANDLE, 0x4 = ENABLE_VIRTUAL_TERMINAL_PROCESSING
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x4))
    except Exception:
        return False


_USE_COLOR = (not NO_COLOR) and sys.stdout.isatty() and _enable_windows_ansi()


def log(tag: str, message: str, color: str | None = None) -> None:
    if _muted:
        return

    stamp = datetime.now().strftime("%H:%M:%S")
    prefix = f"[{stamp}] [{tag:<4}]"

    if _USE_COLOR and color in _COLORS:
        line = f"{_COLORS[color]}{prefix} {message}{_RESET}"
    else:
        line = f"{prefix} {message}"

    with _print_lock:
        print(line, flush=True)
