"""
Operator REPL.

This runs on the main thread of the controller process. The device registry
lives in memory in that same process, so the CLI cannot be launched
standalone - see ``main()``.
"""

from __future__ import annotations

import shlex
import sys

from controller.cli.commands import run_command
from controller.cli.parser import CLIParserError, build_parser

BANNER = """
Remote Device Controller
  list                                  connected devices
  status <device_id>                    detail for one device
  send <device_id> <action> [--param k=v]   queue a command
  pending                               command delivery table
  stats                                 controller counters
  latency [--per-device] [--json] [--reset]   measured latencies
  bench <device_id> <action> -n 100     latency benchmark run
  help | exit
""".strip()


def cli_loop() -> None:
    parser = build_parser(repl=True)
    print(BANNER)

    while True:
        try:
            line = input("controller> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue
        if line.lower() in {"exit", "quit"}:
            break
        if line.lower() in {"help", "?"}:
            print(BANNER)
            continue

        try:
            args = parser.parse_args(shlex.split(line))
        except CLIParserError as exc:
            print(f"error: {exc}")
            continue
        except ValueError as exc:  # unbalanced quotes from shlex
            print(f"error: {exc}")
            continue
        except SystemExit:
            # argparse still exits for --help.
            continue

        try:
            run_command(args)
        except Exception as exc:  # keep the REPL alive on handler bugs
            print(f"error: {exc!r}")


def main() -> None:
    """Standalone entry point.

    The registry of connected devices is per-process state held by the
    controller, so a separate CLI process would always see zero devices.
    Rather than print a convincing but meaningless empty table, refuse.
    """
    print(
        "The controller CLI runs inside the server process, not on its own.\n"
        "Start the controller instead:\n\n"
        "    python -m controller.server.server\n\n"
        "The prompt appears once the listener is up.",
        file=sys.stderr,
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
