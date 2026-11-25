"""Fake-backend tests for the explicit, on-demand capture core."""

from hearth.audio.capture_core import (
    AudioSession,
    CaptureCoreState,
    CaptureDevice,
    OnDemandCaptureCore,
)


class FakeBackend:
    def __init__(self, devices=()):
        self.devices = tuple(devices)
        self.discovery_calls = 0
        self.start_calls = []
        self.stop_calls = []
        self.fail_start = False
        self.fail_stop = False
        self.session_override = None

    def discover_devices(self):
        self.discovery_calls += 1
        return self.devices

    def start_session(self, device):
        self.start_calls.append(device.device_id)
        if self.fail_start:
            raise RuntimeError("simulated start failure")
        return self.session_override or AudioSession("session-1", device.device_id, "fake")

    def stop_session(self, session):
        self.stop_calls.append(session.session_id)
        if self.fail_stop:
            raise RuntimeError("simulated stop failure")


def device(identifier="mic-1", available=True):
    return CaptureDevice(
        identifier, f"Mic {identifier}", "fake", channels=2, sample_rate=48000, available=available
    )


def test_discovery_is_demand_only_and_selection_explicit():
    backend = FakeBackend((device(), device("mic-2", available=False)))
    core = OnDemandCaptureCore(backend)
    assert backend.discovery_calls == 0
    assert core.snapshot().state is CaptureCoreState.IDLE
    snap = core.refresh()
    assert backend.discovery_calls == 1
    assert snap.state is CaptureCoreState.READY
    assert snap.selected_device_id is None
    assert core.select("mic-2").state is CaptureCoreState.ERROR
    assert core.select("mic-1").selected_device_id == "mic-1"


def test_start_stop_transitions_and_session_ownership():
    backend = FakeBackend((device(),))
    core = OnDemandCaptureCore(backend)
    core.refresh()
    core.select("mic-1")
    active = core.start()
    assert active.state is CaptureCoreState.ACTIVE
    assert active.session is not None and active.session.device_id == "mic-1"
    assert backend.start_calls == ["mic-1"]
    stopped = core.stop()
    assert stopped.state is CaptureCoreState.READY
    assert stopped.session is None
    assert backend.stop_calls == ["session-1"]


def test_failed_start_leaves_no_session_handle():
    backend = FakeBackend((device(),))
    backend.fail_start = True
    core = OnDemandCaptureCore(backend)
    core.refresh()
    core.select("mic-1")
    snap = core.start()
    assert snap.state is CaptureCoreState.ERROR
    assert snap.session is None
    assert "simulated start failure" in snap.error


def test_failed_stop_retains_session_for_retry():
    backend = FakeBackend((device(),))
    core = OnDemandCaptureCore(backend)
    core.refresh()
    core.select("mic-1")
    core.start()
    backend.fail_stop = True
    failed = core.stop()
    assert failed.state is CaptureCoreState.ERROR
    assert failed.session is not None
    backend.fail_stop = False
    retried = core.stop()
    assert retried.state is CaptureCoreState.READY
    assert retried.session is None
    assert backend.stop_calls == ["session-1", "session-1"]


def test_refresh_and_select_block_while_session_active():
    backend = FakeBackend((device(),))
    core = OnDemandCaptureCore(backend)
    core.refresh()
    core.select("mic-1")
    core.start()
    assert "active session" in core.refresh().error
    assert "locked" in core.select("mic-1").error
    assert core.snapshot().state is CaptureCoreState.ACTIVE
    core.stop()


def test_invalid_device_and_session_models_reject_bad_values():
    import pytest

    with pytest.raises(ValueError):
        CaptureDevice("", "mic", "fake")
    with pytest.raises(ValueError):
        CaptureDevice("mic", "mic", "fake", channels=True)
    with pytest.raises(ValueError):
        AudioSession("s", "d", "fake", active=1)


def test_duplicate_inventory_is_fail_closed():
    backend = FakeBackend((device(), device()))
    core = OnDemandCaptureCore(backend)
    snap = core.refresh()
    assert snap.state is CaptureCoreState.ERROR
    assert snap.devices == ()
    assert "duplicate" in snap.error


def test_invalid_selection_clears_previous_selection_and_prevents_start():
    backend = FakeBackend((device(),))
    core = OnDemandCaptureCore(backend)
    core.refresh()
    core.select("mic-1")
    failed_selection = core.select("missing-device")
    assert failed_selection.selected_device_id is None
    blocked = core.start()
    assert blocked.state is CaptureCoreState.ERROR
    assert backend.start_calls == []


def test_backend_session_mismatch_is_retained_for_cleanup():
    backend = FakeBackend((device(),))
    backend.session_override = AudioSession("foreign-session", "other-device", "fake")
    core = OnDemandCaptureCore(backend)
    core.refresh()
    core.select("mic-1")
    failed = core.start()
    assert failed.state is CaptureCoreState.ERROR
    assert failed.session is not None
    assert failed.session.device_id == "other-device"
    cleaned = core.stop()
    assert cleaned.state is CaptureCoreState.READY
    assert cleaned.session is None
    assert backend.stop_calls == ["foreign-session"]
