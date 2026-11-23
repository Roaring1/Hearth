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
import os
from collections.abc import Sequence
from importlib import metadata

import hearth
from hearth import logging_setup, paths
from hearth.errors import HearthError

__all__ = ["build_parser", "main", "version"]

log = logging.getLogger(__name__)


def _is_windows() -> bool:
    return os.name == "nt"


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
        "--remap",
        action="store_true",
        help="Open the LPD8 controls window in the running instance.",
    )
    parser.add_argument(
        "--unmute",
        action="store_true",
        help="Request an unmute of every bus and exit; state is not synchronously verified.",
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
        if not core.unmute_all():
            print("could not start one or more unmute commands")
            return EXIT_ERROR
        print("unmute requests started; state not yet verified")
        return EXIT_OK

    if args.quit:
        reply = core.ipc_send("quit")
        print("quit" if reply else "no running instance")
        return EXIT_OK

    if args.remap:
        reply = core.ipc_send("remap")
        print(reply or "no running instance")
        return EXIT_OK

    if args.dump:
        reply = core.ipc_send("dump")
        print(reply or "no running instance")
        return EXIT_OK

    # Default and --show: a socket reply is the reliable IPC signal. A PID
    # file alone is not proof Hearth is running: old files can outlive a
    # session and the kernel may later reuse that PID for an unrelated process.
    # Launching the GTK Application is safe too: its application ID activates
    # the existing instance if IPC is temporarily unavailable.
    if core.ipc_send("show"):
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
            # Windows has a distinct native endpoint/session backend. Do not
            # run the Linux/PipeWire, systemd, or desktop-portal health probes.
            if _is_windows():
                from hearth import windows_status

                report = windows_status.text()
            else:
                # Imported lazily: the Linux report does not need GTK or the
                # legacy core's import-time PipeWire probes.
                from hearth import status

                report = status.text()
            print(report)
            return EXIT_OK

        return _dispatch(args)
    except HearthError as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
