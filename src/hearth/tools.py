"""The companion tooling, described once so the app can offer all of it.

The rig grew a folder of shell and Python helpers that only a terminal can
reach: diagnostics, a mic capture suite, speaker-bleed analysis, Carla
control, a full stack restart. The stated goal is that a person who never
opens a terminal can still use every one of them, so they are described
here as data -- label, one-line purpose, argv, and whether they are
destructive -- and the UI renders whatever is actually installed.

Rules that make this safe to expose in a GUI:

* Nothing here imports ``gi``; this is a registry, not a widget.
* Every entry is argv, never a shell string.
* ``confirm`` marks anything that interrupts audio. The UI must ask first.
* ``missing`` tools are reported, not hidden, so a user can tell the
  difference between "this rig does not have that" and "the button is gone".
* Long-running tools are spawned detached, so quitting Hearth never kills a
  restart halfway through.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from hearth import paths, proc
from hearth.errors import AudioError

log = logging.getLogger(__name__)

#: How long a captured tool may run before we stop waiting for it.
CAPTURE_TIMEOUT = 45.0


@dataclass(frozen=True, slots=True)
class Tool:
    """One companion script, described for a non-technical audience."""

    key: str
    label: str
    purpose: str
    script: str
    args: tuple[str, ...] = ()
    group: str = "Diagnostics"
    #: True when running it can interrupt audio; the UI must confirm first.
    confirm: bool = False
    #: True when the output is the point (show it), False for fire-and-forget.
    captures_output: bool = True
    #: Non-empty when the tool needs one thing chosen first -- the text is
    #: what to ask the person for, e.g. "a recording to analyse". The UI
    #: shows a picker instead of a bare button.
    needs_target: str = ""
    #: Where that picker finds its choices: a directory under home, and the
    #: prefix its session folders use. Declared here so the UI does not have
    #: to know one script's filing habits.
    target_dir: str = ""
    target_prefix: str = ""

    @property
    def path(self) -> Path:
        return paths.home() / "bin" / self.script

    @property
    def installed(self) -> bool:
        return self.path.is_file()

    @property
    def argv(self) -> list[str]:
        return [str(self.path), *self.args]

    def targets(self) -> list[Path]:
        """Candidate things to work on, newest first.

        An empty list is a real answer -- it means nothing has been
        recorded yet, which the UI should say rather than showing an
        empty menu.
        """
        if not self.target_dir:
            return []
        root = paths.home() / self.target_dir
        try:
            found = [
                p for p in root.iterdir() if p.is_dir() and p.name.startswith(self.target_prefix)
            ]
        except OSError:
            return []
        return sorted(found, key=lambda p: p.name, reverse=True)

    def argv_for(self, target: str | Path | None = None) -> list[str]:
        """argv including *target*, refusing the ambiguous cases.

        A tool that needs a target and is handed none would otherwise run
        against whatever default the script picks, which for an analyser
        means silently reporting on the wrong recording.
        """
        if self.needs_target and not target:
            raise AudioError(f"{self.label} needs {self.needs_target} first")
        if target and not self.needs_target:
            raise AudioError(f"{self.label} does not take anything to work on")
        if not target:
            return self.argv
        return [*self.argv, str(target)]


# Ordered by how often a person actually needs them, not alphabetically.
REGISTRY: tuple[Tool, ...] = (
    Tool(
        key="debug_dump",
        label="Collect a diagnostic report",
        purpose="Writes a full snapshot of the audio stack to share when something is wrong.",
        script="roaring_audio_debug_dump.sh",
        group="Diagnostics",
    ),
    Tool(
        key="mic_diag",
        label="Check the microphones",
        purpose="Reports which mic buses exist, their levels and any broken routing.",
        script="mic_diag.sh",
        group="Diagnostics",
    ),
    Tool(
        key="speaker_bleed",
        label="Check for speaker bleed",
        purpose="Measures how much speaker sound is leaking into the microphone.",
        script="speaker_bleed_check.sh",
        group="Diagnostics",
    ),
    Tool(
        key="speaker_bleed_analyze",
        label="Explain a speaker bleed test",
        purpose="Works out how loud the leak is, how far it travelled, and which tones carry.",
        script="speaker_bleed_analyze.py",
        group="Diagnostics",
        needs_target="a bleed test to explain",
        target_dir="audio_diagnostics/bleed_tests",
        target_prefix="test_",
    ),
    Tool(
        key="mic_dashboard",
        label="Build the microphone report",
        purpose="Renders the recorded mic analysis into a readable dashboard.",
        script="mic_render_dashboard.sh",
        group="Diagnostics",
    ),
    Tool(
        key="mic_analyze",
        label="Analyse a recording",
        purpose="Studies one recording in depth. Takes several minutes and runs in the background.",
        script="mic_analyze.py",
        group="Microphone",
        captures_output=False,
        needs_target="a recording to analyse",
        target_dir="audio_diagnostics",
        target_prefix="session_",
    ),
    Tool(
        key="mic_daemon_status",
        label="Mic recorder status",
        purpose="Shows whether the background microphone recorder is running.",
        script="mic_daemon_status.sh",
        group="Microphone",
    ),
    Tool(
        key="mic_daemon_start",
        label="Start the mic recorder",
        purpose="Begins recording microphone sessions for later analysis.",
        script="mic_daemon_start.sh",
        group="Microphone",
        captures_output=False,
    ),
    Tool(
        key="mic_daemon_stop",
        label="Stop the mic recorder",
        purpose="Stops the background microphone recorder.",
        script="mic_daemon_stop.sh",
        group="Microphone",
        captures_output=False,
    ),
    Tool(
        key="mic_listen",
        label="Listen to my microphone",
        purpose="Toggles monitoring so you can hear yourself in the headset.",
        script="toggle_mic_listen.sh",
        group="Microphone",
        captures_output=False,
    ),
    Tool(
        key="scarlett_speakers",
        label="Switch speakers on or off",
        purpose="Toggles the Scarlett monitor speakers without touching the headset.",
        script="toggle_scarlett_speakers.sh",
        group="Outputs",
        captures_output=False,
    ),
    Tool(
        key="astro_target",
        label="Swap the headset channel",
        purpose="Moves the mix between the Astro's Game and Chat channels.",
        script="roaring_toggle_astro_target.sh",
        group="Outputs",
        confirm=True,
        captures_output=False,
    ),
    Tool(
        key="carla_launch",
        label="Open the effects rack",
        purpose="Launches Carla, which hosts the room-correction and mic effects.",
        script="roaring_carla_launch.sh",
        group="Effects",
        captures_output=False,
    ),
    Tool(
        key="carla_backup",
        label="Back up the effects rack",
        purpose="Saves a copy of the current Carla patch.",
        script="roaring_carla_backup.sh",
        group="Effects",
    ),
    Tool(
        key="noise_status",
        label="Room correction status",
        purpose="Shows whether noise and room correction are currently applied.",
        script="no_noise_ctl.sh",
        args=("status",),
        group="Effects",
    ),
    Tool(
        key="restart_stack",
        label="Restart all audio",
        purpose="Rebuilds every bus and route. Sound stops for a few seconds.",
        script="roaring_restart_everything.sh",
        group="Repair",
        confirm=True,
        captures_output=False,
    ),
    Tool(
        key="start_with_carla",
        label="Start audio and effects",
        purpose="Brings the whole rig up, including the effects rack.",
        script="start_roaring_and_carla.sh",
        group="Repair",
        confirm=True,
        captures_output=False,
    ),
    Tool(
        key="padfire_restart",
        label="Restart the pad controller",
        purpose="Reconnects the LPD8 pad surface when it stops responding.",
        script="padfire-restart.sh",
        group="Repair",
        captures_output=False,
    ),
    Tool(
        key="share_repair",
        label="Fix screen share audio",
        purpose="Rebuilds the game and music feed a screen share picks up, when it is silent.",
        script="roaring_vm_share_loopback.sh",
        group="Repair",
        captures_output=False,
    ),
    Tool(
        key="cd_player",
        label="Open the CD player",
        purpose="Shows the disc in the drive with cover art, track list and transport controls.",
        script="roaring-cd-player",
        group="Discs",
        captures_output=False,
    ),
    Tool(
        key="disc_scan",
        label="Look for a disc already in the drive",
        purpose="Picks up a CD that was inserted before the player was listening.",
        script="roaring-presence-scan",
        group="Discs",
    ),
)

# Deliberately absent: roaring_nvidia_powercap.sh. It caps the GPU, not
# anything audio, and its own unit has already applied it by the time
# Hearth starts -- a button here would only invite someone to re-run a
# setting that is already in force.

GROUP_ORDER: tuple[str, ...] = (
    "Diagnostics",
    "Microphone",
    "Outputs",
    "Effects",
    "Discs",
    "Repair",
)


def get(key: str) -> Tool:
    """Look a tool up by key."""
    for tool in REGISTRY:
        if tool.key == key:
            return tool
    raise KeyError(key)


def installed() -> list[Tool]:
    """Only the tools that exist on this machine."""
    return [t for t in REGISTRY if t.installed]


def missing() -> list[Tool]:
    """Tools the registry knows about but this machine does not have."""
    return [t for t in REGISTRY if not t.installed]


def grouped(*, only_installed: bool = True) -> Iterator[tuple[str, list[Tool]]]:
    """Yield ``(group, tools)`` in display order, skipping empty groups."""
    pool = installed() if only_installed else list(REGISTRY)
    for group in GROUP_ORDER:
        members = [t for t in pool if t.group == group]
        if members:
            yield group, members


def run(tool: Tool, target: str | Path | None = None, *, timeout: float = CAPTURE_TIMEOUT) -> str:
    """Run *tool* and return its output.

    Raises :class:`AudioError` rather than letting a missing script or a
    non-zero exit reach the UI as a traceback.
    """
    if not tool.installed:
        raise AudioError(f"{tool.label}: {tool.script} is not installed")
    argv = tool.argv_for(target)
    log.info("tool: running %s", tool.key)
    result = proc.run(argv, timeout=timeout, check=False)
    text = result.stdout or result.stderr
    if not result.ok:
        raise AudioError(f"{tool.label} failed (exit {result.returncode}):\n{text.strip()}")
    return text


def spawn(tool: Tool, target: str | Path | None = None) -> None:
    """Start *tool* detached, for anything that outlives the click."""
    if not tool.installed:
        raise AudioError(f"{tool.label}: {tool.script} is not installed")
    argv = tool.argv_for(target)
    log.info("tool: spawning %s", tool.key)
    proc.spawn(argv)


def invoke(tool: Tool, target: str | Path | None = None) -> str:
    """Run or spawn *tool* according to its own declaration.

    One entry point means the UI never has to decide which of the two a
    given button needs -- that knowledge lives with the tool.
    """
    if tool.captures_output:
        return run(tool, target)
    spawn(tool, target)
    return f"{tool.label}: started"
