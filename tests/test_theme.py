"""Tests for reading the desktop's palette."""

from __future__ import annotations

from pathlib import Path

import pytest

from hearth import theme

# Trimmed from the real kdeglobals on this rig: an odd repeated-bracket
# section, an alpha channel, and an accent that is not any Breeze default.
SAMPLE = """\
[Colors:Header][Inactive]
BackgroundNormal=51,51,51
ForegroundNormal=252,252,252,150

[Colors:View]
BackgroundNormal=36,36,36
ForegroundNormal=252,252,252

[Colors:Window]
BackgroundNormal=54,54,54
BackgroundAlternate=66,66,66
ForegroundNormal=252,252,252
ForegroundInactive=160,160,160

[General]
AccentColor=233,58,154
ColorSchemeHash=79296561e8832ef612d7c1138272b1e03e6ba66d

[Icons]
Theme=breeze
"""


def write_sample(tmp_path: Path, text: str = SAMPLE) -> Path:
    target = tmp_path / "kdeglobals"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def test_reads_the_desktop_palette(tmp_path: Path) -> None:
    result = theme.read(write_sample(tmp_path))
    assert result.from_desktop is True
    assert result.background == "#363636"
    assert result.accent == "#e93a9a"
    assert result.tokens["view-bg"] == "#242424"
    assert result.icon_theme == "breeze"


def test_repeated_bracket_sections_do_not_derail_the_parse(tmp_path: Path) -> None:
    """``[Colors:Header][Inactive]`` is not configparser grammar."""
    config = theme.parse_ini(SAMPLE)
    assert "Colors:Header][Inactive" in config
    assert config["Colors:Window"]["BackgroundNormal"] == "54,54,54"


def test_dark_is_judged_by_brightness_not_by_name(tmp_path: Path) -> None:
    """This desktop's look-and-feel package is literally called WhiteSur-dark."""
    dark = theme.read(write_sample(tmp_path))
    assert dark.is_dark is True

    light = write_sample(
        tmp_path / "light",
        SAMPLE.replace("BackgroundNormal=54,54,54", "BackgroundNormal=252,252,252"),
    )
    assert theme.read(light).is_dark is False


def test_translucent_colours_keep_their_alpha() -> None:
    assert theme.parse_colour("252,252,252,150") == "rgba(252, 252, 252, 0.588)"
    assert theme.parse_colour("252,252,252,255") == "#fcfcfc"


@pytest.mark.parametrize("value", ["", "blue", "1,2", "1,2,3,4,5", "300,0,0", "a,b,c"])
def test_nonsense_colours_are_refused(value: str) -> None:
    assert theme.parse_colour(value) is None


def test_a_missing_file_falls_back_and_says_so(tmp_path: Path) -> None:
    result = theme.read(tmp_path / "absent")
    assert result.from_desktop is False
    assert result.tokens == theme.FALLBACK
    assert "Hearth's own" in theme.describe(result)


def test_a_colourless_file_is_also_the_fallback(tmp_path: Path) -> None:
    target = write_sample(tmp_path, "[KDE]\nwidgetStyle=Breeze\n")
    result = theme.read(target)
    assert result.from_desktop is False
    assert result.tokens == theme.FALLBACK


def test_every_role_has_a_fallback() -> None:
    """A token with no fallback would resolve to nothing in the stylesheet."""
    for _section, _key, name in theme.ROLES:
        assert name in theme.FALLBACK
    assert "accent" in theme.FALLBACK


def test_css_declares_every_token(tmp_path: Path) -> None:
    result = theme.read(write_sample(tmp_path))
    css = result.css()
    assert css.startswith(":root {")
    for name, value in result.tokens.items():
        assert f"--{name}: {value};" in css


def test_describe_avoids_internal_names(tmp_path: Path) -> None:
    text = theme.describe(theme.read(write_sample(tmp_path)))
    assert "kdeglobals" not in text
    assert "Plasma" not in text
    assert text[0].isupper()
