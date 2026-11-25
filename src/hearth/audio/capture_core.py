"""Pure on-demand capture-device/audio-session coordinator.

No hardware discovery, audio graph access, subprocesses, timers, UI, or persistence
is performed here. All I/O is supplied through an injected backend and invoked only
when the caller explicitly requests refresh/start/stop.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class CaptureCoreState(StrEnum):
    IDLE = "idle"
    READY = "ready"
    STARTING = "starting"
    ACTIVE = "active"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CaptureDevice:
    device_id: str
    name: str
    backend: str
    channels: int = 1
    sample_rate: int | None = None
    available: bool = True

    def __post_init__(self) -> None:
        for label, value in (
            ("device_id", self.device_id),
            ("name", self.name),
            ("backend", self.backend),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be non-blank text")
        if (
            isinstance(self.channels, bool)
            or not isinstance(self.channels, int)
            or self.channels < 1
        ):
            raise ValueError("channels must be a positive integer")
        if self.sample_rate is not None and (
            isinstance(self.sample_rate, bool)
            or not isinstance(self.sample_rate, int)
            or self.sample_rate <= 0
        ):
            raise ValueError("sample_rate must be a positive integer or None")
        if not isinstance(self.available, bool):
            raise ValueError("available must be bool")


@dataclass(frozen=True, slots=True)
class AudioSession:
    session_id: str
    device_id: str
    backend: str
    active: bool = True

    def __post_init__(self) -> None:
        for label, value in (
            ("session_id", self.session_id),
            ("device_id", self.device_id),
            ("backend", self.backend),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be non-blank text")
        if not isinstance(self.active, bool):
            raise ValueError("active must be bool")


@dataclass(frozen=True, slots=True)
class CaptureCoreSnapshot:
    state: CaptureCoreState
    devices: tuple[CaptureDevice, ...]
    selected_device_id: str | None
    session: AudioSession | None
    error: str | None
    revision: int


class CaptureBackend(Protocol):
    """Explicit I/O seam. Implementations must not return partially-owned sessions."""

    def discover_devices(self) -> tuple[CaptureDevice, ...]: ...
    def start_session(self, device: CaptureDevice) -> AudioSession: ...
    def stop_session(self, session: AudioSession) -> None: ...


class OnDemandCaptureCore:
    """Small serialized state machine; all backend calls are demand-triggered."""

    def __init__(self, backend: CaptureBackend) -> None:
        self._backend = backend
        self._state = CaptureCoreState.IDLE
        self._devices: tuple[CaptureDevice, ...] = ()
        self._selected: str | None = None
        self._session: AudioSession | None = None
        self._error: str | None = None
        self._revision = 0

    def snapshot(self) -> CaptureCoreSnapshot:
        return CaptureCoreSnapshot(
            self._state, self._devices, self._selected, self._session, self._error, self._revision
        )

    def refresh(self) -> CaptureCoreSnapshot:
        if self._state in (
            CaptureCoreState.STARTING,
            CaptureCoreState.ACTIVE,
            CaptureCoreState.STOPPING,
        ):
            self._error = "device inventory cannot be refreshed during an active session"
            self._revision += 1
            return self.snapshot()
        self._state = CaptureCoreState.IDLE
        self._error = None
        try:
            found = self._backend.discover_devices()
            if not isinstance(found, tuple) or any(
                not isinstance(item, CaptureDevice) for item in found
            ):
                raise TypeError("backend returned an invalid device inventory")
            ids = [device.device_id for device in found]
            if len(ids) != len(set(ids)):
                raise ValueError("backend returned duplicate device identities")
            self._devices = found
            if self._selected not in {device.device_id for device in found if device.available}:
                self._selected = None
            self._state = (
                CaptureCoreState.READY
                if any(device.available for device in found)
                else CaptureCoreState.IDLE
            )
        except Exception as exc:
            self._devices = ()
            self._selected = None
            self._state = CaptureCoreState.ERROR
            self._error = f"device discovery failed: {exc}"
        self._revision += 1
        return self.snapshot()

    def select(self, device_id: str) -> CaptureCoreSnapshot:
        if self._state in (
            CaptureCoreState.STARTING,
            CaptureCoreState.ACTIVE,
            CaptureCoreState.STOPPING,
        ):
            self._error = "device selection is locked during an active session"
            self._revision += 1
            return self.snapshot()
        device = next(
            (item for item in self._devices if item.device_id == device_id and item.available), None
        )
        if device is None:
            self._error = "selected device is unavailable or not in the current inventory"
            self._state = CaptureCoreState.ERROR
            self._revision += 1
            return self.snapshot()
        self._selected = device_id
        self._error = None
        self._state = CaptureCoreState.READY
        self._revision += 1
        return self.snapshot()

    def start(self) -> CaptureCoreSnapshot:
        if self._state in (
            CaptureCoreState.STARTING,
            CaptureCoreState.ACTIVE,
            CaptureCoreState.STOPPING,
        ):
            self._error = "capture session is already starting or active"
            self._revision += 1
            return self.snapshot()
        device = next(
            (item for item in self._devices if item.device_id == self._selected and item.available),
            None,
        )
        if device is None:
            self._error = "select an available capture device before starting"
            self._state = CaptureCoreState.ERROR
            self._revision += 1
            return self.snapshot()
        self._state = CaptureCoreState.STARTING
        self._error = None
        self._revision += 1
        try:
            session = self._backend.start_session(device)
            if (
                not isinstance(session, AudioSession)
                or not session.active
                or session.device_id != device.device_id
            ):
                raise RuntimeError(
                    "backend did not return a valid active session for the selected device"
                )
            self._session = session
            self._state = CaptureCoreState.ACTIVE
        except Exception as exc:
            self._session = None
            self._state = CaptureCoreState.ERROR
            self._error = f"session start failed: {exc}"
        self._revision += 1
        return self.snapshot()

    def stop(self) -> CaptureCoreSnapshot:
        if self._session is None:
            self._state = CaptureCoreState.READY if self._devices else CaptureCoreState.IDLE
            self._error = None
            self._revision += 1
            return self.snapshot()
        session = self._session
        self._state = CaptureCoreState.STOPPING
        self._revision += 1
        try:
            self._backend.stop_session(session)
            self._session = None
            self._error = None
            self._state = CaptureCoreState.READY if self._devices else CaptureCoreState.IDLE
        except Exception as exc:
            # Keep the handle: a retry may still be required by the backend.
            self._state = CaptureCoreState.ERROR
            self._error = f"session stop failed: {exc}"
        self._revision += 1
        return self.snapshot()
