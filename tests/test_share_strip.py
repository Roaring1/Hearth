"""Regression coverage for the standard VM-SHARE master strip contract."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.gui


def test_vm_share_is_a_first_class_master_channel() -> None:
    from hearth.ui.gtk4 import app as mixer

    channels = [channel for channel in mixer.CHANNELS if channel.sink == "vm_share"]
    assert len(channels) == 1
    assert channels[0].name == "Share"
    assert channels[0].group == "headset"
    assert channels[0].master is True
    assert mixer.UNIT_FOR_SINK["vm_share"] == "roaring-vm-share.service"
    # Legacy OUT routes use module-loopback; Share is already fed by a
    # separately-owned direct-link service and is not a generic OUT target.
    assert all(sink != "vm_share" for _label, sink in mixer.OUTPUTS)


def test_global_unmute_does_not_unmute_the_share_master(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hearth.ui.gtk4 import app as mixer

    calls: list[tuple[str, bool]] = []

    class InlineThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    monkeypatch.setattr(mixer.threading, "Thread", InlineThread)
    monkeypatch.setattr(
        mixer.pactl,
        "set_mute",
        lambda sink, muted: calls.append((sink, muted)) or True,
    )

    result = mixer.MixerApp._ctl_unmute(SimpleNamespace())

    assert result == "ok"
    assert calls == [(channel.sink, False) for channel in mixer.CHANNELS if not channel.master]
    assert ("vm_share", False) not in calls


def test_padfire_sink_snapshot_does_not_gain_a_new_share_entry() -> None:
    """The mixer UI adds Share without changing the existing control socket list."""
    import json

    from hearth.ui.gtk4 import app as mixer

    states = {channel.sink: SimpleNamespace(volume=75, muted=False) for channel in mixer.CHANNELS}
    payload = mixer.MixerApp._ctl_sinks(SimpleNamespace(window=SimpleNamespace(states=states)))
    items = json.loads(payload)

    assert "vm_share" not in items
    assert set(items) == {channel.sink for channel in mixer.CHANNELS if not channel.master}
