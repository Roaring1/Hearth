"""No-card V4L2 resolver and composed mpv CaptureRuntime.

The resolver performs one explicit inventory request, selects by stable USB
identity, resolves one eligible capture node and verifies the exact enumerated
mode. `MpvCaptureRuntime` composes that resolver with the mpv viewer/first-frame
adapter and mandatory injected audio-route and idle-inhibitor adapters. It is
inert until `CaptureSessionManager.start()` calls it; no live audio or desktop
resource implementation is provided in this module.
"""

from __future__ import annotations

from typing import Protocol

from hearth.capture_devices import (
    AudioAssociationStatus,
    AudioSourceCandidate,
    CaptureDevice,
    associate_audio_identity,
    choose_capture_node,
)
from hearth.capture_pipewire import PipeWireAudioInventory, PipeWireAudioProvider
from hearth.capture_provider import CaptureInventory, V4L2InventoryProvider
from hearth.capture_session import (
    AudioRouteLease,
    CapturePlan,
    IdleInhibitorLease,
    ResolvedCaptureDevice,
    UsbIdentity,
    ViewerHandle,
)
from hearth.capture_viewer import MpvCaptureViewer
from hearth.errors import HearthError


class CaptureInventorySource(Protocol):
    """One-shot inventory provider; implementations must not start a watcher."""

    def discover(self) -> CaptureInventory: ...


class CaptureDeviceResolutionError(HearthError):
    """The selected USB device/node/mode cannot be resolved unambiguously."""


class CaptureAudioAssociationError(HearthError):
    """The capture card's USB audio function cannot be resolved safely."""

    def __init__(
        self,
        status: AudioAssociationStatus,
        message: str,
        candidates: tuple[AudioSourceCandidate, ...] = (),
    ) -> None:
        self.status = status
        self.candidates = candidates
        super().__init__(message)


class PipeWireAudioInventorySource(Protocol):
    """One-shot PipeWire snapshot source; implementations must not watch the graph."""

    def discover(self) -> PipeWireAudioInventory: ...


class PipeWireCaptureAudioResolver:
    """Resolve exactly one audio source sharing a capture device's USB identity."""

    def __init__(self, inventory: PipeWireAudioInventorySource | None = None) -> None:
        self._inventory = inventory if inventory is not None else PipeWireAudioProvider()

    def resolve(self, identity: UsbIdentity) -> AudioSourceCandidate:
        try:
            snapshot = self._inventory.discover()
        except Exception as exc:
            raise CaptureAudioAssociationError(
                AudioAssociationStatus.UNKNOWN,
                f"capture audio inventory failed: {exc}",
            ) from exc
        association = associate_audio_identity(
            identity,
            snapshot.sources if snapshot.complete else None,
        )
        if association.status is AudioAssociationStatus.MATCHED:
            selected = association.selected
            if selected is not None:
                return selected
        if association.status is AudioAssociationStatus.AMBIGUOUS:
            labels = ", ".join(candidate.display_name for candidate in association.candidates)
            raise CaptureAudioAssociationError(
                association.status,
                "multiple USB audio endpoints match this capture card; "
                f"user selection required: {labels}",
                association.candidates,
            )
        if association.status is AudioAssociationStatus.NONE:
            raise CaptureAudioAssociationError(
                association.status,
                "no USB audio source matches the selected capture device",
            )
        raise CaptureAudioAssociationError(
            AudioAssociationStatus.UNKNOWN,
            "PipeWire audio inventory is incomplete; capture audio identity is unknown",
        )


class V4L2CaptureResolver:
    """Resolve a persisted USB identity to a fresh node/mode at Start time."""

    def __init__(
        self,
        inventory: CaptureInventorySource | None = None,
        *,
        audio_resolver: PipeWireCaptureAudioResolver | None = None,
    ) -> None:
        self._inventory = inventory if inventory is not None else V4L2InventoryProvider()
        self._audio_resolver = audio_resolver

    def probe_device(self, plan: CapturePlan) -> ResolvedCaptureDevice:
        """Run one inventory check and resolve exactly the requested capture mode."""
        try:
            snapshot = self._inventory.discover()
        except Exception as exc:
            raise CaptureDeviceResolutionError(f"video device inventory failed: {exc}") from exc

        # An issue with unknown identity could hide a second copy of this
        # device. An issue attached to another, positively identified USB card
        # does not make the selected device's inventory ambiguous.
        relevant_issues = tuple(
            issue
            for issue in snapshot.issues
            if issue.identity is None or issue.identity.stable_key == plan.device.stable_key
        )
        if relevant_issues:
            first = relevant_issues[0]
            raise CaptureDeviceResolutionError(
                "video device inventory is incomplete; "
                f"{first.device_path} ({first.stage}): {first.message}"
            )

        matching = tuple(
            device
            for device in snapshot.devices
            if device.identity.stable_key == plan.device.stable_key
        )
        if not matching:
            raise CaptureDeviceResolutionError(
                "selected USB capture device was not found; reconnect it and check again"
            )
        if len(matching) != 1:
            raise CaptureDeviceResolutionError(
                "selected USB identity appears more than once in the inventory"
            )
        device: CaptureDevice = matching[0]
        try:
            node = choose_capture_node(device)
        except ValueError as exc:
            raise CaptureDeviceResolutionError(str(exc)) from exc

        if plan.preferred_mode not in node.modes:
            raise CaptureDeviceResolutionError(
                "the selected video mode is no longer available on this device; "
                "refresh the available modes before starting capture"
            )
        audio_source_selector = None
        if self._audio_resolver is not None:
            audio_source_selector = self._audio_resolver.resolve(device.identity).source_selector
        return ResolvedCaptureDevice(
            identity=device.identity,
            video_node_path=node.device_path,
            mode=plan.preferred_mode,
            audio_source_selector=audio_source_selector,
        )


class CaptureAudioAdapter(Protocol):
    """Injected session-scoped audio validation and route lease boundary."""

    def validate(self, plan: CapturePlan, device: ResolvedCaptureDevice) -> None: ...

    def apply_routes(self, plan: CapturePlan, device: ResolvedCaptureDevice) -> AudioRouteLease: ...

    def verify_routes(self, routes: AudioRouteLease) -> None: ...


class CaptureIdleInhibitorAdapter(Protocol):
    """Injected idle-inhibit lease; acquisition happens only during Start."""

    def acquire(self) -> IdleInhibitorLease: ...


class CaptureViewerAdapter(Protocol):
    """Explicit viewer start adapter; construction must be inert."""

    def start(self, device: ResolvedCaptureDevice) -> ViewerHandle: ...


class CaptureRuntimeAdapterError(HearthError):
    """A composed viewer/runtime adapter cannot satisfy the session contract."""


class MpvCaptureRuntime:
    """Compose one-shot V4L2 resolution, mpv readiness and injected session leases.

    This is the concrete orchestration adapter used by `CaptureSessionManager`.
    It is inert at construction. The audio and idle-inhibitor adapters are
    mandatory injections; no placeholder route mutation or inhibitor is
    created by this class. The active viewer's process and timestamp monitor
    remain owned by `MpvViewerProcess` and the session manager's waiter thread.
    """

    def __init__(
        self,
        *,
        audio: CaptureAudioAdapter,
        inhibitor: CaptureIdleInhibitorAdapter,
        resolver: V4L2CaptureResolver | None = None,
        viewer: CaptureViewerAdapter | None = None,
    ) -> None:
        self._resolver = (
            resolver
            if resolver is not None
            else V4L2CaptureResolver(audio_resolver=PipeWireCaptureAudioResolver())
        )
        self._viewer = viewer if viewer is not None else MpvCaptureViewer()
        self._audio = audio
        self._inhibitor = inhibitor

    def probe_device(self, plan: CapturePlan) -> ResolvedCaptureDevice:
        return self._resolver.probe_device(plan)

    def validate_audio(self, plan: CapturePlan, device: ResolvedCaptureDevice) -> None:
        self._audio.validate(plan, device)

    def start_viewer(self, device: ResolvedCaptureDevice) -> ViewerHandle:
        return self._viewer.start(device)

    def apply_audio_routes(
        self, plan: CapturePlan, device: ResolvedCaptureDevice
    ) -> AudioRouteLease:
        return self._audio.apply_routes(plan, device)

    def acquire_idle_inhibitor(self) -> IdleInhibitorLease:
        return self._inhibitor.acquire()

    def wait_for_first_frame(self, viewer: ViewerHandle, timeout_s: float) -> None:
        wait = getattr(viewer, "wait_for_first_frame", None)
        if not callable(wait):
            raise CaptureRuntimeAdapterError(
                "capture viewer does not expose decoded first-frame readiness"
            )
        wait(timeout_s)

    def verify_audio_routes(self, routes: AudioRouteLease) -> None:
        self._audio.verify_routes(routes)
