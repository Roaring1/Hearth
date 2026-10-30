"""Regression tests for privacy-sensitive defaults in the debug bundle script."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "roaring_audio_debug_dump.sh"


def test_remote_windows_collection_is_explicitly_opt_in() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'WIN_IP="${WIN_IP:-}"' in source
    assert 'WIN_USER="${WIN_USER:-}"' in source
    assert 'WIN_KEY="${WIN_KEY:-$HOME/.ssh/roaring_win}"' in source
    assert '[[ -z "$WIN_IP" || -z "$WIN_USER" ]]' in source
    assert "set WIN_IP and WIN_USER to opt in" in source


def test_debug_dump_has_no_hardcoded_windows_endpoint_or_user_path() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "192.168.50.132" not in source
    assert 'WIN_USER="${WIN_USER:-roari}"' not in source
    assert "C:\\\\Users\\\\" not in source
    assert "[Environment]::GetFolderPath('LocalApplicationData')" in source


def test_windows_appdir_remote_command_preserves_quote_boundaries() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    lines = source.splitlines()
    command_index = next(i for i, line in enumerate(lines) if "Get-ChildItem (Join-Path" in line)
    command = "\n".join(lines[command_index - 1 : command_index + 1])
    harness = (
        "WIN_USER=demo WIN_IP=example.invalid WIN_KEY=/tmp/mock-key; "
        'ssh() { printf \'%s\\0\' "$@"; }; eval "$1"'
    )

    result = subprocess.run(
        ["bash", "-c", harness, "hearth-test", command],
        capture_output=True,
        check=True,
        timeout=5,
    )
    args = result.stdout.decode().split("\0")
    assert (
        args[-2]
        == (
            """powershell -NoProfile -NonInteractive -Command "Get-ChildItem """
            """(Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) """
            """'RoaringMic') | Select Name, LastWriteTime, Length | Format-Table -Auto" """
        ).rstrip()
    )
