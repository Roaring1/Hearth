"""Console entry point."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import hearth
from hearth import cli, logging_setup


def test_version_flag_prints_and_exits_clean(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert hearth.__version__ in capsys.readouterr().out


def test_bad_flag_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--nonsense"])
    assert exc.value.code == cli.EXIT_USAGE


def test_paths_command_lists_every_location(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["--paths"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    for label in ("config", "state", "runtime", "socket", "log"):
        assert label in out


def test_logging_is_configured_once_and_only_from_here(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HEARTH_LOG_LEVEL", raising=False)
    logging_setup.configure("debug", to_file=False)
    logging_setup.configure("debug", to_file=False)
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1  # reconfiguring must not stack handlers


def test_unknown_level_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEARTH_LOG_LEVEL", "chatty")
    logging_setup.configure(None, to_file=False)
    assert logging.getLogger().level == getattr(logging, logging_setup.DEFAULT_LEVEL)
