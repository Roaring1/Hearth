from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "roaring_audio_routesd.sh"


def _function_source() -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("find_astro_target() {")
    end = source.index("\n}\n", start) + 3
    return source[start:end]


def _run_target_selector(target: str, sinks: str) -> subprocess.CompletedProcess[str]:
    harness = (
        "set -euo pipefail\n"
        f"_sinks_cache={shlex.quote(sinks)}\n"
        "sink_in_cache() { "
        "printf '%s\\n' \"$_sinks_cache\" | awk '{print $2}' "
        '| grep -Fqx -- "$1"; }\n'
        "log() { printf 'LOG %s\\n' \"$*\"; }\n"
        f"{_function_source()}\n"
        f"ASTRO_TARGET={shlex.quote(target)}\n"
        "find_astro_target\n"
    )
    return subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        check=False,
    )


def test_game_only_sink_inventory_never_falls_back_to_game() -> None:
    result = _run_target_selector(
        "alsa_output.usb-Astro.stereo-chat",
        "1 alsa_output.usb-Astro.stereo-game RUNNING\n",
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "UNAVAILABLE" in result.stderr
    assert "stereo-game" not in result.stdout


def test_chat_is_selected_when_both_astro_endpoints_exist() -> None:
    result = _run_target_selector(
        "alsa_output.usb-Astro.stereo-chat",
        "1 alsa_output.usb-Astro.stereo-game RUNNING\n"
        "2 alsa_output.usb-Astro.stereo-chat SUSPENDED\n",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "alsa_output.usb-Astro.stereo-chat"
    assert result.stderr == ""


def test_unset_target_fails_closed() -> None:
    result = _run_target_selector("", "1 alsa_output.usb-Astro.stereo-chat RUNNING\n")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "unset" in result.stderr


def test_unavailable_target_does_not_terminate_daemon_loop_under_errexit() -> None:
    harness = (
        "set -euo pipefail\n"
        "_sinks_cache='1 alsa_output.usb-Astro.stereo-game RUNNING'\n"
        "sink_in_cache() { "
        "printf '%s\\n' \"$_sinks_cache\" | awk '{print $2}' "
        '| grep -Fqx -- "$1"; }\n'
        "log() { printf 'LOG %s\\n' \"$*\"; }\n"
        f"{_function_source()}\n"
        "ASTRO_TARGET='alsa_output.usb-Astro.stereo-chat'\n"
        "target=''\n"
        "if target=\"$(find_astro_target)\"; then :; else printf 'RETRY\\n'; fi\n"
        "printf 'LOOP-ALIVE\\n'\n"
    )
    result = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == "RETRY\nLOOP-ALIVE\n"
    assert "UNAVAILABLE" in result.stderr
