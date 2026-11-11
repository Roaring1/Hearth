"""Toolkit-free transaction coordinator for an on-demand capture session.

The concrete V4L2, PipeWire, viewer and idle-inhibit adapters are intentionally
injected. This module owns ordering, state, and rollback only, so it can be
exercised with fakes on a machine with no capture card or audio graph.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock, Thread, current_thread
from typing import Protocol

from hearth.errors import HearthError


class AudioMode(StrEnum):
    """Which permitted audio family the active capture session should add."""

    XBOX = "xbox"
    NOBARA = "nobara"
    BOTH = "both"


class SessionState(StrEnum):
    """Externally visible lifecycle states; FAILED means cleanup needs attention."""

    DORMANT = "dormant"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


class SessionStage(StrEnum):
    """The operation whose failure prevented a session from becoming ready."""

    PROBE_DEVICE = "probe device"
    VALIDATE_AUDIO = "validate audio"
    START_VIEWER = "start viewer"
    APPLY_AUDIO = "apply audio routes"
    ACQUIRE_INHIBITOR = "acquire idle inhibitor"
    FIRST_FRAME = "wait for first frame"
    VERIFY_AUDIO = "verify audio routes"
    CLEANUP = "clean up session"


@dataclass(frozen=True, slots=True)
class UsbIdentity:
    """Stable capture-device selector; never stores a volatile /dev/videoN path.

    A USB serial is preferred. Physical USB port topology is an allowed
    fallback for devices without a serial, but it intentionally changes if the
    device is moved to another port. If neither is known, fail closed.
    """

    vendor_id: str
    product_id: str
    serial_number: str | None = field(default=None, repr=False)
    port_path: str | None = None

    def __post_init__(self) -> None:
        for label, value in (("vendor_id", self.vendor_id), ("product_id", self.product_id)):
            if not re.fullmatch(r"[0-9a-fA-F]{4}", value):
                raise ValueError(f"{label} must be four hexadecimal digits")
        if self.serial_number is not None and not self.serial_number.strip():
            raise ValueError("serial_number must not be blank")
        if self.port_path is not None and not self.port_path.strip():
            raise ValueError("port_path must not be blank")
        if self.serial_number is None and self.port_path is None:
            raise ValueError("device identity needs a USB serial or physical port path")

    @property
    def stable_key(self) -> str:
        """Canonical key suitable for saved preference selectors."""
        usb = f"usb:{self.vendor_id.lower()}:{self.product_id.lower()}"
        if self.serial_number is not None:
            return f"{usb}:serial:{self.serial_number}"
        return f"{usb}:port:{self.port_path}"


@dataclass(frozen=True, slots=True)
class VideoMode:
    """One enumerated V4L2 pixel format, frame size and frame interval."""

    pixel_format: str
    width: int
    height: int
    fps_numerator: int
    fps_denominator: int = 1

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9 ]{4}", self.pixel_format):
            raise ValueError("pixel_format must be a four-character V4L2 format")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("video dimensions must be positive")
        if self.fps_numerator <= 0 or self.fps_denominator <= 0:
            raise ValueError("frame rate must be positive")


@dataclass(frozen=True, slots=True)
class CapturePlan:
    """User intent only; never persist device node numbers or PipeWire ids."""

    device: UsbIdentity
    preferred_mode: VideoMode
    audio_mode: AudioMode = AudioMode.XBOX


@dataclass(frozen=True, slots=True)
class ResolvedCaptureDevice:
    """One start-time device resolution, including the currently volatile node."""

    identity: UsbIdentity
    video_node_path: str
    mode: VideoMode
    # Resolved stable selector only; never a numeric PipeWire/ALSA object id.
    audio_source_selector: str | None = None

    def __post_init__(self) -> None:
        if not self.video_node_path.startswith("/dev/"):
            raise ValueError("resolved video node must be an absolute /dev path")
        if self.audio_source_selector is not None:
            selector = self.audio_source_selector.strip()
            if (
                not selector
                or selector.isdecimal()
                or re.fullmatch(r"(?:card|alsa_card|node[._-]?id)[=:]?\d+", selector, re.IGNORECASE)
            ):
                raise ValueError("audio source selector must be stable, not a volatile numeric ID")


class ViewerHandle(Protocol):
    """Owned viewer process; stop must also release the viewer's device handle."""

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def stop(self, *, timeout: float = 1.0) -> int: ...


class AudioRouteLease(Protocol):
    """Only the route links created for this session; removal must be scoped."""

    def remove(self) -> None: ...


class IdleInhibitorLease(Protocol):
    """One explicit inhibitor acquired for the active session."""

    def release(self) -> None: ...


class CaptureRuntime(Protocol):
    """Injectable boundary for real V4L2, PipeWire, viewer and desktop adapters.

    Adapters must either return a fully owned resource or roll back their own
    partial work before raising. Persistent graph mutations are not permitted
    by this interface; an audio adapter must return a session-scoped lease.
    """

    def probe_device(self, plan: CapturePlan) -> ResolvedCaptureDevice: ...

    def validate_audio(self, plan: CapturePlan, device: ResolvedCaptureDevice) -> None: ...

    def start_viewer(self, device: ResolvedCaptureDevice) -> ViewerHandle: ...

    def apply_audio_routes(
        self, plan: CapturePlan, device: ResolvedCaptureDevice
    ) -> AudioRouteLease: ...

    def acquire_idle_inhibitor(self) -> IdleInhibitorLease: ...

    def wait_for_first_frame(self, viewer: ViewerHandle, timeout_s: float) -> None: ...

    def verify_audio_routes(self, routes: AudioRouteLease) -> None: ...


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """Safe state for the UI; no raw device paths or runtime resource handles."""

    state: SessionState
    plan: CapturePlan | None
    stage: SessionStage | None
    error: str | None
    cleanup_failures: tuple[str, ...]
    revision: int
    no_signal: bool = False


class CaptureSessionError(HearthError):
    """A start/stop operation failed, with its stage and incomplete cleanup."""

    def __init__(
        self,
        stage: SessionStage,
        message: str,
        *,
        cleanup_failures: tuple[str, ...] = (),
        cause: BaseException | None = None,
    ) -> None:
        self.stage = stage
        self.reason = message
        self.cleanup_failures = cleanup_failures
        # Keep typed adapter context (for example ambiguous audio candidates)
        # available to the caller without putting raw objects in SessionSnapshot.
        self.cause = cause
        suffix = "; cleanup incomplete: " + ", ".join(cleanup_failures) if cleanup_failures else ""
        super().__init__(f"{stage.value}: {message}{suffix}")


class CaptureSessionManager:
    """Serialize Start/Stop, prove readiness, and retain failed cleanup leases.

    Startup stages are ordered: probe, audio safety validation, viewer,
    session routes, idle inhibitor, first frame, route verification. Stop and
    rollback are deliberately ordered: stop viewer (releasing its device fd),
    release inhibitor, then remove session-owned audio routes.

    A failed cleanup leaves the manager in FAILED with the uncleaned lease
    retained. A later stop() retries only those resources. A new start is
    refused until cleanup reaches DORMANT.
    """

    def __init__(self, runtime: CaptureRuntime, *, first_frame_timeout_s: float = 5.0) -> None:
        if first_frame_timeout_s <= 0:
            raise ValueError("first_frame_timeout_s must be positive")
        self._runtime = runtime
        self._first_frame_timeout_s = first_frame_timeout_s
        self._lock = RLock()
        self._state = SessionState.DORMANT
        self._plan: CapturePlan | None = None
        self._stage: SessionStage | None = None
        self._error: str | None = None
        self._cleanup_failures: tuple[str, ...] = ()
        self._revision = 0
        self._viewer: ViewerHandle | None = None
        self._routes: AudioRouteLease | None = None
        self._inhibitor: IdleInhibitorLease | None = None
        self._viewer_watcher: Thread | None = None
        self._listeners: set[Callable[[SessionSnapshot], None]] = set()

    def subscribe_no_signal(
        self,
        listener: Callable[[SessionSnapshot], None],
        *,
        emit_current: bool = True,
    ) -> Callable[[], None]:
        """Subscribe to no-signal transitions; return an unsubscribe function.

        Lifecycle changes are returned synchronously by start()/stop(). This
        subscription reports only asynchronous viewer no-signal changes.
        Callbacks run on the viewer monitor thread. GTK callers must marshal
        them to the main context (for example with GLib.idle_add). No timer or
        background watcher is created by subscribing.
        """
        with self._lock:
            self._listeners.add(listener)
            current = self.snapshot() if emit_current else None
        if current is not None:
            self._notify_one(listener, current)

        def unsubscribe() -> None:
            with self._lock:
                self._listeners.discard(listener)

        return unsubscribe

    @staticmethod
    def _notify_one(listener: Callable[[SessionSnapshot], None], snapshot: SessionSnapshot) -> None:
        # An observer is never allowed to affect capture ownership or cleanup.
        with suppress(Exception):
            listener(snapshot)

    def _viewer_no_signal_changed(self, _value: bool) -> None:
        with self._lock:
            if self._state is not SessionState.RUNNING or self._viewer is None:
                return
            self._revision += 1
            snapshot = self.snapshot()
            listeners = tuple(self._listeners)
        for listener in listeners:
            self._notify_one(listener, snapshot)

    def snapshot(self) -> SessionSnapshot:
        """Return a copy of UI-safe lifecycle state."""
        with self._lock:
            return SessionSnapshot(
                state=self._state,
                plan=self._plan,
                stage=self._stage,
                error=self._error,
                cleanup_failures=self._cleanup_failures,
                revision=self._revision,
                no_signal=bool(getattr(self._viewer, "no_signal", False)),
            )

    def start(self, plan: CapturePlan) -> SessionSnapshot:
        """Start a capture transaction and return only after first-frame checks."""
        with self._lock:
            if self._state is not SessionState.DORMANT:
                raise CaptureSessionError(
                    self._stage or SessionStage.CLEANUP,
                    f"cannot start while the session is {self._state.value}",
                    cleanup_failures=self._cleanup_failures,
                )
            self._plan = plan
            self._error = None
            self._cleanup_failures = ()
            self._state = SessionState.STARTING
            self._revision += 1
            stage = SessionStage.PROBE_DEVICE
            self._stage = stage
            try:
                device = self._runtime.probe_device(plan)
                if device.identity != plan.device:
                    raise ValueError("the resolved device does not match the selected USB identity")

                stage = SessionStage.VALIDATE_AUDIO
                self._stage = stage
                self._runtime.validate_audio(plan, device)

                stage = SessionStage.START_VIEWER
                self._stage = stage
                self._viewer = self._runtime.start_viewer(device)
                set_no_signal_callback = getattr(self._viewer, "set_no_signal_callback", None)
                if callable(set_no_signal_callback):
                    set_no_signal_callback(self._viewer_no_signal_changed)

                stage = SessionStage.APPLY_AUDIO
                self._stage = stage
                self._routes = self._runtime.apply_audio_routes(plan, device)

                stage = SessionStage.ACQUIRE_INHIBITOR
                self._stage = stage
                self._inhibitor = self._runtime.acquire_idle_inhibitor()

                stage = SessionStage.FIRST_FRAME
                self._stage = stage
                self._runtime.wait_for_first_frame(self._viewer, self._first_frame_timeout_s)

                stage = SessionStage.VERIFY_AUDIO
                self._stage = stage
                self._runtime.verify_audio_routes(self._routes)

            except Exception as exc:
                failures = self._cleanup_resources()
                self._state = SessionState.FAILED if failures else SessionState.DORMANT
                self._stage = stage
                self._error = str(exc) or exc.__class__.__name__
                self._cleanup_failures = tuple(failures)
                self._revision += 1
                raise CaptureSessionError(
                    stage,
                    self._error,
                    cleanup_failures=self._cleanup_failures,
                    cause=exc,
                ) from exc

            self._state = SessionState.RUNNING
            self._stage = None
            self._error = None
            self._cleanup_failures = ()
            self._revision += 1
            viewer = self._viewer
            if viewer is not None:
                watcher = Thread(
                    target=self._watch_viewer,
                    args=(viewer,),
                    name="hearth-capture-viewer-exit",
                    daemon=True,
                )
                self._viewer_watcher = watcher
                try:
                    watcher.start()
                except RuntimeError as exc:
                    self._viewer_watcher = None
                    self._state = SessionState.FAILED
                    self._stage = SessionStage.CLEANUP
                    self._error = f"could not monitor Share Output viewer: {exc}"
                    failures = self._cleanup_resources()
                    self._cleanup_failures = tuple(failures)
                    self._revision += 1
                    raise CaptureSessionError(
                        SessionStage.CLEANUP,
                        self._error,
                        cleanup_failures=self._cleanup_failures,
                    ) from exc
            return self.snapshot()

    def _watch_viewer(self, viewer: ViewerHandle) -> None:
        """Wait only while RUNNING; no polling or dormant watcher exists."""
        try:
            try:
                monitor = getattr(viewer, "monitor_until_exit", None)
                returncode = monitor() if callable(monitor) else viewer.wait()
            except Exception as exc:
                self.viewer_exited(
                    -1,
                    detail=f"viewer stream/process monitor failed: {exc}",
                    ignore_if_not_running=True,
                )
            else:
                self.viewer_exited(returncode, ignore_if_not_running=True)
        finally:
            with self._lock:
                if self._viewer_watcher is current_thread():
                    self._viewer_watcher = None

    def viewer_exited(
        self,
        returncode: int,
        *,
        detail: str | None = None,
        ignore_if_not_running: bool = False,
    ) -> SessionSnapshot:
        """Handle an unexpected viewer exit while a session is running.

        The runtime's active-session process waiter calls this after the child
        exits. The manager stops/releases every remaining session resource and
        enters FAILED even when cleanup succeeds, so the UI never silently
        returns to a ready-looking state or leaves a stale last frame implied.
        An explicit Stop acknowledges the failure and returns to DORMANT.
        """
        with self._lock:
            if self._state is not SessionState.RUNNING:
                if ignore_if_not_running:
                    # A Stop may already have intentionally ended the viewer.
                    # Its internal waiter can wake afterwards; that completion is stale.
                    return self.snapshot()
                raise CaptureSessionError(
                    SessionStage.CLEANUP,
                    f"viewer exit reported while the session is {self._state.value}",
                )
            self._state = SessionState.FAILED
            self._stage = SessionStage.CLEANUP
            self._error = detail or f"Share Output viewer exited unexpectedly (code {returncode})"
            self._revision += 1
            failures = self._cleanup_resources()
            self._cleanup_failures = tuple(failures)
            self._revision += 1
            snapshot = self.snapshot()
            watcher = self._viewer_watcher
        if watcher is not None and watcher is not current_thread():
            watcher.join(timeout=1.0)
        return snapshot

    def stop(self) -> SessionSnapshot:
        """Stop or retry cleanup; idempotent after a complete stop."""
        error: CaptureSessionError | None = None
        with self._lock:
            if self._state is SessionState.DORMANT:
                snapshot = self.snapshot()
                watcher = self._viewer_watcher
            else:
                self._state = SessionState.STOPPING
                self._stage = SessionStage.CLEANUP
                self._revision += 1
                failures = self._cleanup_resources()
                self._cleanup_failures = tuple(failures)
                if failures:
                    self._state = SessionState.FAILED
                    self._error = "Some capture resources could not be cleaned up"
                    self._revision += 1
                    error = CaptureSessionError(
                        SessionStage.CLEANUP,
                        self._error,
                        cleanup_failures=self._cleanup_failures,
                    )
                else:
                    self._state = SessionState.DORMANT
                    self._stage = None
                    self._error = None
                    self._plan = None
                    self._revision += 1
                snapshot = self.snapshot()
                watcher = self._viewer_watcher
        # The watcher may have been waiting to acquire _lock after the child
        # was stopped. Join only after releasing the manager lock.
        if watcher is not None and watcher is not current_thread():
            watcher.join(timeout=1.0)
            if not watcher.is_alive():
                with self._lock:
                    if self._viewer_watcher is watcher:
                        self._viewer_watcher = None
        if error is not None:
            raise error
        return snapshot

    def _cleanup_resources(self) -> list[str]:
        """Attempt every cleanup stage in required order; retain failed leases."""
        failures: list[str] = []
        if self._viewer is not None:
            try:
                self._viewer.stop()
            except Exception as exc:
                failures.append(f"viewer: {exc}")
            else:
                self._viewer = None
        if self._inhibitor is not None:
            try:
                self._inhibitor.release()
            except Exception as exc:
                failures.append(f"idle inhibitor: {exc}")
            else:
                self._inhibitor = None
        if self._routes is not None:
            try:
                self._routes.remove()
            except Exception as exc:
                failures.append(f"share routes: {exc}")
            else:
                self._routes = None
        return failures
