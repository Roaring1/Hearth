"""Presentation-state tests for the pure Capture Mode view-model."""

import pytest

from hearth.audio.capture_core import (
    AudioSession,
    CaptureCoreSnapshot,
    CaptureCoreState,
    CaptureDevice,
)
from hearth.capture_ui_model import CaptureModeViewModel


def device(device_id: str = "mic-1", *, available: bool = True) -> CaptureDevice:
    return CaptureDevice(device_id, f"Mic {device_id}", "fake", available=available)


def snapshot(
    state: CaptureCoreState = CaptureCoreState.IDLE,
    devices: tuple[CaptureDevice, ...] = (),
    selected: str | None = None,
    session: AudioSession | None = None,
    error: str | None = None,
    revision: int = 0,
) -> CaptureCoreSnapshot:
    return CaptureCoreSnapshot(state, devices, selected, session, error, revision)


def test_idle_without_devices_offers_refresh_only():
    view = CaptureModeViewModel.present(snapshot())
    assert view.status == "No capture device ready"
    assert view.refresh_enabled
    assert not view.selection_enabled
    assert not view.start_enabled
    assert not view.stop_enabled
    assert not view.error_visible


def test_ready_devices_require_explicit_selection_before_start():
    view = CaptureModeViewModel.present(
        snapshot(CaptureCoreState.READY, (device(), device("mic-2", available=False)))
    )
    assert view.selection_enabled
    assert view.refresh_enabled
    assert not view.start_enabled
    assert view.selected_device_id is None
    assert [item.label for item in view.devices] == ["Mic mic-1", "Mic mic-2 (unavailable)"]


def test_selected_available_device_enables_start():
    view = CaptureModeViewModel.present(
        snapshot(CaptureCoreState.READY, (device(),), selected="mic-1")
    )
    assert view.start_enabled
    assert view.selection_enabled
    assert view.refresh_enabled
    assert not view.stop_enabled


@pytest.mark.parametrize(
    "state,status",
    [
        (CaptureCoreState.STARTING, "Starting capture…"),
        (CaptureCoreState.ACTIVE, "Capture active"),
        (CaptureCoreState.STOPPING, "Stopping capture…"),
    ],
)
def test_lifecycle_states_block_refresh_selection_and_start(state, status):
    view = CaptureModeViewModel.present(snapshot(state, (device(),), selected="mic-1"))
    assert view.status == status
    assert not view.refresh_enabled
    assert not view.selection_enabled
    assert not view.start_enabled
    assert not view.stop_enabled


def test_active_session_exposes_stop_only():
    session = AudioSession("session-1", "mic-1", "fake")
    view = CaptureModeViewModel.present(
        snapshot(CaptureCoreState.ACTIVE, (device(),), selected="mic-1", session=session)
    )
    assert view.stop_enabled
    assert not view.refresh_enabled
    assert not view.selection_enabled
    assert not view.start_enabled


def test_error_without_retained_session_can_refresh_and_shows_generic_copy():
    view = CaptureModeViewModel.present(
        snapshot(CaptureCoreState.ERROR, (device(),), error="private host exception detail")
    )
    assert view.error_visible
    assert view.refresh_enabled
    assert view.selection_enabled
    assert not view.start_enabled
    assert not view.stop_enabled
    assert "private host exception detail" not in view.detail


def test_error_with_retained_session_only_allows_stop_cleanup_retry():
    session = AudioSession("session-1", "mic-1", "fake")
    view = CaptureModeViewModel.present(
        snapshot(CaptureCoreState.ERROR, (device(),), session=session, error="cleanup failed")
    )
    assert view.error_visible
    assert view.stop_enabled
    assert not view.refresh_enabled
    assert not view.selection_enabled
    assert not view.start_enabled
    assert "Cleanup did not finish" in view.detail


def test_unavailable_device_is_not_selectable_or_startable():
    view = CaptureModeViewModel.present(
        snapshot(CaptureCoreState.READY, (device("mic-1", available=False),), selected="mic-1")
    )
    assert not view.start_enabled
    assert view.selection_enabled is False


def test_viewmodel_rejects_wrong_snapshot_type():
    with pytest.raises(TypeError):
        CaptureModeViewModel.present(object())  # type: ignore[arg-type]
