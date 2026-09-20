"""Command line interface.

This is the only module that configures logging, and the only one that returns
process exit codes. Everything below it raises instead.

The GUI is imported lazily: ``--version``, ``--paths`` and the IPC commands
must work on a machine with no display, and importing :mod:`hearth.ui` pulls in
``gi`` and the legacy core, which probes PipeWire at import time.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from importlib import metadata

import hearth
from hearth import logging_setup, paths
from hearth.errors import HearthError

__all__ = ["build_parser", "main", "version"]

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def version() -> str:
    """Installed version if available, else the in-tree constant."""
    try:
        return metadata.version("hearth")
    except metadata.PackageNotFoundError:
        return hearth.__version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hearth",
        description="Control panel for a PipeWire virtual-mixer audio rig.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Hearth {version()}",
    )
    parser.add_argument(
        "--log-level",
        choices=[level.lower() for level in logging_setup.LEVELS],
        help="Logging verbosity. Defaults to $HEARTH_LOG_LEVEL, then info.",
    )
    parser.add_argument(
        "--paths",
        action="store_true",
        help="Print the resolved XDG locations Hearth uses, then exit.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print a plain-language report on the whole rig, then exit.",
    )
    parser.add_argument(
        "--quit",
        action="store_true",
        help="Quit the running instance.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show and focus the running instance, starting one if needed.",
    )
    parser.add_argument(
        "--unmute",
        action="store_true",
        help="Unmute every bus and exit. Needs no display.",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Ask the running instance for a debug dump.",
    )
    return parser


def _print_paths() -> None:
    for label, value in (
        ("config", paths.config_dir()),
        ("config file", paths.config_file()),
        ("settings", paths.settings_file()),
        ("state", paths.state_dir()),
        ("log", paths.log_file()),
        ("dumps", paths.dumps_dir()),
        ("cache", paths.cache_dir()),
        ("runtime", paths.runtime_dir()),
        ("socket", paths.socket_file()),
        ("pid", paths.pid_file()),
    ):
        print(f"{label:<12} {value}")


def _dispatch(args: argparse.Namespace) -> int:
    """Run the commands that need the legacy core or the GUI.

    Imported here rather than at module scope: the legacy core probes PipeWire
    at import time, and ``hearth.ui`` imports ``gi``.
    """
    from hearth import legacy_core as core

    if args.unmute:
        core.unmute_all()
        print("unmuted all sinks")
        return EXIT_OK

    if args.quit:
        reply = core.ipc_send("quit")
        print("quit" if reply else "no running instance")
        return EXIT_OK

    if args.dump:
        reply = core.ipc_send("dump")
        print(reply or "no running instance")
        return EXIT_OK

    # Default and --show: hand off to a live instance, else become one.
    if core.ipc_send("show"):
        return EXIT_OK
    pid = core._read_pid()
    if pid and core._pid_alive(pid):
        core.ipc_send("show")
        return EXIT_OK

    from hearth.ui import run_app

    return run_app()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging_setup.configure(args.log_level)

    try:
        if args.paths:
            _print_paths()
            return EXIT_OK

        if args.status:
            # Imported here, not at module scope: the report only needs the
            # new GTK-free modules, and must not pay for the legacy core.
            from hearth import status

            print(status.text())
            return EXIT_OK

        return _dispatch(args)
    except HearthError as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
