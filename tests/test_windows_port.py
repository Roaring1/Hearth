"""Windows-only regression coverage for Hearth's first native port layer."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from hearth import paths, services, settings, windows_status
from hearth.ipc import client, protocol, windows_pipe
from hearth.ipc.server import Server, read_pid, remove_pid, write_pid
from hearth.platform import windows_fs
from hearth.platform.locking import lock_file, unlock_file

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows port regression tests")


def test_windows_core_audio_dependency_is_platform_marked() -> None:
    """Windows needs Pycaw; Linux must keep its distribution audio stack."""
    metadata = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = metadata["project"]["dependencies"]
    assert dependencies == ["pycaw>=20260927; sys_platform == 'win32'"]


def test_all_production_modules_import() -> None:
    root = Path(__file__).parents[1] / "src" / "hearth"
    names = [f"hearth.{p.stem}" for p in root.glob("*.py") if p.stem != "__init__"]
    ui = root / "ui" / "gtk4"
    names.extend(f"hearth.ui.gtk4.{p.stem}" for p in ui.glob("*.py") if p.stem != "__init__")
    for name in names:
        importlib.import_module(name)


def test_windows_app_paths_are_under_private_local_app_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        monkeypatch.delenv(name, raising=False)
    paths.ensure_dirs()
    root = paths.data_root()
    assert root == Path(os.environ["LOCALAPPDATA"]) / "Hearth"
    for item in (
        paths.config_dir(),
        paths.state_dir(),
        paths.cache_dir(),
        paths.runtime_dir(),
        paths.dumps_dir(),
    ):
        assert item.resolve().is_relative_to(root.resolve())


def test_private_root_and_file_dacl_exclude_sandbox_users(tmp_path: Path) -> None:
    paths.ensure_dirs()
    root = paths.data_root()
    sample = paths.config_dir() / "acl-regression-test.txt"
    try:
        sample.write_text("private", encoding="utf-8")
        paths.ensure_private_file(sample)
        icacls = Path(os.environ["WINDIR"]) / "System32" / "icacls.exe"
        for target in (root, paths.config_dir(), sample):
            result = subprocess.run(
                [str(icacls), str(target)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )
            assert "CodexSandboxUsers" not in result.stdout
    finally:
        sample.unlink(missing_ok=True)


def test_private_root_rejects_hardlinked_descendants_without_changing_external_acl(
    tmp_path: Path,
) -> None:
    root = tmp_path / "private-root"
    root.mkdir()
    external = tmp_path / "external.txt"
    external.write_text("preserve external ACL", encoding="utf-8")
    linked = root / "linked.txt"
    os.link(external, linked)
    icacls = Path(os.environ["WINDIR"]) / "System32" / "icacls.exe"
    subprocess.run(
        [str(icacls), str(external), "/grant", "*S-1-1-0:F"],
        capture_output=True,
        text=True,
        check=True,
    )

    with pytest.raises(OSError, match="hard-linked"):
        windows_fs.ensure_private_root(root)

    external_acl = subprocess.run(
        [str(icacls), str(external)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout
    assert "Everyone:" in external_acl
    assert external.read_text(encoding="utf-8") == "preserve external ACL"


def test_private_root_rejects_reparse_point_descendants(tmp_path: Path) -> None:
    root = tmp_path / "private-root"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    junction = root / "external-junction"
    # MSYS2 Python's pathlib renders Windows paths with forward slashes.
    # cmd/mklink interprets the slash in C:/Users as an option switch, so
    # normalize to native Windows separators before invoking cmd.exe.
    junction_arg = str(junction).replace("/", "\\")
    external_arg = str(external).replace("/", "\\")
    created = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", junction_arg, external_arg],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip(f"could not create a test junction: {created.stderr or created.stdout}")
    try:
        assert windows_fs.is_reparse_point(junction)
        with pytest.raises(PermissionError, match="reparse point"):
            windows_fs.ensure_private_root(root)
    finally:
        if windows_fs.is_reparse_point(junction):
            junction.rmdir()


def test_private_directory_and_file_remove_explicit_everyone_aces(tmp_path: Path) -> None:
    """Repair explicit grants recursively on the app root and on standalone files."""
    root = tmp_path / "private-root"
    root.mkdir()
    child = root / "existing-child.txt"
    child.write_text("private child", encoding="utf-8")
    icacls = Path(os.environ["WINDIR"]) / "System32" / "icacls.exe"

    subprocess.run(
        [str(icacls), str(root), "/grant", "*S-1-1-0:(OI)(CI)F"],
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        [str(icacls), str(child), "/grant", "*S-1-1-0:F"],
        capture_output=True,
        text=True,
        check=True,
    )
    windows_fs.ensure_private_root(root)

    # The root DACL must propagate usable access to regular files. An empty
    # effective file DACL can otherwise look private in icacls while making
    # Hearth's own log and IPC lock files inaccessible.
    assert child.read_text(encoding="utf-8") == "private child"
    child.write_text("updated private child", encoding="utf-8")
    assert child.read_text(encoding="utf-8") == "updated private child"

    for target in (root, child):
        acl = subprocess.run(
            [str(icacls), str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        ).stdout
        assert "Everyone:" not in acl
        assert "CodexSandboxUsers" not in acl

    sample = root / "standalone-private.txt"
    sample.write_text("private", encoding="utf-8")
    subprocess.run(
        [str(icacls), str(sample), "/grant", "*S-1-1-0:F"],
        capture_output=True,
        text=True,
        check=True,
    )
    paths.ensure_private_file(sample)
    file_acl = subprocess.run(
        [str(icacls), str(sample)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout
    assert "Everyone:" not in file_acl


def test_windows_status_reports_only_observed_local_audio_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hearth.collector import SinkState, Snapshot, StreamState

    monkeypatch.setattr(
        windows_status,
        "_query_service_state",
        lambda _name: "running",
    )
    snapshot = Snapshot(
        sinks={
            "vm_game": SinkState(name="vm_game", index="render-1", volume=80),
            "laptop_audio": SinkState(name="laptop_audio", index="render-2", volume=60),
        },
        streams={"vm_game": [StreamState(index="session-1", name="Synthetic app", sink="vm_game")]},
        sources={"capture-1": "Synthetic microphone"},
        default_sink="Speakers (Synthetic)",
        routes=None,
    )
    monkeypatch.setattr(windows_status.windows_audio, "collect", lambda: snapshot)

    report = windows_status.gather()

    assert report.audio_service == "running"
    assert report.endpoint_builder == "running"
    assert report.default_playback == "Speakers (Synthetic)"
    assert report.render_channels == ("vm_game", "laptop_audio")
    assert report.missing_render_channels == ("vm_chat", "vm_music")
    assert report.capture_endpoint_count == 1
    assert report.application_session_count == 1
    assert "Windows playback endpoint is available" in report.headline
    lines = "\n".join(report.lines())
    assert "Routing graph" in lines
    assert "Not tested; this report is read-only inventory" in lines
    assert "complete Hearth rig" in lines


def test_windows_status_degrades_to_unknown_when_probes_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_name: str) -> str:
        raise PermissionError("service query denied")

    def no_inventory():
        raise OSError("Core Audio inventory unavailable")

    monkeypatch.setattr(windows_status, "_query_service_state", unavailable)
    monkeypatch.setattr(windows_status.windows_audio, "collect", no_inventory)

    report = windows_status.gather()

    assert report.audio_service == "unknown"
    assert report.endpoint_builder == "unknown"
    assert report.default_playback is None
    assert report.capture_endpoint_count is None
    assert report.application_session_count is None
    assert report.inventory_error == "OSError: Core Audio inventory unavailable"
    assert report.headline == "Could not inspect Windows audio endpoints"


def test_private_file_rejects_hard_links(tmp_path: Path) -> None:
    original = tmp_path / "original.txt"
    linked = tmp_path / "linked.txt"
    original.write_text("do not expose", encoding="utf-8")
    os.link(original, linked)
    with pytest.raises(OSError, match="hard-linked"):
        paths.ensure_private_file(linked)


def test_private_handle_rejects_hard_links(tmp_path: Path) -> None:
    original = tmp_path / "original-handle.txt"
    linked = tmp_path / "linked-handle.txt"
    original.write_text("do not expose", encoding="utf-8")
    os.link(original, linked)
    fd = os.open(linked, os.O_RDWR)
    try:
        with pytest.raises(OSError, match="hard-linked"):
            paths.ensure_private_fd(fd, linked)
    finally:
        os.close(fd)


def test_msvcrt_lock_is_exclusive_across_processes(tmp_path: Path) -> None:
    lock_path = tmp_path / "cross-process.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        lock_file(fd)
        assert os.fstat(fd).st_size == 0
        code = (
            "import os,sys; "
            "from hearth.platform.locking import try_lock_file; "
            "fd=os.open(sys.argv[1],os.O_RDWR); "
            "print(try_lock_file(fd)); os.close(fd)"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
        blocked = subprocess.run(
            [sys.executable, "-c", code, str(lock_path)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
            check=True,
        )
        assert blocked.stdout.strip() == "False"
        unlock_file(fd)
        acquired = subprocess.run(
            [sys.executable, "-c", code, str(lock_path)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
            check=True,
        )
        assert acquired.stdout.strip() == "True"
    finally:
        os.close(fd)


def test_named_pipe_protocol_and_server_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    # The live desktop application owns the stable per-user pipe name.
    # Tests use a private process-scoped name so they can run while Hearth
    # is open without replacing or conflicting with its IPC listener.
    test_pipe = rf"\\.\pipe\hearth-test-{os.getpid()}"
    monkeypatch.setattr(windows_pipe, "_pipe_address", lambda: test_pipe)
    payload = "x" * 1200
    state = {"reply": "ready", "fail": False}

    def handler(request: str) -> str:
        if state["fail"]:
            raise RuntimeError("expected handler failure")
        return payload if request == "large" else f"{state['reply']}:{request}"

    server = Server(handler)
    server.start()
    try:
        assert client.send("show") == "ready:show"
        assert client.send("large") == payload
        state["fail"] = True
        assert client.send("broken") is None
        state["fail"] = False
        assert client.send("show") == "ready:show"

        duplicate = Server(lambda _request: "second")
        with pytest.raises(OSError):
            duplicate.start()
    finally:
        server.stop(timeout=3.0)

    assert client.send("show") is None
    assert protocol.encode("show") == b"show\n"


def test_pid_file_atomic_lifecycle(tmp_path: Path) -> None:
    target = tmp_path / "hearth-test.pid"
    write_pid(target)
    assert read_pid(target) == os.getpid()
    assert remove_pid(target, expected_pid=os.getpid())
    assert read_pid(target) is None


def test_settings_atomic_save_preserves_unknown_keys(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    values = {"refresh_ms": 1350, "future_setting": {"enabled": True}}
    settings.save(values, target)
    loaded = settings.load(target)
    assert loaded["refresh_ms"] == 1350
    assert loaded["future_setting"] == {"enabled": True}
    assert list(tmp_path.glob("settings.json.tmp.*")) == []


def test_windows_systemd_service_calls_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from hearth import proc

    calls: list[tuple[str, ...]] = []

    def forbidden_run(argv, **_kwargs):
        calls.append(tuple(argv))
        raise AssertionError("systemctl must not be invoked on Windows")

    def forbidden_spawn(argv, **_kwargs):
        calls.append(tuple(argv))
        raise AssertionError("systemctl must not be spawned on Windows")

    monkeypatch.setattr(proc, "run", forbidden_run)
    monkeypatch.setattr(proc, "spawn", forbidden_spawn)

    assert services.states(["hearth-ui.service"]) == {"hearth-ui.service": services.UNKNOWN}
    assert services.is_active("hearth-ui.service") is False
    assert services.restart("hearth-ui.service") is False
    assert services.reset_failed("hearth-ui.service") is False
    assert services.daemon_reload() is False
    assert calls == []
