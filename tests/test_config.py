"""Tests for the machine configuration and the user settings.

Both files are hand-edited or machine-written, so the cases that matter are
the malformed ones. Section 3.6 of the plan asked for exactly this: missing
file, empty file, malformed JSON, unknown keys, wrong types, all degrading
to defaults rather than crashing.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from hearth import config, settings

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
