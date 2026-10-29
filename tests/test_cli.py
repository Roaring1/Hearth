"""Console entry point."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import hearth
from hearth import cli, logging_setup, paths


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


def test_file_logging_creates_private_state_and_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    try:
        logging_setup.configure("info", to_file=True)

        assert paths.state_dir().stat().st_mode & 0o777 == 0o700
        assert paths.log_file().stat().st_mode & 0o777 == 0o600
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in previous_handlers:
            root.addHandler(handler)
        root.setLevel(previous_level)


def test_file_logging_refuses_a_hard_linked_log_without_modifying_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    target = paths.log_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    victim = target.parent / "unrelated.txt"
    victim.write_text("keep this file", encoding="utf-8")
    victim.chmod(0o644)
    original_mode = victim.stat().st_mode & 0o777
    target.hardlink_to(victim)

    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    try:
        logging_setup.configure("info", to_file=True)
        assert target.exists()
        assert target.stat().st_nlink == 2
        assert target.read_text(encoding="utf-8") == "keep this file"
        assert victim.read_text(encoding="utf-8") == "keep this file"
        assert victim.stat().st_mode & 0o777 == original_mode
        assert not any(
            isinstance(handler, logging.handlers.RotatingFileHandler) for handler in root.handlers
        )
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in previous_handlers:
            root.addHandler(handler)
        root.setLevel(previous_level)


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


def test_legacy_mixer_config_refuses_a_hard_linked_lock_without_chmodding_target(
    tmp_path: Path,
) -> None:
    from hearth import legacy_core as core

    conf = tmp_path / "roaring_mixer.conf"
    original = 'ASTRO_TARGET="chat"\nLATENCY_MSEC="12"\n'
    conf.write_text(original, encoding="utf-8")
    victim = tmp_path / "outside.txt"
    victim.write_text("leave this alone", encoding="utf-8")
    original_mode = victim.stat().st_mode & 0o777
    (tmp_path / ".roaring_mixer.conf.lock").hardlink_to(victim)

    assert core.write_conf_keys(conf, {"LATENCY_MSEC": "20"}) is False
    assert conf.read_text(encoding="utf-8") == original
    assert victim.read_text(encoding="utf-8") == "leave this alone"
    assert victim.stat().st_mode & 0o777 == original_mode


def test_legacy_mixer_config_refuses_a_foreign_owned_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from hearth import legacy_core as core

    conf = tmp_path / "roaring_mixer.conf"
    original = 'ASTRO_TARGET="chat"\nLATENCY_MSEC="12"\n'
    conf.write_text(original, encoding="utf-8")
    real_lstat = Path.lstat

    def report_foreign_owner(path):
        metadata = real_lstat(path)
        if path == conf:
            return SimpleNamespace(st_mode=metadata.st_mode, st_uid=os.getuid() + 1)
        return metadata

    monkeypatch.setattr(Path, "lstat", report_foreign_owner)
    assert core.write_conf_keys(conf, {"LATENCY_MSEC": "20"}) is False
    assert conf.read_text(encoding="utf-8") == original


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
    monkeypatch.setattr(core, "run_steps", lambda steps, **_kw: queued.append(list(steps)) or True)

    assert core.set_moonlight_mic("b1_mic") == (True, True)

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


def test_moonlight_write_refuses_hard_linked_lock_without_chmodding_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from hearth import legacy_core as core

    unit = tmp_path / "roaring-moonlight-mic.service"
    original = "Environment=MIC_SOURCE=b2_mic\n"
    unit.write_text(original, encoding="utf-8")
    victim = tmp_path / "outside.txt"
    victim.write_text("leave this alone", encoding="utf-8")
    original_mode = victim.stat().st_mode & 0o777
    (tmp_path / ".roaring-moonlight-mic.service.lock").hardlink_to(victim)
    monkeypatch.setattr(core, "MOONLIGHT_SVC", unit)
    queued: list[object] = []
    monkeypatch.setattr(core, "run_steps", lambda steps, **_kw: queued.append(steps))

    assert core.set_moonlight_mic("b1_mic") == (False, False)
    assert unit.read_text(encoding="utf-8") == original
    assert victim.read_text(encoding="utf-8") == "leave this alone"
    assert victim.stat().st_mode & 0o777 == original_mode
    assert queued == []


def test_moonlight_write_refuses_a_foreign_owned_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from hearth import legacy_core as core

    unit = tmp_path / "roaring-moonlight-mic.service"
    original = "Environment=MIC_SOURCE=b2_mic\n"
    unit.write_text(original, encoding="utf-8")
    real_lstat = Path.lstat

    def report_foreign_owner(path):
        metadata = real_lstat(path)
        if path == unit:
            return SimpleNamespace(st_mode=metadata.st_mode, st_uid=os.getuid() + 1)
        return metadata

    monkeypatch.setattr(Path, "lstat", report_foreign_owner)
    monkeypatch.setattr(core, "MOONLIGHT_SVC", unit)
    queued: list[object] = []
    monkeypatch.setattr(core, "run_steps", lambda steps, **_kw: queued.append(steps))

    assert core.set_moonlight_mic("b1_mic") == (False, False)
    assert unit.read_text(encoding="utf-8") == original
    assert queued == []


def test_moonlight_source_write_fails_closed_and_does_not_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from hearth import legacy_core as core

    missing = tmp_path / "missing.service"
    monkeypatch.setattr(core, "MOONLIGHT_SVC", missing)
    queued: list[object] = []
    monkeypatch.setattr(core, "run_steps", lambda steps, **_kw: queued.append(steps))

    assert core.set_moonlight_mic("b1_mic") == (False, False)
    assert queued == []

    invalid = tmp_path / "invalid.service"
    raw = bytes([91, 83, 101, 114, 118, 105, 99, 101, 93, 10, 255, 254])
    invalid.write_bytes(raw)
    monkeypatch.setattr(core, "MOONLIGHT_SVC", invalid)
    assert core.set_moonlight_mic("b1_mic") == (False, False)
    assert invalid.read_bytes() == raw
    assert queued == []

    no_assignment = tmp_path / "no-assignment.service"
    no_assignment.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    monkeypatch.setattr(core, "MOONLIGHT_SVC", no_assignment)
    assert core.set_moonlight_mic("b1_mic") == (False, False)
    assert "ExecStart=/bin/true" in no_assignment.read_text()
    assert queued == []

    victim = tmp_path / "victim.service"
    victim.write_text("Environment=MIC_SOURCE=b2_mic\n", encoding="utf-8")
    linked = tmp_path / "linked.service"
    linked.symlink_to(victim)
    monkeypatch.setattr(core, "MOONLIGHT_SVC", linked)
    assert core.set_moonlight_mic("b1_mic") == (False, False)
    assert linked.is_symlink()
    assert victim.read_text() == "Environment=MIC_SOURCE=b2_mic\n"
    assert queued == []


def test_moonlight_source_can_save_even_if_restart_cannot_be_queued(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from hearth import legacy_core as core

    unit = tmp_path / "moonlight.service"
    unit.write_text("Environment=MIC_SOURCE=b2_mic\n", encoding="utf-8")
    monkeypatch.setattr(core, "MOONLIGHT_SVC", unit)
    monkeypatch.setattr(core, "run_steps", lambda *_args, **_kwargs: False)

    assert core.set_moonlight_mic("b1_mic") == (True, False)
    assert unit.read_text(encoding="utf-8") == "Environment=MIC_SOURCE=b1_mic\n"


def test_run_steps_reports_worker_thread_start_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from hearth import legacy_core as core

    class ThreadThatCannotStart:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread limit reached")

    monkeypatch.setattr(core.threading, "Thread", ThreadThatCannotStart)

    assert core.run_steps([["systemctl", "--user", "restart", "example.service"]]) is False


def test_moonlight_source_rejects_unknown_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from hearth import legacy_core as core

    unit = tmp_path / "moonlight.service"
    unit.write_text("Environment=MIC_SOURCE=b2_mic\n", encoding="utf-8")
    monkeypatch.setattr(core, "MOONLIGHT_SVC", unit)
    queued: list[object] = []
    monkeypatch.setattr(core, "run_steps", lambda steps, **_kw: queued.append(steps))

    assert core.set_moonlight_mic("$(touch /tmp/should-not-exist)") == (False, False)
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


def test_background_argv_helper_reports_spawn_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hearth import legacy_core as core

    def fail_spawn(*_args, **_kwargs):
        raise FileNotFoundError("pactl unavailable")

    monkeypatch.setattr(core.subprocess, "Popen", fail_spawn)

    assert core.sh_bg_argv(["pactl", "set-sink-mute", "sink-a", "0"]) is False


def test_unmute_all_reports_when_any_command_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hearth import legacy_core as core

    monkeypatch.setattr(core, "monitor_sink", lambda: None)
    monkeypatch.setattr(core, "UNMUTE_SINKS", ["sink-a", "sink-b"])
    monkeypatch.setattr(core, "pactl_vol", lambda _sink: 100)
    attempted: list[tuple[str, ...]] = []

    def fake_pactl_bg(*args: str) -> bool:
        attempted.append(args)
        return args[1] != "sink-b"

    monkeypatch.setattr(core, "pactl_bg", fake_pactl_bg)

    assert core.unmute_all() is False
    assert attempted == [
        ("set-sink-mute", "sink-a", "0"),
        ("set-sink-mute", "sink-b", "0"),
    ]


def test_unmute_cli_describes_a_request_not_verified_audio_state(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from hearth import legacy_core as core

    monkeypatch.setattr(logging_setup, "configure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(core, "unmute_all", lambda: True)

    assert cli.main(["--unmute"]) == cli.EXIT_OK
    assert capsys.readouterr().out == "unmute requests started; state not yet verified\n"


def test_unmute_cli_returns_error_if_a_command_cannot_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from hearth import legacy_core as core

    monkeypatch.setattr(logging_setup, "configure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(core, "unmute_all", lambda: False)

    assert cli.main(["--unmute"]) == cli.EXIT_ERROR
    assert capsys.readouterr().out == "could not start one or more unmute commands\n"
