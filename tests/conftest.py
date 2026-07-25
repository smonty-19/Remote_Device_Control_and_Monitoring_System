"""Shared test fixtures."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controller.server import command_tracker as ct  # noqa: E402
from controller.server import device_manager as dm  # noqa: E402
from controller.server import latency_monitor as lm  # noqa: E402
from controller.server import logger  # noqa: E402
from controller.server import server as srv  # noqa: E402
from controller.server.connection_handler import handle_device  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    """Every test starts from an empty registry, tracker and sample set."""
    dm.clear()
    ct.clear()
    lm.clear()
    logger.set_muted(True)
    yield
    dm.clear()
    ct.clear()
    lm.clear()
    logger.set_muted(False)


def free_port() -> int:
    """Ask the OS for an unused port, so parallel test runs never collide."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.01) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class ControllerHarness:
    """A real listener on a real port, accepting real device connections."""

    def __init__(self) -> None:
        self.port = free_port()
        self._stop = threading.Event()
        self._listener = srv.bind_listener("127.0.0.1", self.port)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._listener.accept()
            except OSError:
                return
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=handle_device, args=(conn, addr), daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass


@pytest.fixture
def controller():
    harness = ControllerHarness()
    yield harness
    harness.close()
