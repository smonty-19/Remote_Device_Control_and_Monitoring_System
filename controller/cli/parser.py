"""Argument parsing for the controller CLI."""

from __future__ import annotations

import argparse


class CLIParserError(Exception):
    """Raised instead of exiting, so a bad line does not kill the REPL."""


class _REPLParser(argparse.ArgumentParser):
    """ArgumentParser that raises rather than calling ``sys.exit``."""

    def error(self, message: str):  # noqa: D102 - argparse hook
        raise CLIParserError(message)


def build_parser(repl: bool = False) -> argparse.ArgumentParser:
    cls = _REPLParser if repl else argparse.ArgumentParser
    parser = cls(prog="controller", description="Remote device controller CLI")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    sub.add_parser("list", help="List connected devices")
    sub.add_parser("pending", help="Show tracked commands and their delivery state")
    sub.add_parser("stats", help="Show controller-wide counters")

    status_p = sub.add_parser("status", help="Show a device status")
    status_p.add_argument("device_id", help="Device ID")

    send_p = sub.add_parser("send", help="Send a command to a device")
    send_p.add_argument("device_id", help="Device ID")
    send_p.add_argument("action", help="Command action, e.g. TURN_ON")
    send_p.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Optional command parameter; may be repeated",
    )

    lat_p = sub.add_parser("latency", help="Show measured latency statistics")
    lat_p.add_argument(
        "--per-device",
        action="store_true",
        help="Break the table down by device as well as in total",
    )
    lat_p.add_argument(
        "--reset",
        action="store_true",
        help="Discard all collected samples and start over",
    )
    lat_p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a table",
    )

    bench_p = sub.add_parser(
        "bench", help="Send N commands to a device back-to-back and report latency"
    )
    bench_p.add_argument("device_id", help="Device ID")
    bench_p.add_argument("action", help="Command action to repeat, e.g. TOGGLE")
    bench_p.add_argument("-n", "--count", type=int, default=100, help="Commands to send")
    bench_p.add_argument(
        "--interval",
        type=float,
        default=0.01,
        help="Seconds to wait between sends (default: 0.01)",
    )

    return parser
