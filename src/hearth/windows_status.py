"""Read-only health summary for Hearth's native Windows audio foundation.

This intentionally reports only facts available from Windows Core Audio and
the Service Control Manager. It is not a claim that virtual routing,
microphone processing, playback, or capture is working.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass

from hearth import windows_audio

log = logging.getLogger(__name__)

_SC_MANAGER_CONNECT = 0x0001
_SERVICE_QUERY_STATUS = 0x0004
_SC_STATUS_PROCESS_INFO = 0
_ERROR_SERVICE_DOES_NOT_EXIST = 1060

_SERVICE_STATES: dict[int, str] = {
    1: "stopped",
    2: "start pending",
    3: "stop pending",
    4: "running",
    5: "continue pending",
    6: "pause pending",
    7: "paused",
}


class _ServiceStatusProcess(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", wintypes.DWORD),
        ("dwCurrentState", wintypes.DWORD),
        ("dwControlsAccepted", wintypes.DWORD),
        ("dwWin32ExitCode", wintypes.DWORD),
        ("dwServiceSpecificExitCode", wintypes.DWORD),
        ("dwCheckPoint", wintypes.DWORD),
        ("dwWaitHint", wintypes.DWORD),
        ("dwProcessId", wintypes.DWORD),
        ("dwServiceFlags", wintypes.DWORD),
    ]


def _query_service_state(service_name: str) -> str:
    """Read a Windows service state using SCM; never start or change it."""
    advapi32 = ctypes.WinDLL("Advapi32", use_last_error=True)

    open_manager = advapi32.OpenSCManagerW
    open_manager.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    open_manager.restype = wintypes.HANDLE

    open_service = advapi32.OpenServiceW
    open_service.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
    open_service.restype = wintypes.HANDLE

    query_status = advapi32.QueryServiceStatusEx
    query_status.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.BYTE),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    query_status.restype = wintypes.BOOL

    close_service = advapi32.CloseServiceHandle
    close_service.argtypes = [wintypes.HANDLE]
    close_service.restype = wintypes.BOOL

    manager = open_manager(None, None, _SC_MANAGER_CONNECT)
    if not manager:
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error))

    service = None
    try:
        service = open_service(manager, service_name, _SERVICE_QUERY_STATUS)
        if not service:
            error = ctypes.get_last_error()
            if error == _ERROR_SERVICE_DOES_NOT_EXIST:
                return "not installed"
            raise OSError(error, ctypes.FormatError(error), service_name)

        status = _ServiceStatusProcess()
        needed = wintypes.DWORD()
        buffer = ctypes.cast(
            ctypes.byref(status),
            ctypes.POINTER(wintypes.BYTE),
        )
        if not query_status(
            service,
            _SC_STATUS_PROCESS_INFO,
            buffer,
            ctypes.sizeof(status),
            ctypes.byref(needed),
        ):
            error = ctypes.get_last_error()
            raise OSError(error, ctypes.FormatError(error), service_name)
        return _SERVICE_STATES.get(int(status.dwCurrentState), "unknown")
    finally:
        if service:
            close_service(service)
        close_service(manager)


@dataclass(frozen=True, slots=True)
class WindowsStatus:
    """A limited, explicit account of locally observable Windows audio state."""

    audio_service: str
    endpoint_builder: str
    default_playback: str | None
    render_channels: tuple[str, ...]
    missing_render_channels: tuple[str, ...]
    capture_endpoint_count: int | None
    application_session_count: int | None
    inventory_error: str | None = None

    @property
    def headline(self) -> str:
        """Summarize local playback availability without claiming full rig health."""
        for name, state in (
            ("Windows Audio", self.audio_service),
            ("Audio Endpoint Builder", self.endpoint_builder),
        ):
            if state == "stopped":
                return f"{name} service is stopped"
        if self.default_playback:
            if self.audio_service == "running" and self.endpoint_builder == "running":
                return f"Windows playback endpoint is available: {self.default_playback}"
            return "Playback endpoint found; Windows service health is incomplete"
        if self.inventory_error:
            return "Could not inspect Windows audio endpoints"
        return "No active default Windows playback endpoint was found"

    def lines(self) -> list[str]:
        """Render the report as plain text for the --status command."""
        output = [
            "Windows audio health",
            self.headline,
            "",
            f"Windows Audio service       {self.audio_service}",
            f"Audio Endpoint Builder      {self.endpoint_builder}",
            f"Default playback endpoint   {self.default_playback or 'unknown'}",
        ]
        if self.capture_endpoint_count is None:
            capture = "unknown"
        else:
            capture = str(self.capture_endpoint_count)
        if self.application_session_count is None:
            sessions = "unknown"
        else:
            sessions = str(self.application_session_count)
        output.extend(
            [
                f"Capture endpoint inventory  {capture} (signal not tested)",
                f"Application audio sessions  {sessions}",
                (
                    "Mapped Hearth playback     "
                    f"{len(self.render_channels)}/{len(windows_audio.RENDER_CHANNELS)} endpoints"
                ),
                "Virtual mixer engine        Not verified by endpoint inventory",
            ]
        )
        if self.missing_render_channels:
            output.append("Missing recognized outputs  " + ", ".join(self.missing_render_channels))
        if self.inventory_error:
            output.append(f"Endpoint inventory error   {self.inventory_error}")
        output.extend(
            [
                "",
                "Routing graph               Not available in the native Windows backend",
                "App-to-bus reassignment     Not implemented",
                "Playback/capture signal    Not tested; this report is read-only inventory",
                "Scope                       Local Windows audio only, not the complete Hearth rig",
            ]
        )
        return output


def gather() -> WindowsStatus:
    """Collect service state and Core Audio inventory without mutating devices."""
    service_states: dict[str, str] = {}
    for service_name in ("Audiosrv", "AudioEndpointBuilder"):
        try:
            service_states[service_name] = _query_service_state(service_name)
        except Exception as exc:
            log.warning("could not query Windows service %s: %s", service_name, exc)
            service_states[service_name] = "unknown"

    inventory_error: str | None = None
    try:
        snapshot = windows_audio.collect()
    except Exception as exc:
        log.warning("could not collect Windows Core Audio inventory: %s", exc)
        snapshot = None
        inventory_error = f"{type(exc).__name__}: {exc}"

    if snapshot is None:
        render_channels: tuple[str, ...] = ()
        missing_channels = tuple(windows_audio.RENDER_CHANNELS)
        default_playback = None
        capture_endpoint_count = None
        application_session_count = None
    else:
        render_channels = tuple(
            channel for channel in windows_audio.RENDER_CHANNELS if channel in snapshot.sinks
        )
        missing_channels = tuple(
            channel for channel in windows_audio.RENDER_CHANNELS if channel not in snapshot.sinks
        )
        default_name = snapshot.default_sink.strip()
        default_labels = {
            "vm_game": "Game (Voicemeeter Input)",
            "vm_chat": "Chat (Voicemeeter AUX Input)",
            "vm_music": "Music (Voicemeeter VAIO3 Input)",
            "laptop_audio": "Laptop (Windows default)",
        }
        default_playback = default_labels.get(default_name, default_name) or None
        capture_endpoint_count = len(snapshot.sources)
        application_session_count = sum(len(sessions) for sessions in snapshot.streams.values())

    return WindowsStatus(
        audio_service=service_states["Audiosrv"],
        endpoint_builder=service_states["AudioEndpointBuilder"],
        default_playback=default_playback,
        render_channels=render_channels,
        missing_render_channels=missing_channels,
        capture_endpoint_count=capture_endpoint_count,
        application_session_count=application_session_count,
        inventory_error=inventory_error,
    )


def text() -> str:
    """The Windows-local health report as one printable string."""
    return "\n".join(gather().lines())
