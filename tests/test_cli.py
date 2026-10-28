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


def test_legacy_mixer_config_writer_updates_multiple_keys_atomically(
    tmp_path: Path,
) -> None:
    from hearth import legacy_core as core

    conf = tmp_path / "roaring_mixer.conf"
    conf.write_text(
        '# keep this comment\nASTRO_TARGET="old"\nLATENCY_MSEC="12"\nUNKNOWN="keep"\n',
        encoding="utf-8",
    )
    conf.chmod(0o640)

    assert core.write_conf_keys(
        conf,
        {
            "ASTRO_TARGET": "alsa_output.usb-Astro.stereo-game",
            "LATENCY_MSEC": "18",
        },
    )

    text = conf.read_text(encoding="utf-8")
    assert 'ASTRO_TARGET="alsa_output.usb-Astro.stereo-game"' in text
    assert 'LATENCY_MSEC="18"' in text
    assert 'UNKNOWN="keep"' in text
    assert "# keep this comment" in text
    assert conf.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.glob("*.tmp.*")) == []
    assert (tmp_path / ".roaring_mixer.conf.lock").stat().st_mode & 0o777 == 0o600


def test_legacy_mixer_config_writer_fails_closed_on_missing_invalid_or_symlinked_files(
    tmp_path: Path,
) -> None:
    from hearth import legacy_core as core

    absent = tmp_path / "absent.conf"
    assert core.write_conf_keys(absent, {"LATENCY_MSEC": "20"}) is False

    invalid = tmp_path / "invalid.conf"
    raw = bytes([65, 83, 84, 82, 79, 95, 84, 65, 82, 71, 69, 84, 61, 255])
    invalid.write_bytes(raw)
    assert core.write_conf_keys(invalid, {"LATENCY_MSEC": "20"}) is False
    assert invalid.read_bytes() == raw

    victim = tmp_path / "victim.conf"
    victim.write_text('ASTRO_TARGET="keep"\n', encoding="utf-8")
    link = tmp_path / "linked.conf"
    link.symlink_to(victim)
    assert core.write_conf_keys(link, {"ASTRO_TARGET": "overwrite"}) is False
    assert link.is_symlink()
    assert victim.read_text(encoding="utf-8") == 'ASTRO_TARGET="keep"\n'


def test_legacy_mixer_config_concurrent_updates_do_not_lose_keys(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from hearth import legacy_core as core

    conf = tmp_path / "roaring_mixer.conf"
    conf.write_text('ASTRO_TARGET="chat"\nLATENCY_MSEC="12"\n' + "# pad\n" * 20_000)
    start = Barrier(3)

    def update(values: dict[str, str]) -> bool:
        start.wait()
        return core.write_conf_keys(conf, values)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(update, {"ASTRO_TARGET": "game"})
        second = pool.submit(update, {"LATENCY_MSEC": "24"})
        start.wait()
        assert first.result()
        assert second.result()

    text = conf.read_text()
    assert 'ASTRO_TARGET="game"' in text
    assert 'LATENCY_MSEC="24"' in text


def test_moonlight_source_write_is_atomic_and_queues_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from hearth import legacy_core as core

    unit = tmp_path / "roaring-moonlight-mic.service"
    unit.write_text(
        "[Service]\n# preserve this note\n"
        "Environment=MIC_SOURCE=b2_mic\nExecStart=/usr/bin/example\n",
        encoding="utf-8",
    )
    unit.chmod(0o640)
    monkeypatch.setattr(core, "MOONLIGHT_SVC", unit)
    queued: list[list[object]] = []
    monkeypatch.setattr(core, "run_steps", lambda steps, **_kw: queued.append(list(steps)))

    assert core.set_moonlight_mic("b1_mic") is True

    updated = unit.read_text(encoding="utf-8")
    assert "Environment=MIC_SOURCE=b1_mic" in updated
    assert "# preserve this note" in updated
    assert "ExecStart=/usr/bin/example" in updated
    assert unit.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.glob("*.tmp.*")) == []
    assert (tmp_path / ".roaring-moonlight-mic.service.lock").stat().st_mode & 0o777 == 0o600
    assert queued == [
        [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "restart", "roaring-moonlight-mic.service"],
        ]
    ]


def test_moonlight_source_write_fails_closed_and_does_not_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from hearth import legacy_core as core

    missing = tmp_path / "missing.service"
    monkeypatch.setattr(core, "MOONLIGHT_SVC", missing)
    queued: list[object] = []
    monkeypatch.setattr(core, "run_steps", lambda steps, **_kw: queued.append(steps))

    assert core.set_moonlight_mic("b1_mic") is False
    assert queued == []

    invalid = tmp_path / "invalid.service"
    raw = bytes([91, 83, 101, 114, 118, 105, 99, 101, 93, 10, 255, 254])
    invalid.write_bytes(raw)
    monkeypatch.setattr(core, "MOONLIGHT_SVC", invalid)
    assert core.set_moonlight_mic("b1_mic") is False
    assert invalid.read_bytes() == raw
    assert queued == []

    no_assignment = tmp_path / "no-assignment.service"
    no_assignment.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    monkeypatch.setattr(core, "MOONLIGHT_SVC", no_assignment)
    assert core.set_moonlight_mic("b1_mic") is False
    assert "ExecStart=/bin/true" in no_assignment.read_text()
    assert queued == []

    victim = tmp_path / "victim.service"
    victim.write_text("Environment=MIC_SOURCE=b2_mic\n", encoding="utf-8")
    linked = tmp_path / "linked.service"
    linked.symlink_to(victim)
    monkeypatch.setattr(core, "MOONLIGHT_SVC", linked)
    assert core.set_moonlight_mic("b1_mic") is False
    assert linked.is_symlink()
    assert victim.read_text() == "Environment=MIC_SOURCE=b2_mic\n"
    assert queued == []


def test_moonlight_source_rejects_unknown_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from hearth import legacy_core as core

    unit = tmp_path / "moonlight.service"
    unit.write_text("Environment=MIC_SOURCE=b2_mic\n", encoding="utf-8")
    monkeypatch.setattr(core, "MOONLIGHT_SVC", unit)
    queued: list[object] = []
    monkeypatch.setattr(core, "run_steps", lambda steps, **_kw: queued.append(steps))

    assert core.set_moonlight_mic("$(touch /tmp/should-not-exist)") is False
    assert unit.read_text() == "Environment=MIC_SOURCE=b2_mic\n"
    assert queued == []


def test_show_does_not_trust_a_stale_pid_file(monkeypatch: pytest.MonkeyPatch) -> None:
    from hearth import legacy_core as core
    from hearth import ui

    monkeypatch.setattr(core, "ipc_send", lambda _command: False)
    # Simulate a stale PID file that happens to point at a live unrelated PID.
    monkeypatch.setattr(core, "_read_pid", lambda: 12345)
    monkeypatch.setattr(core, "_pid_alive", lambda _pid: True)
    launched: list[bool] = []
    monkeypatch.setattr(ui, "run_app", lambda: launched.append(True) or 37)

    args = cli.build_parser().parse_args(["--show"])
    assert cli._dispatch(args) == 37
    assert launched == [True]
