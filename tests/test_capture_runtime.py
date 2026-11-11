"""V4L2 resolution seam tests consume fake inventories only."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from hearth.capture_devices import (
    CaptureDevice,
    CaptureNode,
    V4L2Capabilities,
)
from hearth.capture_provider import CaptureInventory, CaptureProbeIssue
from hearth.capture_runtime import CaptureDeviceResolutionError, V4L2CaptureResolver
from hearth.capture_session import CapturePlan, UsbIdentity, VideoMode

CARD = UsbIdentity("1abc", "02ef", serial_number="GUV302G-001")
MODE = VideoMode("MJPG", 1920, 1080, 60)
OTHER_MODE = VideoMode("MJPG", 1280, 720, 60)


def _device(identity: UsbIdentity = CARD, path: str = "/dev/video4") -> CaptureDevice:
    node = CaptureNode(
        path,
        "Fixture capture card",
        identity,
        V4L2Capabilities(True, False, False),
        (MODE, OTHER_MODE),
    )
    return CaptureDevice(identity, (node,))


@dataclass
class FakeInventory:
    snapshot: CaptureInventory
    calls: int = 0

    def discover(self) -> CaptureInventory:
        self.calls += 1
        return self.snapshot


def test_resolver_selects_exact_device_identity_node_and_mode() -> None:
    provider = FakeInventory(
        CaptureInventory(
            devices=(
                _device(UsbIdentity("1abc", "02ef", serial_number="other-card"), "/dev/video6"),
                _device(),
            )
        )
    )
    result = V4L2CaptureResolver(provider).probe_device(CapturePlan(CARD, MODE))

    assert result.identity == CARD
    assert result.video_node_path == "/dev/video4"
    assert result.mode == MODE
    assert result.audio_source_selector is None
    assert provider.calls == 1


def test_resolver_fails_when_identity_is_absent() -> None:
    provider = FakeInventory(CaptureInventory(devices=(_device(),)))
    absent = UsbIdentity("1abc", "02ef", serial_number="different-serial")

    with pytest.raises(CaptureDeviceResolutionError, match="was not found"):
        V4L2CaptureResolver(provider).probe_device(CapturePlan(absent, MODE))


def test_resolver_fails_closed_on_incomplete_inventory() -> None:
    provider = FakeInventory(
        CaptureInventory(
            devices=(_device(),),
            issues=(CaptureProbeIssue("/dev/video8", "USB identity", "permission denied"),),
        )
    )

    with pytest.raises(CaptureDeviceResolutionError, match="inventory is incomplete"):
        V4L2CaptureResolver(provider).probe_device(CapturePlan(CARD, MODE))


def test_resolver_requires_exact_enumerated_mode() -> None:
    unavailable = VideoMode("YUYV", 1920, 1080, 60)
    provider = FakeInventory(CaptureInventory(devices=(_device(),)))

    with pytest.raises(CaptureDeviceResolutionError, match="mode is no longer available"):
        V4L2CaptureResolver(provider).probe_device(CapturePlan(CARD, unavailable))


def test_resolver_refuses_multiple_eligible_video_nodes_on_same_usb_device() -> None:
    first = _device().nodes[0]
    second = CaptureNode(
        "/dev/video5",
        "Fixture capture card secondary",
        CARD,
        first.capabilities,
        first.modes,
    )
    device = CaptureDevice(CARD, (first, second))
    provider = FakeInventory(CaptureInventory(devices=(device,)))

    with pytest.raises(CaptureDeviceResolutionError, match="user selection required"):
        V4L2CaptureResolver(provider).probe_device(CapturePlan(CARD, MODE))


def test_injected_inventory_provider_is_called_exactly_once_per_probe() -> None:
    provider = FakeInventory(CaptureInventory(devices=(_device(),)))
    resolver = V4L2CaptureResolver(provider)

    resolver.probe_device(CapturePlan(CARD, MODE))
    resolver.probe_device(CapturePlan(CARD, OTHER_MODE))

    assert provider.calls == 2


class FakeViewerHandle:
    def __init__(self, events: list[str], *, fail_first_frame: bool = False) -> None:
        from threading import Event

        self.events = events
        self.exit_event = Event()
        self.fail_first_frame = fail_first_frame
        self.exit_code = 0

    def poll(self) -> int | None:
        return self.exit_code if self.exit_event.is_set() else None

    def wait(self, timeout: float | None = None) -> int:
        if not self.exit_event.wait(timeout):
            raise TimeoutError("viewer still active")
        return self.exit_code

    def stop(self, *, timeout: float = 1.0) -> int:
        self.events.append("viewer_stop")
        self.exit_code = -15
        self.exit_event.set()
        return self.exit_code

    def wait_for_first_frame(self, timeout_s: float) -> None:
        self.events.append("first_frame")
        assert timeout_s > 0
        if self.fail_first_frame:
            raise TimeoutError("no decoded frame")

    def monitor_until_exit(self) -> int:
        return self.wait()


class FakeViewerAdapter:
    def __init__(self, events: list[str], *, fail_first_frame: bool = False) -> None:
        self.events = events
        self.fail_first_frame = fail_first_frame
        self.handle: FakeViewerHandle | None = None

    def start(self, device: object) -> FakeViewerHandle:
        self.events.append("viewer_start")
        self.handle = FakeViewerHandle(self.events, fail_first_frame=self.fail_first_frame)
        return self.handle


class ComposedRoutes:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def remove(self) -> None:
        self.events.append("remove_routes")


class ComposedInhibitor:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def release(self) -> None:
        self.events.append("release_inhibitor")


class FakeAudioAdapter:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def validate(self, plan: CapturePlan, device: object) -> None:
        self.events.append("audio_validate")

    def apply_routes(self, plan: CapturePlan, device: object) -> ComposedRoutes:
        self.events.append("routes_apply")
        return ComposedRoutes(self.events)

    def verify_routes(self, routes: ComposedRoutes) -> None:
        self.events.append("routes_verify")


class FakeIdleAdapter:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def acquire(self) -> ComposedInhibitor:
        self.events.append("inhibitor_acquire")
        return ComposedInhibitor(self.events)


def _composed_runtime(*, fail_first_frame: bool = False):
    from hearth.capture_runtime import MpvCaptureRuntime

    events: list[str] = []
    inventory = FakeInventory(CaptureInventory(devices=(_device(),)))
    runtime = MpvCaptureRuntime(
        resolver=V4L2CaptureResolver(inventory),
        viewer=FakeViewerAdapter(events, fail_first_frame=fail_first_frame),
        audio=FakeAudioAdapter(events),
        inhibitor=FakeIdleAdapter(events),
    )
    return runtime, events, inventory


def test_composed_mpv_runtime_runs_session_in_order_and_is_inert_until_start() -> None:
    from hearth.capture_session import CaptureSessionManager, SessionState

    runtime, events, inventory = _composed_runtime()
    assert events == []
    assert inventory.calls == 0

    manager = CaptureSessionManager(runtime)
    snapshot = manager.start(CapturePlan(CARD, MODE))

    assert snapshot.state is SessionState.RUNNING
    assert events == [
        "audio_validate",
        "viewer_start",
        "routes_apply",
        "inhibitor_acquire",
        "first_frame",
        "routes_verify",
    ]
    assert inventory.calls == 1

    stopped = manager.stop()

    assert stopped.state is SessionState.DORMANT
    assert events[-3:] == ["viewer_stop", "release_inhibitor", "remove_routes"]


def test_composed_mpv_runtime_rolls_back_if_first_frame_never_arrives() -> None:
    from hearth.capture_session import (
        CaptureSessionError,
        CaptureSessionManager,
        SessionStage,
        SessionState,
    )

    runtime, events, inventory = _composed_runtime(fail_first_frame=True)
    manager = CaptureSessionManager(runtime)

    with pytest.raises(CaptureSessionError) as caught:
        manager.start(CapturePlan(CARD, MODE))

    assert caught.value.stage is SessionStage.FIRST_FRAME
    assert manager.snapshot().state is SessionState.DORMANT
    assert events == [
        "audio_validate",
        "viewer_start",
        "routes_apply",
        "inhibitor_acquire",
        "first_frame",
        "viewer_stop",
        "release_inhibitor",
        "remove_routes",
    ]
    assert inventory.calls == 1


def test_resolver_ignores_a_probe_issue_for_another_identified_usb_device() -> None:
    other_identity = UsbIdentity("1abc", "02ef", serial_number="other-card")
    provider = FakeInventory(
        CaptureInventory(
            devices=(_device(),),
            issues=(
                CaptureProbeIssue(
                    "/dev/video8",
                    "Device Caps",
                    "busy",
                    other_identity,
                ),
            ),
        )
    )

    result = V4L2CaptureResolver(provider).probe_device(CapturePlan(CARD, MODE))

    assert result.video_node_path == "/dev/video4"


def test_resolver_fails_on_a_probe_issue_with_the_selected_usb_identity() -> None:
    provider = FakeInventory(
        CaptureInventory(
            devices=(_device(),),
            issues=(CaptureProbeIssue("/dev/video5", "Video formats", "busy", CARD),),
        )
    )

    with pytest.raises(CaptureDeviceResolutionError, match="inventory is incomplete"):
        V4L2CaptureResolver(provider).probe_device(CapturePlan(CARD, MODE))
