"""LED device simulator.

Run directly (``python simulations/simulated_led_device.py``) or as a module
(``python -m simulations.simulated_led_device``).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

if __package__ in (None, ""):
    # Direct script execution puts simulations/ on sys.path, not the repo
    # root, so `controller` would not be importable without this.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controller.config.server_config import HOST, PORT  # noqa: E402
from simulations.base import SimulatedDevice  # noqa: E402

ACTIONS = ("TURN_ON", "TURN_OFF", "TOGGLE")


class LEDDevice(SimulatedDevice):
    device_type = "LED"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.state = "OFF"

    def current_state(self) -> dict[str, Any]:
        return {"led": self.state}

    def execute(self, action: str, params: dict[str, Any]) -> tuple[bool, str]:
        if action == "TURN_ON":
            self.state = "ON"
        elif action == "TURN_OFF":
            self.state = "OFF"
        elif action == "TOGGLE":
            self.state = "OFF" if self.state == "ON" else "ON"
        else:
            return False, f"unsupported action: {action} (expected one of {', '.join(ACTIONS)})"

        self.say(f"LED -> {self.state}")
        return True, f"led {self.state.lower()}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulated LED device")
    parser.add_argument("--id", default=os.getenv("LED_DEVICE_ID", "led1"), help="Device ID")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--quiet", action="store_true", help="Suppress per-command output")
    args = parser.parse_args()

    device = LEDDevice(args.id, host=args.host, port=args.port, quiet=args.quiet)
    try:
        device.run()
    except KeyboardInterrupt:
        pass
    finally:
        device.stop()


if __name__ == "__main__":
    main()
