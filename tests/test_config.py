"""Tests for the machine configuration and the user settings.

Both files are hand-edited or machine-written, so the cases that matter are
the malformed ones. Section 3.6 of the plan asked for exactly this: missing
file, empty file, malformed JSON, unknown keys, wrong types, all degrading
to defaults rather than crashing.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest

from hearth import config, paths, settings

# --------------------------------------------------------------------------
# config.json
# --------------------------------------------------------------------------


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    loaded = config.load(tmp_path / "nope.json")
    assert loaded == config.Config()


@pytest.mark.parametrize("text", ["", "   ", "{", "not json at all", "null"])
def test_unusable_content_degrades_to_defaults(tmp_path: Path, text: str) -> None:
    target = tmp_path / "config.json"
    target.write_text(text)
    assert config.load(target) == config.Config()


@pytest.mark.parametrize("payload", ["[]", '"a string"', "42"])
def test_json_that_is_not_an_object_degrades_to_defaults(tmp_path: Path, payload: str) -> None:
    target = tmp_path / "config.json"
    target.write_text(payload)
    assert config.load(target) == config.Config()


def test_one_bad_value_does_not_cost_the_good_ones(tmp_path: Path) -> None:
    """A typo in one line must not throw away the rest of the file."""
    target = tmp_path / "config.json"
    target.write_text(
        json.dumps({"laptop_rtp_port": "forty-six thousand", "laptop_host": "studio"})
    )
    loaded = config.load(target)
    assert loaded.laptop_host == "studio"
    assert loaded.laptop_rtp_port == config.Config().laptop_rtp_port


def test_a_boolean_is_not_accepted_as_a_port() -> None:
    """``True`` is an ``int`` in Python; accepting it would fail much later,
    somewhere far less obvious than here."""
    assert config.parse({"laptop_rtp_port": True}).laptop_rtp_port == 46000


@pytest.mark.parametrize("port", [1, 46000, 65535])
def test_valid_rtp_port_boundaries_are_accepted(port: int) -> None:
    assert config.parse({"laptop_rtp_port": port}).laptop_rtp_port == port


@pytest.mark.parametrize("port", [-1, 0, 65536, 1000000])
def test_invalid_rtp_port_falls_back_to_default(port: int) -> None:
    assert (
        config.parse({"laptop_rtp_port": port}).laptop_rtp_port == config.Config().laptop_rtp_port
    )


def test_unknown_keys_are_reported_rather_than_silently_ignored() -> None:
    """A typo'd key is otherwise invisible: the setting just does nothing and
    the user concludes the feature is broken."""
    raw = {"laptop_host": "studio", "laptop_prot": 46000, "wat": 1}
    assert config.unknown_keys(raw) == ["laptop_prot", "wat"]
    assert config.parse(raw).laptop_host == "studio"


def test_values_are_read(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text(json.dumps({"monitor_sink_match": "Scarlett", "laptop_rtp_port": 47000}))
    loaded = config.load(target)
    assert loaded.monitor_sink_match == "Scarlett"
    assert loaded.laptop_rtp_port == 47000


def test_the_ssh_key_path_expands_a_tilde() -> None:
    loaded = config.parse({"ssh_key": "~/.ssh/id_ed25519"})
    key = loaded.ssh_key_path
    assert key is not None
    assert "~" not in str(key)
    assert config.Config().ssh_key_path is None


def test_the_published_defaults_name_no_real_machine() -> None:
    """This file is published. A serial number, hostname or IP address must
    never be a default in the source."""
    blob = json.dumps(dataclasses.asdict(config.Config()))
    for banned in ("192.168.", "10.0.", "/home/", "roaring"):
        assert banned not in blob


# --------------------------------------------------------------------------
# rac_settings.json
# --------------------------------------------------------------------------


def test_settings_missing_file_gives_the_defaults(tmp_path: Path) -> None:
    assert settings.load(tmp_path / "nope.json") == settings.DEFAULTS


def test_settings_malformed_file_gives_the_defaults(tmp_path: Path) -> None:
    target = tmp_path / "rac_settings.json"
    target.write_text("{ truncated")
    assert settings.load(target) == settings.DEFAULTS


def test_a_bad_setting_does_not_reset_the_others() -> None:
    merged = settings.merge({"vu_speed": "fast", "win_w": 1200})
    assert merged["win_w"] == 1200
    assert merged["vu_speed"] == settings.DEFAULTS["vu_speed"]


def test_a_checkbox_setting_rejects_a_number() -> None:
    assert settings.merge({"autoheal": 1})["autoheal"] is True
    assert settings.merge({"autoheal": False})["autoheal"] is False


def test_an_int_is_accepted_where_a_float_is_expected() -> None:
    """``"vu_speed": 1`` is a perfectly reasonable thing to hand-write."""
    assert settings.merge({"vu_speed": 1})["vu_speed"] == 1


@pytest.mark.parametrize("latency", [1, 12, 200])
def test_latency_milliseconds_accepts_setup_widget_range(latency: int) -> None:
    assert settings.merge({"latency_msec": latency})["latency_msec"] == latency


@pytest.mark.parametrize("latency", [-100, 0, 201, 1000000])
def test_invalid_latency_falls_back_to_default(latency: int) -> None:
    assert (
        settings.merge({"latency_msec": latency})["latency_msec"]
        == settings.DEFAULTS["latency_msec"]
    )


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), 10**400],
)
def test_non_finite_or_unrepresentable_float_preferences_are_rejected(
    value: int | float,
) -> None:
    assert settings.merge({"vu_speed": value})["vu_speed"] == settings.DEFAULTS["vu_speed"]


@pytest.mark.parametrize("key,value", [("win_w", -1), ("win_w", 1), ("win_h", 0), ("win_h", 10**9)])
def test_invalid_window_dimensions_fall_back_to_defaults(key: str, value: int) -> None:
    assert settings.merge({key: value})[key] == settings.DEFAULTS[key]


def test_unknown_settings_survive_a_downgrade() -> None:
    """A file written by a newer Hearth must not lose keys when an older one
    opens and re-saves it."""
    assert settings.merge({"a_future_setting": "keep me"})["a_future_setting"] == "keep me"


def test_saving_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "rac_settings.json"
    values = dict(settings.DEFAULTS)
    values["win_w"] = 1234
    settings.save(values, target)
    assert settings.load(target)["win_w"] == 1234


def test_saving_creates_the_directory(tmp_path: Path) -> None:
    target = tmp_path / "fresh" / "rac_settings.json"
    settings.save(dict(settings.DEFAULTS), target)
    assert target.exists()


def test_saving_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    target = tmp_path / "rac_settings.json"
    settings.save(dict(settings.DEFAULTS), target)
    assert list(tmp_path.glob("*.tmp.*")) == []


def test_saving_repairs_permissive_settings_file_permissions(tmp_path: Path) -> None:
    target = tmp_path / "rac_settings.json"
    settings.save(dict(settings.DEFAULTS), target)
    target.chmod(0o644)

    settings.save({**settings.DEFAULTS, "win_w": 1234}, target)

    if os.name == "nt":
        paths.ensure_private_file(target)
    else:
        assert target.stat().st_mode & 0o777 == 0o600
    assert settings.load(target)["win_w"] == 1234


def test_saving_refuses_a_symlink_settings_target(tmp_path: Path) -> None:
    victim = tmp_path / "victim.json"
    victim.write_text('{"private": "keep"}', encoding="utf-8")
    target = tmp_path / "rac_settings.json"
    target.symlink_to(victim)

    with pytest.raises(OSError, match="non-regular settings path"):
        settings.save(dict(settings.DEFAULTS), target)

    assert target.is_symlink()
    assert victim.read_text(encoding="utf-8") == '{"private": "keep"}'


def test_concurrent_saves_do_not_collide_or_corrupt_json(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    target = tmp_path / "rac_settings.json"

    def save_width(width: int) -> None:
        settings.save({**settings.DEFAULTS, "win_w": width}, target)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(save_width, range(800, 840)))

    loaded = json.loads(target.read_text())
    assert loaded["win_w"] in range(800, 840)
    assert list(tmp_path.glob("*.tmp.*")) == []


def test_a_failed_save_leaves_the_previous_file_intact(tmp_path: Path) -> None:
    """The whole point of the atomic write: a save that dies midway must not
    truncate the settings and silently reset every preference."""
    target = tmp_path / "rac_settings.json"
    settings.save({"win_w": 900}, target)

    class Unserialisable:
        pass

    with pytest.raises(TypeError):
        settings.save({"win_w": Unserialisable()}, target)

    assert json.loads(target.read_text()) == {"win_w": 900}
