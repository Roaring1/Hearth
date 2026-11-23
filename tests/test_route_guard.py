from __future__ import annotations

from pathlib import Path

from hearth.audio.pactl import Module
from hearth.audio.route_guard import InventoryError, check_and_recover
from hearth.audio.route_health import LinkInventory

BUS = "vm_game"
SINK = "astro.chat"
NODE = "123-45"


def _inventory(*, missing: set[str] | None = None) -> LinkInventory:
    missing = missing or set()
    edges = {
        (f"{BUS}:monitor_FL", f"input.loopback-{NODE}:input_FL"),
        (f"{BUS}:monitor_FR", f"input.loopback-{NODE}:input_FR"),
        (f"output.loopback-{NODE}:output_FL", f"{SINK}:playback_FL"),
        (f"output.loopback-{NODE}:output_FR", f"{SINK}:playback_FR"),
    }
    edges = {
        edge for edge in edges if not any(label in edge[0] or label in edge[1] for label in missing)
    }
    return LinkInventory(
        frozenset(
            {
                f"{BUS}:monitor_FL",
                f"{BUS}:monitor_FR",
                f"{SINK}:playback_FL",
                f"{SINK}:playback_FR",
                f"input.loopback-{NODE}:input_FL",
                f"input.loopback-{NODE}:input_FR",
                f"output.loopback-{NODE}:output_FL",
                f"output.loopback-{NODE}:output_FR",
            }
        ),
        frozenset(edges),
    )


def _module(index: int = 1) -> Module:
    return Module(
        index=index,
        name="module-loopback",
        args=f"source={BUS}.monitor sink={SINK} latency_msec=12",
    )


def test_missing_module_creates_one_exact_route_and_respects_cooldown(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        "hearth.audio.route_guard._collect",
        lambda sink: (
            True,
            [],
            _inventory(missing={"monitor_FL", "monitor_FR", "output_FL", "output_FR"}),
        ),
    )
    monkeypatch.setattr(
        "hearth.audio.route_guard._create_loopback",
        lambda bus, sink, latency: calls.append((bus, sink, latency)) or "77",
    )
    state = tmp_path / "attempts.json"

    first = check_and_recover(
        SINK,
        latency_msec=12,
        state_file=state,
        cooldown_sec=30,
        buses=(BUS,),
    )
    second = check_and_recover(
        SINK,
        latency_msec=12,
        state_file=state,
        cooldown_sec=30,
        buses=(BUS,),
    )

    assert calls == [(BUS, SINK, 12)]
    assert "module 77" in first[0]
    assert "cooldown" in second[0]
    assert state.is_file()
    assert state.stat().st_mode & 0o777 == 0o600


def test_existing_module_repairs_only_the_exact_missing_edge(monkeypatch, tmp_path: Path) -> None:
    inventories = iter(
        (
            (True, [_module()], _inventory(missing={"output_FR"})),
            (True, [_module()], _inventory()),
        )
    )
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "hearth.audio.route_guard._collect",
        lambda sink: next(inventories),
    )
    monkeypatch.setattr(
        "hearth.audio.route_guard._run_recovery_command",
        lambda argv: commands.append(tuple(argv)) or "",
    )

    result = check_and_recover(
        SINK,
        state_file=tmp_path / "attempts.json",
        cooldown_sec=0,
        buses=(BUS,),
    )

    assert commands == [("pw-link", f"output.loopback-{NODE}:output_FR", f"{SINK}:playback_FR")]
    assert "healthy" in result[0]
    assert "reconnected 1 missing link" in result[0]


def test_inventory_failure_is_unknown_and_never_mutates(monkeypatch, tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    def fail(sink: str):
        raise InventoryError("PipeWire unavailable")

    monkeypatch.setattr("hearth.audio.route_guard._collect", fail)
    monkeypatch.setattr(
        "hearth.audio.route_guard._run_recovery_command",
        lambda argv: commands.append(tuple(argv)),
    )

    result = check_and_recover(
        SINK,
        state_file=tmp_path / "attempts.json",
        buses=("vm_game", "vm_chat"),
    )

    assert result == [
        "vm_game: unknown (inventory query failed: PipeWire unavailable)",
        "vm_chat: unknown (inventory query failed: PipeWire unavailable)",
    ]
    assert commands == []


def test_missing_chat_sink_is_unavailable_and_never_mutates(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        "hearth.audio.route_guard._collect",
        lambda sink: (False, [], _inventory()),
    )
    monkeypatch.setattr(
        "hearth.audio.route_guard._create_loopback",
        lambda *args: calls.append(args),
    )

    result = check_and_recover(
        SINK,
        state_file=tmp_path / "attempts.json",
        buses=("vm_game",),
    )

    assert result[0].startswith("vm_game: unavailable")
    assert calls == []


def test_duplicate_module_ownership_is_unknown_and_not_repaired(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "hearth.audio.route_guard._collect",
        lambda sink: (True, [_module(1), _module(2)], _inventory()),
    )
    monkeypatch.setattr(
        "hearth.audio.route_guard._run_recovery_command",
        lambda argv, calls=calls: calls.append(tuple(argv)),
    )

    result = check_and_recover(
        SINK,
        state_file=tmp_path / "attempts.json",
        buses=(BUS,),
    )

    assert result[0].startswith("vm_game: unknown")
    assert "2 matching loopback modules" in result[0]
    assert calls == []


def test_state_reader_refuses_symlinks_and_preserves_target(tmp_path: Path) -> None:
    from hearth.audio.route_guard import _load_attempts

    target = tmp_path / "private.json"
    target.write_text('{"vm_game|astro.chat": 123.0}', encoding="utf-8")
    state = tmp_path / "attempts.json"
    state.symlink_to(target)

    try:
        _load_attempts(state)
    except OSError:
        pass
    else:
        raise AssertionError("symlinked cooldown state must be refused")

    assert target.read_text(encoding="utf-8") == '{"vm_game|astro.chat": 123.0}'


def test_state_reader_refuses_hard_links(tmp_path: Path) -> None:
    import os

    from hearth.audio.route_guard import _load_attempts

    state = tmp_path / "attempts.json"
    other = tmp_path / "other.json"
    state.write_text("{}", encoding="utf-8")
    os.link(state, other)

    try:
        _load_attempts(state)
    except OSError as exc:
        assert "hard-linked" in str(exc)
    else:
        raise AssertionError("hard-linked cooldown state must be refused")


def test_corrupt_state_fails_closed_instead_of_resetting_cooldown(tmp_path: Path) -> None:
    from hearth.audio.route_guard import _load_attempts

    state = tmp_path / "attempts.json"
    state.write_text("{not-json", encoding="utf-8")

    try:
        _load_attempts(state)
    except OSError as exc:
        assert "corrupt" in str(exc)
    else:
        raise AssertionError("corrupt cooldown state must not be treated as empty")


def test_guard_refuses_state_symlink_before_collect_or_mutation(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    state = tmp_path / "attempts.json"
    state.symlink_to(target)
    monkeypatch.setattr(
        "hearth.audio.route_guard._collect",
        lambda sink: (
            calls.append(sink)
            or (
                True,
                [],
                _inventory(missing={"monitor_FL", "monitor_FR", "output_FL", "output_FR"}),
            )
        ),
    )

    try:
        check_and_recover(SINK, state_file=state, buses=(BUS,))
    except OSError:
        pass
    else:
        raise AssertionError("guardian must refuse a symlinked cooldown state")

    assert calls == []
    assert target.read_text(encoding="utf-8") == "{}"


def test_guard_refuses_symlinked_lock_before_collect_or_mutation(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    state = tmp_path / "attempts.json"
    target = tmp_path / "lock-target"
    target.write_text("do not alter", encoding="utf-8")
    lock = tmp_path / ".attempts.json.lock"
    lock.symlink_to(target)
    monkeypatch.setattr(
        "hearth.audio.route_guard._collect",
        lambda sink: (
            calls.append(sink)
            or (
                True,
                [],
                _inventory(missing={"monitor_FL", "monitor_FR", "output_FL", "output_FR"}),
            )
        ),
    )

    try:
        check_and_recover(SINK, state_file=state, buses=(BUS,))
    except OSError:
        pass
    else:
        raise AssertionError("guardian must refuse a symlinked lock")

    assert calls == []
    assert target.read_text(encoding="utf-8") == "do not alter"


def test_guard_refuses_non_linux_before_state_or_graph_access(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    state = tmp_path / "not-created" / "attempts.json"
    monkeypatch.setattr("hearth.audio.route_guard._is_linux_runtime", lambda: False)
    monkeypatch.setattr(
        "hearth.audio.route_guard._collect",
        lambda sink: calls.append(sink),
    )
    monkeypatch.setattr(
        "hearth.audio.route_guard._create_loopback",
        lambda *args: calls.append("create-loopback"),
    )
    monkeypatch.setattr(
        "hearth.audio.route_guard._run_recovery_command",
        lambda argv: calls.append("route-mutation"),
    )

    result = check_and_recover(SINK, state_file=state, buses=("vm_game", "vm_chat"))

    assert result == [
        "vm_game: unavailable (route guardian requires Linux/PipeWire)",
        "vm_chat: unavailable (route guardian requires Linux/PipeWire)",
    ]
    assert calls == []
    assert not state.parent.exists()


def test_guard_refuses_hard_linked_lock_before_collect_or_mutation(
    monkeypatch, tmp_path: Path
) -> None:
    import os

    calls: list[str] = []
    state = tmp_path / "attempts.json"
    target = tmp_path / "lock-target"
    target.write_text("do not alter", encoding="utf-8")
    lock = tmp_path / ".attempts.json.lock"
    os.link(target, lock)
    monkeypatch.setattr(
        "hearth.audio.route_guard._collect",
        lambda sink: (
            calls.append(sink)
            or (
                True,
                [],
                _inventory(missing={"monitor_FL", "monitor_FR", "output_FL", "output_FR"}),
            )
        ),
    )

    try:
        check_and_recover(SINK, state_file=state, buses=(BUS,))
    except OSError as exc:
        assert "hard-linked" in str(exc)
    else:
        raise AssertionError("guardian must refuse a hard-linked lock")

    assert calls == []
    assert target.read_text(encoding="utf-8") == "do not alter"
