"""One-shot, fail-closed route guardian for the configured Astro output.

This module is called by the long-running user service. It reads each
inventory once, plans repairs from the live PipeWire graph, and mutates only
the exact source/sink route whose ownership is unambiguous.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import logging
import math
import os
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from hearth import paths
from hearth.audio.pactl import Module, parse_modules, parse_short_list, select_loopbacks
from hearth.audio.route_health import (
    BusRouteHealth,
    LinkInventory,
    RecoveryAction,
    RecoveryPlan,
    RouteState,
    parse_link_inventory,
    plan_route_recovery,
    verify_bus,
)

log = logging.getLogger(__name__)
BUSES = ("vm_game", "vm_chat", "vm_music")
DEFAULT_COOLDOWN_SEC = 15.0


class InventoryError(RuntimeError):
    """A required PipeWire or Pulse inventory query failed."""


def _is_linux_runtime() -> bool:
    """Return the runtime platform without Win32 mypy narrowing module code."""
    return sys.platform.startswith("linux")


def _current_uid() -> int | None:
    """Return the POSIX UID, or None where the platform has no UID concept."""
    getter = getattr(os, "getuid", None)
    return int(getter()) if callable(getter) else None


def _require_current_uid() -> int:
    """Require an owner identity before accepting or changing private state."""
    uid = _current_uid()
    if uid is None:
        raise OSError("POSIX user ownership cannot be verified on this platform")
    return uid


def _fchmod(fd: int, mode: int) -> None:
    """Change an open file's mode, or fail closed where fchmod is unavailable."""
    operation = getattr(os, "fchmod", None)
    if not callable(operation):
        raise OSError("private file descriptor permissions are unavailable")
    cast(Callable[[int, int], None], operation)(fd, mode)


def _flock(fd: int, operation_name: str) -> None:
    """Apply a POSIX advisory lock without importing fcntl on non-Linux hosts."""
    if not _is_linux_runtime():
        raise OSError("route guardian file locking is supported only on Linux")
    module = importlib.import_module("fcntl")
    operation = getattr(module, operation_name, None)
    lock = getattr(module, "flock", None)
    if not isinstance(operation, int) or not callable(lock):
        raise OSError("POSIX advisory file locking is unavailable")
    cast(Callable[[int, int], None], lock)(fd, operation)


def _capture(argv: Sequence[str], *, timeout: float = 2.0) -> str:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InventoryError(f"{' '.join(argv)} failed: {exc}") from exc
    return result.stdout


def _collect(sink: str) -> tuple[bool, list[Module], LinkInventory]:
    try:
        sink_rows = parse_short_list(_capture(("pactl", "list", "short", "sinks")))
        modules = parse_modules(_capture(("pactl", "list", "short", "modules"), timeout=2.5))
        graph = parse_link_inventory(_capture(("pw-link", "-iol"), timeout=2.5))
    except InventoryError:
        raise
    except Exception as exc:
        raise InventoryError(f"could not parse route inventory: {exc}") from exc

    sink_present = any(row.name == sink for row in sink_rows)
    return sink_present, modules, graph


def _validate_private_regular_file(path: Path, *, missing_ok: bool = True) -> None:
    """Reject state paths that could redirect reads or share a sensitive inode."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return
        raise
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"refusing non-regular route recovery state file: {path}")
    if metadata.st_uid != _require_current_uid():
        raise PermissionError(f"route recovery state file is not owned by this user: {path}")
    if metadata.st_nlink != 1:
        raise OSError(f"refusing hard-linked route recovery state file: {path}")


def _open_state_lock(path: Path) -> int:
    """Open and lock one persistent private lock inode without following links."""
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"route recovery lock is not a regular file: {path}")
        if metadata.st_uid != _require_current_uid():
            raise PermissionError(f"route recovery lock is not owned by this user: {path}")
        if metadata.st_nlink != 1:
            raise OSError(f"refusing hard-linked route recovery lock: {path}")
        _fchmod(fd, 0o600)
        _flock(fd, "LOCK_EX")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _atomic_write(path: Path, payload: dict[str, float]) -> None:
    """Atomically publish private cooldown state after validating any old target."""
    paths.ensure_private_dir(path.parent)
    _validate_private_regular_file(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        _fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            Path(temporary).unlink()
        raise


def _load_attempts(path: Path) -> dict[str, float]:
    """Read only a private regular state file; invalid state fails closed."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return {}
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"route recovery state is not a regular file: {path}")
        if metadata.st_uid != _require_current_uid():
            raise PermissionError(f"route recovery state is not owned by this user: {path}")
        if metadata.st_nlink != 1:
            raise OSError(f"refusing hard-linked route recovery state: {path}")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = -1
            value = json.load(stream)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OSError(f"route recovery cooldown state is corrupt: {path}") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    if not isinstance(value, dict):
        raise OSError(f"route recovery cooldown state has an invalid shape: {path}")
    attempts: dict[str, float] = {}
    for key, timestamp in value.items():
        if (
            isinstance(key, str)
            and len(key) <= 512
            and isinstance(timestamp, (int, float))
            and not isinstance(timestamp, bool)
            and math.isfinite(float(timestamp))
            and timestamp >= 0
        ):
            attempts[key] = float(timestamp)
    return attempts


def _attempt_key(bus: str, sink: str) -> str:
    return f"{bus}|{sink}"


def _run_recovery_command(argv: Sequence[str]) -> str:
    return _capture(argv, timeout=3.0).strip()


def _create_loopback(bus: str, sink: str, latency_msec: int) -> str:
    return _run_recovery_command(
        (
            "pactl",
            "load-module",
            "module-loopback",
            f"source={bus}.monitor",
            f"sink={sink}",
            f"latency_msec={latency_msec}",
            "rate=48000",
            "channels=2",
            "channel_map=front-left,front-right",
            "remix=yes",
            "source_dont_move=true",
            "sink_dont_move=true",
        )
    )


def _plan_for_bus(
    bus: str,
    sink: str,
    sink_present: bool,
    modules: Sequence[Module],
    graph: LinkInventory,
) -> tuple[RecoveryPlan | None, BusRouteHealth]:
    owned = select_loopbacks(
        modules,
        source=f"{bus}.monitor",
        sinks=[sink],
        exact_source=True,
    )
    if len(owned) > 1:
        # Duplicate module ownership is known, but cannot be repaired without
        # deciding which module instance to preserve.
        observed = verify_bus(bus, sink, set(graph.edges), sink_present=sink_present)
        return None, BusRouteHealth(
            bus,
            RouteState.UNKNOWN,
            observed.verified_links,
            detail=f"{len(owned)} matching loopback modules; refusing to guess ownership",
        )
    plan = plan_route_recovery(
        bus,
        sink,
        graph,
        sink_present=sink_present,
        module_exists=bool(owned),
    )
    return plan, plan.health


def check_and_recover(
    sink: str,
    *,
    latency_msec: int = 12,
    state_file: Path | None = None,
    cooldown_sec: float = DEFAULT_COOLDOWN_SEC,
    buses: Sequence[str] = BUSES,
) -> list[str]:
    """Inspect all managed buses and perform only conservative targeted repairs."""
    if not sink or any(char.isspace() for char in sink):
        raise ValueError("sink must be a non-empty exact PipeWire sink name")
    if not 1 <= latency_msec <= 2000:
        raise ValueError("latency must be between 1 and 2000 ms")
    if cooldown_sec < 0:
        raise ValueError("cooldown must not be negative")
    if any(bus not in BUSES for bus in buses):
        raise ValueError("unsupported bus name")
    if not _is_linux_runtime():
        return [f"{bus}: unavailable (route guardian requires Linux/PipeWire)" for bus in buses]

    state_file = state_file or (Path.home() / ".cache" / "hearth" / "route-recovery-attempts.json")
    state_file = state_file.expanduser()
    if not state_file.is_absolute():
        state_file = Path.cwd() / state_file
    state_parent = paths.ensure_private_dir(state_file.parent)
    state_file = state_parent / state_file.name
    _validate_private_regular_file(state_file)
    lock_path = state_file.with_name(f".{state_file.name}.lock")
    lock_fd = _open_state_lock(lock_path)
    reports: list[str] = []

    try:
        try:
            sink_present, modules, graph = _collect(sink)
        except InventoryError as exc:
            return [f"{bus}: unknown (inventory query failed: {exc})" for bus in buses]

        if not sink_present:
            return [f"{bus}: unavailable (configured sink is absent: {sink})" for bus in buses]

        attempts = _load_attempts(state_file)
        now = time.time()

        for bus in buses:
            plan, health = _plan_for_bus(bus, sink, sink_present, modules, graph)
            if plan is None:
                reports.append(f"{bus}: {health.state.value} ({health.detail})")
                continue

            if plan.action is RecoveryAction.NONE:
                reports.append(
                    f"{bus}: {health.state.value} "
                    f"({health.verified_links}/4 expected links; {health.detail})"
                )
                continue

            key = _attempt_key(bus, sink)
            last_attempt = attempts.get(key, 0.0)
            remaining = cooldown_sec - (now - last_attempt)
            if remaining > 0:
                reports.append(
                    f"{bus}: {health.state.value} ({plan.action.value} cooldown {remaining:.1f}s)"
                )
                continue

            # Persist before attempting a mutation. A failing pw-link/pactl
            # must not be retried on every 5-second guardian poll.
            attempts[key] = now
            _atomic_write(state_file, attempts)

            try:
                if plan.action is RecoveryAction.CREATE_LOOPBACK:
                    module_id = _create_loopback(bus, sink, latency_msec)
                    reports.append(
                        f"{bus}: creating exact Chat route (module {module_id}); "
                        "awaiting graph links"
                    )
                    # A new Pulse loopback's PipeWire stream nodes are linked
                    # asynchronously by WirePlumber. Verify on the next poll.
                    continue

                if plan.action is RecoveryAction.CONNECT_LINKS:
                    repaired = 0
                    for output, input_port in plan.links:
                        _run_recovery_command(("pw-link", output, input_port))
                        repaired += 1
                    # Refresh once after the complete bounded repair batch.
                    new_sink_present, new_modules, new_graph = _collect(sink)
                    fresh, fresh_health = _plan_for_bus(
                        bus, sink, new_sink_present, new_modules, new_graph
                    )
                    if (
                        fresh is not None
                        and fresh_health.state is RouteState.HEALTHY
                        and fresh.action is RecoveryAction.NONE
                    ):
                        reports.append(f"{bus}: healthy (reconnected {repaired} missing link(s))")
                    else:
                        reports.append(
                            f"{bus}: {fresh_health.state.value} after repair "
                            f"({fresh_health.detail})"
                        )
                    # Reuse fresh inventories for the remaining buses.
                    sink_present, modules, graph = (
                        new_sink_present,
                        new_modules,
                        new_graph,
                    )
                    continue

            except (InventoryError, OSError, subprocess.SubprocessError) as exc:
                reports.append(f"{bus}: degraded (targeted recovery failed: {exc})")

        return reports
    finally:
        _flock(lock_fd, "LOCK_UN")
        os.close(lock_fd)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sink", required=True, help="exact configured output sink name")
    parser.add_argument("--latency", type=int, default=12, help="loopback latency in ms")
    parser.add_argument(
        "--state-file",
        type=Path,
        help="recovery cooldown state path (default: ~/.cache/hearth/...)",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=DEFAULT_COOLDOWN_SEC,
        help="minimum seconds between recovery attempts per bus and sink",
    )
    parser.add_argument(
        "--bus",
        action="append",
        choices=BUSES,
        help="check only this bus; may be supplied more than once (default: all)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="[route-guard] %(message)s")
    try:
        reports = check_and_recover(
            args.sink,
            latency_msec=args.latency,
            state_file=args.state_file,
            cooldown_sec=args.cooldown,
            buses=args.bus or BUSES,
        )
    except (OSError, ValueError) as exc:
        print(f"route-guard: unavailable ({exc})")
        return 0
    for report in reports:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
