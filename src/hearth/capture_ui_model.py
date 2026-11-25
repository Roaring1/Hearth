"""Pure presentation model for the on-demand Capture Mode core.

This module is deliberately toolkit-free: it maps immutable capture-core
snapshots into UI labels and action availability. It performs no I/O and
never changes the core/session state.
"""

from __future__ import annotations

from dataclasses import dataclass

from hearth.audio.capture_core import CaptureCoreSnapshot, CaptureCoreState, CaptureDevice


@dataclass(frozen=True, slots=True)
class CaptureDeviceOption:
    device_id: str
    label: str
    available: bool


@dataclass(frozen=True, slots=True)
class CaptureModeViewState:
    state: CaptureCoreState
    status: str
    detail: str
    devices: tuple[CaptureDeviceOption, ...]
    selected_device_id: str | None
    refresh_enabled: bool
    selection_enabled: bool
    start_enabled: bool
    stop_enabled: bool
    error_visible: bool


class CaptureModeViewModel:
    """Map capture-core snapshots to stable UI copy and action states."""

    @staticmethod
    def present(snapshot: CaptureCoreSnapshot) -> CaptureModeViewState:
        if not isinstance(snapshot, CaptureCoreSnapshot):
            raise TypeError("snapshot must be a CaptureCoreSnapshot")

        available = tuple(device for device in snapshot.devices if device.available)
        options = tuple(
            CaptureDeviceOption(
                device.device_id,
                CaptureModeViewModel._device_label(device),
                device.available,
            )
            for device in snapshot.devices
        )
        session_owned = snapshot.session is not None
        can_refresh = not session_owned and snapshot.state in (
            CaptureCoreState.IDLE,
            CaptureCoreState.READY,
            CaptureCoreState.ERROR,
        )
        selected_is_available = snapshot.selected_device_id in {
            device.device_id for device in available
        }
        can_select = can_refresh and bool(available)
        can_start = (
            snapshot.state is CaptureCoreState.READY and selected_is_available and not session_owned
        )
        can_stop = session_owned

        copy = {
            CaptureCoreState.IDLE: (
                "No capture device ready",
                "Refresh to check for capture devices.",
            ),
            CaptureCoreState.READY: (
                "Capture device ready",
                "Select an available device to begin.",
            ),
            CaptureCoreState.STARTING: (
                "Starting capture…",
                "The capture session is being prepared.",
            ),
            CaptureCoreState.ACTIVE: (
                "Capture active",
                "The selected capture session is running.",
            ),
            CaptureCoreState.STOPPING: (
                "Stopping capture…",
                "The capture session is releasing its resources.",
            ),
            CaptureCoreState.ERROR: (
                "Capture needs attention",
                "Review the error and retry the available action.",
            ),
        }
        status, detail = copy[snapshot.state]
        if snapshot.state is CaptureCoreState.READY and not selected_is_available:
            detail = "Choose an available capture device before starting."
        elif snapshot.state is CaptureCoreState.ERROR and session_owned:
            detail = "Cleanup did not finish. Stop is available to retry resource release."
        elif snapshot.state is CaptureCoreState.ERROR and snapshot.error:
            detail = "The operation failed. Retry or refresh to check again."
        elif snapshot.state is CaptureCoreState.IDLE and not available:
            detail = "No available capture devices were found. Refresh to check again."

        return CaptureModeViewState(
            state=snapshot.state,
            status=status,
            detail=detail,
            devices=options,
            selected_device_id=snapshot.selected_device_id,
            refresh_enabled=can_refresh,
            selection_enabled=can_select,
            start_enabled=can_start,
            stop_enabled=can_stop,
            error_visible=snapshot.state is CaptureCoreState.ERROR,
        )

    @staticmethod
    def _device_label(device: CaptureDevice) -> str:
        availability = "" if device.available else " (unavailable)"
        return f"{device.name}{availability}"
