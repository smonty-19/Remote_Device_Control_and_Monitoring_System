#!/usr/bin/env bash
# Start one LED and one temperature simulator, and stop both on Ctrl+C.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m simulations.simulated_led_device "$@" &
LED_PID=$!

python3 -m simulations.simulated_temp_device "$@" &
TEMP_PID=$!

trap 'kill "$LED_PID" "$TEMP_PID" 2>/dev/null || true' EXIT INT TERM
wait
