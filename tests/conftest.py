"""Shared test configuration.

Tests must never touch the developer's real config, state or audio graph, so
the home directory is redirected for every test by default.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    sandbox = tmp_path / "home"
    sandbox.mkdir()
    monkeypatch.setenv("HOME", str(sandbox))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(sandbox / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(sandbox / ".local/state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(sandbox / ".cache"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(sandbox / "run"))
    return sandbox


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_text():
    """Read a recorded command output from ``tests/fixtures/``."""

    def _read(name: str) -> str:
        return (FIXTURES / name).read_text()

    return _read


def _display_available() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip GUI-marked tests when there is no display or no PyGObject."""
    try:
        import gi  # noqa: F401

        have_gi = True
    except ImportError:
        have_gi = False

    if have_gi and _display_available():
        return

    reason = "needs PyGObject" if not have_gi else "needs a display (try xvfb-run)"
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "gui" in item.keywords:
            item.add_marker(skip)
