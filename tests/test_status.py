"""Tests for the whole-rig status composer."""

from __future__ import annotations

from hearth import chain, health, mic, midi, share, status, units


def test_only_genuinely_broken_units_reach_the_report() -> None:
    """A finished oneshot, an idle socket-activated service and a waiting
    timer are all healthy; only the stopped daemon is a fault."""
    found = [
        units.Unit("roaring-vm-share.service", "oneshot", "inactive", "dead"),
        units.Unit("roaring-presenced.service", "ondemand", "inactive", "dead"),
        units.Unit("roaring-carla-backup.timer", "timer", "active", "waiting"),
        units.Unit("roaring-mic-busses.service", "daemon", "inactive", "dead"),
    ]
    assert status.broken_units(found) == ["roaring-mic-busses.service"]


def test_the_expensive_probe_is_only_asked_once_per_window(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The screen-share probe costs two interpreter startups; a refresh
    tick must not pay that every second."""
    calls = []

    def fake_view() -> share.ShareView:
        calls.append(1)
        return share.ShareView(installed=True)

    status.forget_share()
    monkeypatch.setattr(status.share, "view", fake_view)
    status.share_view()
    status.share_view()
    assert len(calls) == 1

    status.share_view(max_age=0)
    assert len(calls) == 2
    status.forget_share()


def test_forgetting_the_cache_forces_a_fresh_look(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = []
    monkeypatch.setattr(
        status.share,
        "view",
        lambda: (calls.append(1), share.ShareView(installed=True))[1],
    )
    status.forget_share()
    status.share_view()
    status.forget_share()
    status.share_view()
    assert len(calls) == 2
    status.forget_share()


def test_unreadable_graph_gives_unknown_share_channels_not_zero() -> None:
    """Zero channels means "nothing is wired"; unknown means "could not ask"."""
    assert status.share_channels(None) is None
    assert status.share_channels([]) == 0


def test_the_share_bus_is_in_the_bus_list() -> None:
    """Step 11 found the rig had a bus the app could not see. Keep it."""
    assert status.SHARE_SINK in dict(status.BUSES)


def test_any_portal_backend_satisfies_screen_sharing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The GTK backend is as valid as the KDE one; asking only for KDE cried wolf."""
    monkeypatch.setattr(
        status.services,
        "states",
        lambda units: {
            "xdg-desktop-portal": "active",
            "xdg-desktop-portal-kde": "inactive",
            "xdg-desktop-portal-gtk": "active",
        },
    )
    assert status.portal_ok() is True


def test_unqueryable_portal_is_unknown_not_broken(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(status.services, "states", lambda units: {})
    assert status.portal_ok() is None


def test_frontend_up_with_no_backend_is_a_real_fault(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        status.services,
        "states",
        lambda units: {"xdg-desktop-portal": "active", "xdg-desktop-portal-kde": "inactive"},
    )
    assert status.portal_ok() is False


def _report(issues: list[health.Issue] | None = None) -> status.Report:
    return status.Report(
        health=health.Health(issues=issues or []),
        chain=chain.ChainState(present=True, inputs=["sm7b_mono"], outputs=["mic_b1"]),
        share=share.ShareView(installed=True),
        mic=mic.MicView(recording=False, analysis=None, session_count=0),
        surfaces=midi.analyze([], service_states={midi.LPD8_UNIT: True}),
    )


def test_a_healthy_report_leads_with_the_headline() -> None:
    lines = _report().lines()
    assert lines[0] == "Everything is working"
    assert any(line.startswith("Voice effects") for line in lines)
    assert any(line.startswith("Controls") for line in lines)


def test_issues_are_printed_with_their_fix() -> None:
    issue = health.Issue(
        key="muted",
        severity="warning",
        title="Music is muted",
        detail="Nothing from that bus is audible.",
        fix="Unmute it.",
    )
    lines = _report([issue])
    text = "\n".join(lines.lines())
    assert "Music is muted" in text
    assert "Fix: Unmute it." in text


def test_the_report_never_names_internals() -> None:
    text = "\n".join(_report().lines())
    for token in ("vm_", "mic_b1", "pactl", "systemctl", ".service"):
        assert token not in text, f"{token!r} leaked into the report"
