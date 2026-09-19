"""XDG location resolution.

These cases are the ones that actually go wrong in the wild: variables unset,
variables set to a relative path (which the spec says must be ignored), and
the runtime directory being absent entirely.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hearth import paths

XDG_VARS = (
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for var in XDG_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_defaults_follow_the_spec(clean_env: Path) -> None:
    assert paths.config_dir() == clean_env / ".config" / "hearth"
    assert paths.state_dir() == clean_env / ".local" / "state" / "hearth"
    assert paths.cache_dir() == clean_env / ".cache" / "hearth"


def test_absolute_overrides_are_honoured(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert paths.config_dir() == tmp_path / "cfg" / "hearth"


@pytest.mark.parametrize("bad", ["relative/path", "./here", ".."])
def test_relative_overrides_are_ignored(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", bad)
    assert paths.state_dir() == clean_env / ".local" / "state" / "hearth"


def test_empty_override_is_ignored(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", "")
    assert paths.cache_dir() == clean_env / ".cache" / "hearth"


def test_socket_and_pid_live_in_the_runtime_dir(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    assert paths.socket_file().parent == tmp_path / "run" / "hearth"
    assert paths.pid_file().parent == tmp_path / "run" / "hearth"
    # Regression guard: these used to sit in the config directory.
    assert paths.config_dir() not in paths.socket_file().parents


def test_runtime_dir_falls_back_outside_home(clean_env: Path) -> None:
    runtime = paths.runtime_dir()
    assert runtime.is_absolute()
    assert clean_env not in runtime.parents
    assert str(os.getuid()) in runtime.name


def test_log_is_state_not_cache(clean_env: Path) -> None:
    assert paths.log_file().parent == paths.state_dir()
    assert paths.dumps_dir().parent == paths.state_dir()


def test_ensure_dirs_is_idempotent_and_private(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    paths.ensure_dirs()
    paths.ensure_dirs()
    for directory in (paths.config_dir(), paths.state_dir(), paths.dumps_dir()):
        assert directory.is_dir()
    assert paths.runtime_dir().stat().st_mode & 0o777 == 0o700
