"""Hardware-free tests for the Capture Mode session transaction."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from hearth.capture_session import (
    AudioMode,
    CapturePlan,
    CaptureSessionError,
    CaptureSessionManager,
    ResolvedCaptureDevice,
    SessionStage,
    SessionState,
    UsbIdentity,
    VideoMode,
)

IDENTITY = UsbIdentity("1abc", "02ef", serial_number="test-card-001")
MODE = VideoMode("MJPG", 1920, 1080, 60)
PLAN = CapturePlan(IDENTITY, MODE)


@dataclass
class FakeViewer:
    events: list[str]
    stop_failures: int = 0

    def stop(self) -> None:
        self.events.append("stop_viewer")
        if self.stop_failures:
            self.stop_failures -= 1
            raise RuntimeError("viewer did not stop")


@dataclass
class FakeRoutes:
    events: list[str]
    remove_failures: int = 0

    def remove(self) -> None:
        self.events.append("remove_routes")
        if self.remove_failures:
            self.remove_failures -= 1
            raise RuntimeError("route removal not verified")


@dataclass
class FakeInhibitor:
    events: list[str]
    release_failures: int = 0

    def release(self) -> None:
        self.events.append("release_inhibitor")
        if self.release_failures:
            self.release_failures -= 1
            raise RuntimeError("inhibitor release failed")


class FakeRuntime:
    def __init__(self, *, fail_at: str | None = None) -> None:
        self.events: list[str] = []
        self.fail_at = fail_at
        self.viewer: FakeViewer | None = None
        self.routes: FakeRoutes | None = None
        self.inhibitor: FakeInhibitor | None = None
        self.viewer_stop_failures = 0
        self.route_remove_failures = 0
        self.inhibitor_release_failures = 0

    def _step(self, name: str) -> None:
        self.events.append(name)
        if self.fail_at == name:
            raise RuntimeError(f"injected failure at {name}")

    def probe_device(self, plan: CapturePlan) -> ResolvedCaptureDevice:
        self._step("probe_device")
        return ResolvedCaptureDevice(
            plan.device, "/dev/video42", plan.preferred_mode, "usb-audio-7"
        )

    def validate_audio(self, plan: CapturePlan, device: ResolvedCaptureDevice) -> None:
        self._step("validate_audio")

    def start_viewer(self, device: ResolvedCaptureDevice) -> FakeViewer:
        self._step("start_viewer")
        self.viewer = FakeViewer(self.events, stop_failures=self.viewer_stop_failures)
        return self.viewer

    def apply_audio_routes(self, plan: CapturePlan, device: ResolvedCaptureDevice) -> FakeRoutes:
        self._step("apply_audio_routes")
        self.routes = FakeRoutes(self.events, remove_failures=self.route_remove_failures)
        return self.routes

    def acquire_idle_inhibitor(self) -> FakeInhibitor:
        self._step("acquire_idle_inhibitor")
        self.inhibitor = FakeInhibitor(
            self.events, release_failures=self.inhibitor_release_failures
        )
        return self.inhibitor

    def wait_for_first_frame(self, viewer: FakeViewer, timeout_s: float) -> None:
        assert timeout_s > 0
        self._step("wait_for_first_frame")

    def verify_audio_routes(self, routes: FakeRoutes) -> None:
        self._step("verify_audio_routes")


@pytest.mark.parametrize(
    ("failure", "stage", "cleanup"),
    [
        ("probe_device", SessionStage.PROBE_DEVICE, []),
        ("validate_audio", SessionStage.VALIDATE_AUDIO, []),
        ("start_viewer", SessionStage.START_VIEWER, []),
        ("apply_audio_routes", SessionStage.APPLY_AUDIO, ["stop_viewer"]),
        (
            "acquire_idle_inhibitor",
            SessionStage.ACQUIRE_INHIBITOR,
            ["stop_viewer", "remove_routes"],
        ),
        (
            "wait_for_first_frame",
            SessionStage.FIRST_FRAME,
            ["stop_viewer", "release_inhibitor", "remove_routes"],
        ),
        (
            "verify_audio_routes",
            SessionStage.VERIFY_AUDIO,
            ["stop_viewer", "release_inhibitor", "remove_routes"],
        ),
    ],
)
def test_start_failure_rolls_back_every_acquired_resource(
    failure: str,
    stage: SessionStage,
    cleanup: list[str],
) -> None:
    runtime = FakeRuntime(fail_at=failure)
    manager = CaptureSessionManager(runtime)

    with pytest.raises(CaptureSessionError) as excinfo:
        manager.start(PLAN)

    assert excinfo.value.stage is stage
    assert excinfo.value.cleanup_failures == ()
    assert runtime.events[-len(cleanup) :] == cleanup if cleanup else True
    assert manager.snapshot().state is SessionState.DORMANT
    assert manager.snapshot().error == f"injected failure at {failure}"


def test_happy_path_waits_for_frame_and_route_verification_before_ready() -> None:
    runtime = FakeRuntime()
    manager = CaptureSessionManager(runtime)

    started = manager.start(PLAN)

    assert started.state is SessionState.RUNNING
    assert runtime.events == [
        "probe_device",
        "validate_audio",
        "start_viewer",
        "apply_audio_routes",
        "acquire_idle_inhibitor",
        "wait_for_first_frame",
        "verify_audio_routes",
    ]
    stopped = manager.stop()
    assert stopped.state is SessionState.DORMANT
    assert runtime.events[-3:] == ["stop_viewer", "release_inhibitor", "remove_routes"]


def test_start_is_refused_while_session_is_running() -> None:
    runtime = FakeRuntime()
    manager = CaptureSessionManager(runtime)
    manager.start(PLAN)

    with pytest.raises(CaptureSessionError, match="cannot start while the session is running"):
        manager.start(PLAN)

    assert manager.snapshot().state is SessionState.RUNNING


def test_cleanup_failures_are_retained_and_stop_retries_only_failed_resources() -> None:
    runtime = FakeRuntime()
    manager = CaptureSessionManager(runtime)
    manager.start(PLAN)
    assert (
        runtime.viewer is not None and runtime.routes is not None and runtime.inhibitor is not None
    )
    runtime.viewer.stop_failures = 1
    runtime.routes.remove_failures = 1

    with pytest.raises(CaptureSessionError) as excinfo:
        manager.stop()

    assert excinfo.value.stage is SessionStage.CLEANUP
    assert excinfo.value.cleanup_failures == (
        "viewer: viewer did not stop",
        "share routes: route removal not verified",
    )
    assert manager.snapshot().state is SessionState.FAILED
    assert runtime.events[-3:] == ["stop_viewer", "release_inhibitor", "remove_routes"]

    # Successful cleanup stages are not repeated; failed leases are retried.
    stopped = manager.stop()
    assert stopped.state is SessionState.DORMANT
    assert runtime.events[-2:] == ["stop_viewer", "remove_routes"]


def test_start_failure_with_incomplete_rollback_requires_explicit_stop_retry() -> None:
    runtime = FakeRuntime(fail_at="wait_for_first_frame")
    runtime.viewer_stop_failures = 1
    manager = CaptureSessionManager(runtime)

    with pytest.raises(CaptureSessionError) as excinfo:
        manager.start(PLAN)

    assert excinfo.value.cleanup_failures == ("viewer: viewer did not stop",)
    assert manager.snapshot().state is SessionState.FAILED
    assert manager.snapshot().cleanup_failures == ("viewer: viewer did not stop",)

    # The manager refuses a new session until the remaining viewer lease is
    # successfully closed. Stop retries that lease and reaches DORMANT.
    with pytest.raises(CaptureSessionError, match="cannot start while the session is failed"):
        manager.start(PLAN)
    assert manager.stop().state is SessionState.DORMANT


def test_stop_while_dormant_is_idempotent() -> None:
    runtime = FakeRuntime()
    manager = CaptureSessionManager(runtime)
    before = manager.snapshot()

    after = manager.stop()

    assert after.state is SessionState.DORMANT
    assert after.revision == before.revision
    assert runtime.events == []


def test_plan_stores_stable_identity_not_volatile_video_node_path() -> None:
    identity = UsbIdentity("1ABC", "02Ef", port_path="1-3.2")
    assert identity.stable_key == "usb:1abc:02ef:port:1-3.2"
    assert "/dev/video" not in identity.stable_key
    with pytest.raises(ValueError, match="serial or physical port"):
        UsbIdentity("1abc", "02ef")


def test_identity_prefers_serial_over_current_usb_port() -> None:
    identity = UsbIdentity("1abc", "02ef", serial_number="unit-serial", port_path="1-4")
    assert identity.stable_key == "usb:1abc:02ef:serial:unit-serial"


def test_video_mode_rejects_invalid_capabilities() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        VideoMode("MJPG", 0, 1080, 60)
    with pytest.raises(ValueError, match="frame rate"):
        VideoMode("YUYV", 1920, 1080, 60, 0)
    with pytest.raises(ValueError, match="four-character"):
        VideoMode("MJ", 1920, 1080, 60, 1_000)


def test_capture_plan_defaults_to_xbox_audio_mode() -> None:
    assert PLAN.audio_mode is AudioMode.XBOX


def test_resolved_device_rejects_volatile_audio_source_ids() -> None:
    for selector in ("42", "card2", "alsa_card=3", "node.id=9"):
        with pytest.raises(ValueError, match="stable, not a volatile numeric"):
            ResolvedCaptureDevice(IDENTITY, "/dev/video42", MODE, selector)


def test_unexpected_viewer_exit_cleans_session_and_requires_explicit_stop_ack() -> None:
    runtime = FakeRuntime()
    manager = CaptureSessionManager(runtime)
    manager.start(PLAN)

    failed = manager.viewer_exited(17)

    assert failed.state is SessionState.FAILED
    assert failed.error == "Share Output viewer exited unexpectedly (code 17)"
    assert failed.cleanup_failures == ()
    assert runtime.events[-3:] == ["stop_viewer", "release_inhibitor", "remove_routes"]
    with pytest.raises(CaptureSessionError, match="cannot start while the session is failed"):
        manager.start(PLAN)
    assert manager.stop().state is SessionState.DORMANT


def test_unexpected_viewer_exit_retains_failed_route_cleanup_for_stop_retry() -> None:
    runtime = FakeRuntime()
    manager = CaptureSessionManager(runtime)
    manager.start(PLAN)
    assert runtime.routes is not None
    runtime.routes.remove_failures = 1

    failed = manager.viewer_exited(-15)

    assert failed.state is SessionState.FAILED
    assert failed.cleanup_failures == ("share routes: route removal not verified",)
    # Viewer and inhibitor cleanup succeeded once and are not repeated.
    assert runtime.events[-3:] == ["stop_viewer", "release_inhibitor", "remove_routes"]
    assert manager.stop().state is SessionState.DORMANT
    assert runtime.events[-1] == "remove_routes"


def test_viewer_exit_outside_running_state_is_rejected() -> None:
    manager = CaptureSessionManager(FakeRuntime())
    with pytest.raises(
        CaptureSessionError, match="viewer exit reported while the session is dormant"
    ):
        manager.viewer_exited(1)
