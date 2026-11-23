"""Opt-in integration tests against a disposable, hardware-free PipeWire graph.

Run on Linux with PipeWire, WirePlumber and PipeWire-Pulse installed:

    HEARTH_RUN_PIPEWIRE_INTEGRATION=1 PYTHONPATH=src \
        python -m pytest -q tests/integration/test_pipewire_route_recovery.py

The test starts private server processes under a temporary XDG_RUNTIME_DIR.
Its WirePlumber profile disables audio, Bluetooth and video hardware monitors.
It never attaches to the desktop PipeWire core or opens physical devices.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from hearth.audio.pactl import parse_modules, select_loopbacks
from hearth.audio.route_health import (
    RecoveryAction,
    RouteState,
    parse_link_inventory,
    plan_route_recovery,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("HEARTH_RUN_PIPEWIRE_INTEGRATION") != "1",
    reason="set HEARTH_RUN_PIPEWIRE_INTEGRATION=1 to run disposable PipeWire tests",
)


class IsolatedPipeWire:
    """Private PipeWire + Pulse + WirePlumber with no hardware monitors."""

    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hearth-pw-integration-")
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(mode=0o700)
        self.state = self.root / "state"
        self.cache = self.root / "cache"
        self.config_home = self.root / "config"
        self.wp_home = self.root / "wp"
        for path in (self.state, self.cache, self.config_home, self.wp_home):
            path.mkdir(mode=0o700)
        self.processes: list[subprocess.Popen[bytes]] = []
        self.logs: dict[str, Path] = {}
        self.env = os.environ.copy()
        self.env.update(
            {
                "XDG_RUNTIME_DIR": str(self.runtime),
                "PIPEWIRE_RUNTIME_DIR": str(self.runtime),
                "PIPEWIRE_REMOTE": "hearth-test",
                "XDG_STATE_HOME": str(self.state),
                "XDG_CACHE_HOME": str(self.cache),
                "XDG_CONFIG_HOME": str(self.config_home),
                "PULSE_RUNTIME_PATH": str(self.runtime / "pulse"),
            }
        )
        for key in ("PIPEWIRE_CORE", "PIPEWIRE_DAEMON", "PULSE_SERVER"):
            self.env.pop(key, None)
        # Do not share the desktop session bus: its Pulse server name is
        # already owned by the live desktop. The sandbox needs no D-Bus device
        # reservation because every hardware monitor is disabled.
        self.env.pop("DBUS_SESSION_BUS_ADDRESS", None)

    def __enter__(self) -> IsolatedPipeWire:
        try:
            self._start_core()
            self._start_pulse()
            self._start_wireplumber()
            self._wait_for(Path(self.env["PULSE_RUNTIME_PATH"]) / "native")
            self.command("pactl", "info")
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _start(self, name: str, argv: list[str], env: dict[str, str]) -> None:
        log_path = self.root / f"{name}.log"
        with log_path.open("wb") as stream:
            process = subprocess.Popen(
                argv,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.processes.append(process)
        self.logs[name] = log_path

    def _start_core(self) -> None:
        source = Path("/usr/share/pipewire/minimal.conf")
        if not source.is_file():
            pytest.skip("PipeWire minimal.conf is not installed")
        config = source.read_text(encoding="utf-8")
        objects_start = config.index("context.objects = [")
        objects_end = config.index("\n]\n\ncontext.exec = [", objects_start) + 2
        # Replace the distro config's hardware objects with only a dummy driver.
        config = (
            config[:objects_start]
            + "context.objects = [ { factory = spa-node-factory args = { "
            + "factory.name = support.node.driver node.name = Dummy-Driver "
            + "node.group = pipewire.dummy priority.driver = 20000 } } ]\n"
            + config[objects_end:]
        )
        # The Pulse protocol listener is launched as a separate process below.
        module_start = config.index("    { name = libpipewire-module-protocol-pulse")
        module_end = config.index("\n    }", module_start) + 6
        config = config[:module_start] + config[module_end:]
        config_path = self.root / "pipewire.conf"
        config_path.write_text(config, encoding="utf-8")

        server_env = self.env.copy()
        server_env.pop("PIPEWIRE_REMOTE", None)
        server_env["PIPEWIRE_CORE"] = "hearth-test"
        server_env["PIPEWIRE_DAEMON"] = "true"
        self._start("pipewire", ["pipewire", "-c", str(config_path)], server_env)
        self._wait_for(self.runtime / "hearth-test")
        self.command("pw-cli", "-r", "hearth-test", "info", "0")

    def _start_pulse(self) -> None:
        pulse_env = self.env.copy()
        pulse_env.pop("PIPEWIRE_CORE", None)
        pulse_env.pop("PIPEWIRE_DAEMON", None)
        pulse_env.pop("PULSE_SERVER", None)
        self._start(
            "pipewire-pulse",
            ["pipewire-pulse", "-c", "/usr/share/pipewire/pipewire-pulse.conf"],
            pulse_env,
        )

    def _start_wireplumber(self) -> None:
        source = Path("/usr/share/wireplumber/wireplumber.conf")
        if not source.is_file():
            pytest.skip("WirePlumber configuration is not installed")
        config = source.read_text(encoding="utf-8")
        profile_start = config.index("  main = {")
        profile_end = config.index("\n  }", profile_start)
        profile = config[profile_start:profile_end]
        for feature in ("hardware.audio", "hardware.bluetooth", "hardware.video-capture"):
            profile = re.sub(
                rf"{re.escape(feature)}\s*=\s*required",
                f"{feature} = disabled",
                profile,
            )
        custom_config = self.wp_home / "wireplumber.conf"
        custom_config.write_text(
            config[:profile_start] + profile + config[profile_end:],
            encoding="utf-8",
        )
        self._start(
            "wireplumber",
            ["wireplumber", "-c", str(custom_config)],
            self.env,
        )

    def _wait_for(self, path: Path, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return
            self._assert_processes_alive()
            time.sleep(0.05)
        self._assert_processes_alive()
        raise RuntimeError(f"timed out waiting for {path}; logs: {self.log_text()}")

    def _assert_processes_alive(self) -> None:
        for process in self.processes:
            if process.poll() is not None:
                raise RuntimeError(
                    f"isolated process exited ({process.returncode}); logs: {self.log_text()}"
                )

    def command(self, *argv: str) -> str:
        result = subprocess.run(
            argv,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
            timeout=4,
        )
        return result.stdout.strip()

    def log_text(self) -> str:
        return "\n".join(
            f"--- {name} ---\n{path.read_text(errors='replace')}"
            for name, path in self.logs.items()
            if path.exists()
        )

    def close(self) -> None:
        for process in reversed(self.processes):
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
        for process in reversed(self.processes):
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
        self.temp.cleanup()


def _inventory(session: IsolatedPipeWire):
    return parse_link_inventory(session.command("pw-link", "-iol"))


def _module_exists(session: IsolatedPipeWire, bus: str, sink: str) -> bool:
    modules = parse_modules(session.command("pactl", "list", "short", "modules"))
    return bool(
        select_loopbacks(
            modules,
            source=f"{bus}.monitor",
            sinks=[sink],
            exact_source=True,
        )
    )


def _plan(session: IsolatedPipeWire, bus: str, sink: str):
    return plan_route_recovery(
        bus,
        sink,
        _inventory(session),
        sink_present=True,
        module_exists=_module_exists(session, bus, sink),
    )


def _wait_for_plan(
    session: IsolatedPipeWire,
    bus: str,
    sink: str,
    state: RouteState,
    action: RecoveryAction,
    timeout: float = 3.0,
):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = _plan(session, bus, sink)
        if latest.health.state is state and latest.action is action:
            return latest
        time.sleep(0.05)
    raise AssertionError((latest, _inventory(session), session.log_text()[-2500:]))


def _run_route_guard(session: IsolatedPipeWire, bus: str, sink: str) -> str:
    return session.command(
        "python3",
        "-m",
        "hearth.audio.route_guard",
        "--sink",
        sink,
        "--bus",
        bus,
        "--state-file",
        str(session.state / "route-attempts.json"),
        "--cooldown",
        "0",
    )


def _repair_once(session: IsolatedPipeWire, bus: str, sink: str) -> None:
    report = _run_route_guard(session, bus, sink)
    assert "reconnected 1 missing link" in report, report
    verified = _wait_for_plan(session, bus, sink, RouteState.HEALTHY, RecoveryAction.NONE)
    assert verified.health.verified_links == 4


def test_real_module_loopback_repairs_both_stereo_sides_and_is_idempotent() -> None:
    required = ("pipewire", "pipewire-pulse", "wireplumber", "pactl", "pw-link", "pw-cli")
    missing = [name for name in required if shutil.which(name) is None]
    if missing:
        pytest.skip(f"missing integration prerequisites: {', '.join(missing)}")

    with IsolatedPipeWire() as session:
        bus, sink = "vm_game", "astro_chat"
        session.command(
            "pactl",
            "load-module",
            "module-null-sink",
            f"sink_name={bus}",
            "channels=2",
            "channel_map=front-left,front-right",
        )
        session.command(
            "pactl",
            "load-module",
            "module-null-sink",
            f"sink_name={sink}",
            "channels=2",
            "channel_map=front-left,front-right",
        )
        created = _run_route_guard(session, bus, sink)
        assert "creating exact Chat route" in created, created

        initial = _wait_for_plan(session, bus, sink, RouteState.HEALTHY, RecoveryAction.NONE)
        assert initial.health.verified_links == 4
        assert initial.action is RecoveryAction.NONE

        # Break and restore one capture-side stereo edge.
        capture_edge = next(
            (source, target)
            for source, target in _inventory(session).edges
            if source == f"{bus}:monitor_FR" and target.startswith("input.loopback-")
        )
        session.command("pw-link", "-d", *capture_edge)
        _repair_once(session, bus, sink)

        # Break and restore one playback-side stereo edge.
        playback_edge = next(
            (source, target)
            for source, target in _inventory(session).edges
            if source.startswith("output.loopback-") and target == f"{sink}:playback_FL"
        )
        session.command("pw-link", "-d", *playback_edge)
        _repair_once(session, bus, sink)

        modules = parse_modules(session.command("pactl", "list", "short", "modules"))
        owned = select_loopbacks(modules, source=f"{bus}.monitor", sinks=[sink], exact_source=True)
        assert len(owned) == 1, owned
