#!/usr/bin/env bash
# Start the controller. The operator CLI runs inside this process.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 -m controller.server.server "$@"
