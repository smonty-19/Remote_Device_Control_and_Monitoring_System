"""Temperature sensor simulator.

Unlike the LED it is not purely reactive: it pushes an unsolicited STATUS
reading every ``--interval`` seconds, which the controller records as
telemetry.

Run directly (``python simulations/simulated_temp_device.py``) or as a module
(``python -m simulations.simulated_temp_device``).
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from typing import Any

if __package__ in (None, ""):
    # Direct script execution puts simulations/ on sys.path, not the repo
    # root, so `controller` would not be importable without this.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controller.config.server_config import HOST, PORT  # noqa: E402
from controller.protocol.message_format import make_status  # noqa: E402
from simulations.base import SimulatedDevice  # noqa: E402

MIN_INTERVAL_SEC = 0.5


class TempDevice(SimulatedDevice):
    device_type = "TEMP_SENSOR"

    def __init__(self, *args, report_interval: float = 2.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.report_interval = max(MIN_INTERVAL_SEC, report_interval)
        self.last_report = 0.0

    def current_state(self) -> dict[str, Any]:
        """Simulate a 10-bit ADC reading on a slow sine wave."""
        raw = int(450 + 100 * math.sin(time.time() / 3.0))
        return {"temperature_raw": raw, "temperature_c": round((raw / 1023.0) * 100, 2)}

    def tick(self) -> None:
        """Push a periodic reading.

        Called from the read loop on every pass, including reads that time
        out, so telemetry keeps flowing on an otherwise idle connection.
        """
        if not self.authenticated:
            return
        now = time.time()
        if now - self.last_report < self.report_interval:
            return
        self.last_report = now
        self.send(
            make_status(
                self.device_id,
                self.current_state(),
                success=True,
                message="periodic reading",
            )
        )

    def execute(self, action: str, params: dict[str, Any]) -> tuple[bool, str]:
        if action == "READ_NOW":
            return True, "measurement sent"

        if action == "SET_INTERVAL":
            seconds = params.get("seconds")
            if seconds is None:
                return False, "missing 'seconds' parameter"
            try:
                self.report_interval = max(MIN_INTERVAL_SEC, float(seconds))
            except (TypeError, ValueError):
                return False, f"invalid 'seconds' parameter: {seconds!r}"
            self.say(f"report interval -> {self.report_interval}s")
            return True, f"interval set to {self.report_interval}s"

        return False, f"unsupported action: {action} (expected READ_NOW or SET_INTERVAL)"


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulated temperature sensor")
    parser.add_argument("--id", default=os.getenv("TEMP_DEVICE_ID", "temp1"), help="Device ID")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument(
        "--interval", type=float, default=2.0, help="Seconds between readings"
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress per-command output")
    args = parser.parse_args()

    device = TempDevice(
        args.id,
        host=args.host,
        port=args.port,
        quiet=args.quiet,
        report_interval=args.interval,
    )
    try:
        device.run()
    except KeyboardInterrupt:
        pass
    finally:
        device.stop()


if __name__ == "__main__":
    main()
