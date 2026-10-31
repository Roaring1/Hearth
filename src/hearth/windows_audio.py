"""Windows Core Audio backend used by the existing Hearth mixer contracts.

This module deliberately uses endpoint/session control interfaces only.
It does not open capture streams, change default devices, create virtual
routes, or start/stop Windows audio services.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hearth.audio.pactl import ShortEntry
    from hearth.collector import Snapshot


log = logging.getLogger(__name__)

# Hearth's logical strip IDs map to stable Windows endpoint friendly names.
# They are matched by exact name after Windows endpoint enumeration.
CHANNEL_ENDPOINTS: dict[str, str] = {
    "vm_game": "Voicemeeter Input (VB-Audio Voicemeeter VAIO)",
    "vm_chat": "Voicemeeter AUX Input (VB-Audio Voicemeeter VAIO)",
    "vm_music": "Voicemeeter VAIO3 Input (VB-Audio Voicemeeter VAIO)",
    "laptop_audio": "Speakers (Realtek(R) Audio)",
    "mic_b1": "Voicemeeter Out B1 (VB-Audio Voicemeeter VAIO)",
    "mic_b2": "Voicemeeter Out B2 (VB-Audio Voicemeeter VAIO)",
}
RENDER_CHANNELS = ("vm_game", "vm_chat", "vm_music", "laptop_audio")
CAPTURE_CHANNELS = ("mic_b1", "mic_b2")


@contextmanager
def _com_apartment() -> Iterator[None]:
    """Initialize COM for this calling thread, respecting an existing apartment."""
    import comtypes  # type: ignore[import-untyped]

    initialized = False
    try:
        comtypes.CoInitialize()
        initialized = True
    except OSError as exc:
        # RPC_E_CHANGED_MODE means this thread is already initialized using
        # another apartment model. COM calls remain legal in that apartment.
        hresult = getattr(exc, "hresult", None)
        if hresult not in (-2147417850, 0x80010106):
            raise
    try:
        yield
    finally:
        if initialized:
            comtypes.CoUninitialize()


def _pycaw() -> tuple[Any, Any, Any, Any]:
    from pycaw.pycaw import (  # type: ignore[import-untyped]
        IAudioMeterInformation,
        IAudioSessionControl2,
    )
    from pycaw.utils import AudioSession, AudioUtilities  # type: ignore[import-untyped]

    return AudioUtilities, AudioSession, IAudioSessionControl2, IAudioMeterInformation


def _is_active(device: object) -> bool:
    state = str(getattr(device, "state", ""))
    return state.endswith("Active") or state == "1"


def _all_devices() -> list[Any]:
    AudioUtilities, _AudioSession, _SessionControl, _Meter = _pycaw()
    return list(AudioUtilities.GetAllDevices())


def _logical_devices(devices: list[Any] | None = None) -> dict[str, Any]:
    AudioUtilities, _AudioSession, _SessionControl, _Meter = _pycaw()
    found = devices if devices is not None else list(AudioUtilities.GetAllDevices())
    by_name = {
        str(getattr(device, "FriendlyName", "")).strip().casefold(): device
        for device in found
        if _is_active(device)
    }
    logical = {
        key: device
        for key, friendly_name in CHANNEL_ENDPOINTS.items()
        if (device := by_name.get(friendly_name.casefold())) is not None
    }

    # Hearth's "Laptop" strip follows the active Windows default render
    # endpoint unless that endpoint is already one of the dedicated Voicemeeter
    # strips. This handles Steam/RDP audio endpoints as well as Realtek speakers.
    try:
        default_device = AudioUtilities.GetSpeakers()
        default_id = str(getattr(default_device, "id", ""))
        mapped_ids = {str(getattr(device, "id", "")) for device in logical.values()}
        if _is_active(default_device) and default_id not in mapped_ids:
            logical["laptop_audio"] = default_device
    except Exception as exc:
        log.debug("could not map the default playback endpoint to Laptop: %s", exc)
    return logical


def _find_endpoint(name: str, devices: list[Any] | None = None) -> Any | None:
    all_devices = devices if devices is not None else _all_devices()
    logical = _logical_devices(all_devices)
    if name in logical:
        return logical[name]

    wanted = name.casefold()
    for device in all_devices:
        if not _is_active(device):
            continue
        if str(getattr(device, "id", "")).casefold() == wanted:
            return device
        if str(getattr(device, "FriendlyName", "")).strip().casefold() == wanted:
            return device
    # Accept a logical monitor source ("vm_game.monitor") for meter helpers.
    if wanted.endswith(".monitor"):
        return logical.get(wanted[:-8])
    return None


def _endpoint_state(device: Any) -> tuple[int, bool]:
    endpoint = device.EndpointVolume
    volume = max(0, min(100, round(float(endpoint.GetMasterVolumeLevelScalar()) * 100)))
    muted = bool(endpoint.GetMute())
    return volume, muted


def _iter_sessions(device: Any) -> Iterator[Any]:
    """Yield Pycaw AudioSession wrappers for one endpoint."""
    _AudioUtilities, AudioSession, SessionControl, _Meter = _pycaw()
    manager = device.AudioSessionManager
    enumerator = manager.GetSessionEnumerator()
    for index in range(int(enumerator.GetCount())):
        control = enumerator.GetSession(index)
        if control is None:
            continue
        try:
            control2 = control.QueryInterface(SessionControl)
            session = AudioSession(control2)
            # State 2 is expired; active (0) and inactive (1) sessions remain
            # meaningful controls for Windows' per-application mixer.
            if int(session.State) == 2:
                continue
            yield session
        except Exception as exc:
            log.debug("could not inspect an audio session on %s: %s", device, exc)


def _session_label(session: Any) -> str:
    process = None
    with suppress(Exception):
        process = session.Process
    if process is not None:
        with suppress(Exception):
            name = str(process.name())
            if name:
                return name.removesuffix(".exe")
    with suppress(Exception):
        label = str(session.DisplayName or "").strip()
        if label:
            return label
    return f"Process {getattr(session, 'ProcessId', 0)}"


def _session_key(device: Any, session: Any) -> str:
    return f"{getattr(device, 'id', '')}|{session.InstanceIdentifier}"


def collect(_units: Sequence[str] = ()) -> Snapshot:
    """Build one Snapshot from Core Audio endpoints and their app sessions."""
    from hearth.collector import SinkState, Snapshot, StreamState

    AudioUtilities, _AudioSession, _SessionControl, _Meter = _pycaw()
    with _com_apartment():
        devices = list(AudioUtilities.GetAllDevices())
        logical_devices = _logical_devices(devices)
        sinks: dict[str, SinkState] = {}
        streams: dict[str, list[StreamState]] = {}
        sources: dict[str, str] = {}

        for device in devices:
            if not _is_active(device):
                continue
            device_id = str(getattr(device, "id", ""))
            friendly = str(getattr(device, "FriendlyName", "")).strip()
            if device_id.startswith("{0.0.1."):
                sources[device_id] = friendly

        for logical, device in logical_devices.items():
            try:
                volume, muted = _endpoint_state(device)
            except Exception as exc:
                log.warning("could not read Windows endpoint %s: %s", logical, exc)
                continue

            sinks[logical] = SinkState(
                name=logical,
                index=str(getattr(device, "id", "")),
                volume=volume,
                muted=muted,
                state="RUNNING",
            )
            entries: list[StreamState] = []
            try:
                sessions = list(_iter_sessions(device))
            except Exception as exc:
                log.debug("could not enumerate sessions for %s: %s", logical, exc)
                sessions = []

            for session in sessions:
                try:
                    # PID 0 is Windows' System Sounds pseudo-session, not an
                    # application stream that should appear in an app drawer.
                    if int(session.ProcessId) == 0:
                        continue
                    simple = session.SimpleAudioVolume
                    app_volume = max(
                        0,
                        min(100, round(float(simple.GetMasterVolume()) * 100)),
                    )
                    app_muted = bool(simple.GetMute())
                    entries.append(
                        StreamState(
                            index=_session_key(device, session),
                            name=_session_label(session),
                            sink=logical,
                            icon=str(getattr(session, "IconPath", "") or ""),
                            volume=app_volume,
                            muted=app_muted,
                        )
                    )
                except Exception as exc:
                    log.debug("could not read an app session on %s: %s", logical, exc)
            streams[logical] = entries

        default_sink = ""
        try:
            default_device = AudioUtilities.GetSpeakers()
            default_id = str(getattr(default_device, "id", ""))
            for logical, device in logical_devices.items():
                if str(getattr(device, "id", "")) == default_id:
                    default_sink = logical
                    break
            if not default_sink:
                default_sink = str(getattr(default_device, "FriendlyName", "") or "")
        except Exception as exc:
            log.debug("could not read the Windows default render endpoint: %s", exc)

        return Snapshot(
            sinks=sinks,
            streams=streams,
            units={},
            sources=sources,
            server_version="Windows Core Audio",
            default_sink=default_sink,
            taken_at=time.time(),
            # Windows does not expose the PipeWire module-loopback graph.
            # None intentionally means unknown, not "all routes disconnected".
            routes=None,
        )


def get_endpoint_state(name: str) -> tuple[int, bool] | None:
    with _com_apartment():
        device = _find_endpoint(name)
        if device is None:
            return None
        return _endpoint_state(device)


def endpoint_volume(name: str) -> int:
    state = get_endpoint_state(name)
    return state[0] if state is not None else 0


def endpoint_muted(name: str) -> bool:
    state = get_endpoint_state(name)
    return state[1] if state is not None else False


def set_endpoint_volume(name: str, percent: int) -> bool:
    try:
        with _com_apartment():
            device = _find_endpoint(name)
            if device is None:
                return False
            value = max(0, min(100, int(percent))) / 100.0
            device.EndpointVolume.SetMasterVolumeLevelScalar(value, None)
            return True
    except Exception as exc:
        log.warning("could not set Windows endpoint volume for %s: %s", name, exc)
        return False


def set_endpoint_mute(name: str, muted: bool) -> bool:
    try:
        with _com_apartment():
            device = _find_endpoint(name)
            if device is None:
                return False
            device.EndpointVolume.SetMute(1 if muted else 0, None)
            return True
    except Exception as exc:
        log.warning("could not set Windows endpoint mute for %s: %s", name, exc)
        return False


def _find_session(session_key: str) -> Any | None:
    device_id, separator, instance_id = str(session_key).partition("|")
    if not separator or not device_id or not instance_id:
        return None
    for device in _all_devices():
        if str(getattr(device, "id", "")) != device_id:
            continue
        for session in _iter_sessions(device):
            try:
                if session.InstanceIdentifier == instance_id:
                    return session
            except Exception as exc:
                log.debug("could not inspect Windows audio-session identity: %s", exc)
                continue
    return None


def set_session_volume(session_key: str, percent: int) -> bool:
    try:
        with _com_apartment():
            session = _find_session(session_key)
            if session is None:
                return False
            # ISimpleAudioVolume uses a 0.0..1.0 scalar, unlike PipeWire's
            # optional >100% software gain. Never pass an out-of-range value.
            value = max(0, min(100, int(percent))) / 100.0
            session.SimpleAudioVolume.SetMasterVolume(value, None)
            return True
    except Exception as exc:
        log.warning("could not set Windows app-session volume: %s", exc)
        return False


def set_session_mute(session_key: str, muted: bool) -> bool:
    try:
        with _com_apartment():
            session = _find_session(session_key)
            if session is None:
                return False
            session.SimpleAudioVolume.SetMute(1 if muted else 0, None)
            return True
    except Exception as exc:
        log.warning("could not set Windows app-session mute: %s", exc)
        return False


def peaks(sources: list[str] | tuple[str, ...]) -> dict[str, float]:
    """Read endpoint peak meters without opening capture streams."""
    _AudioUtilities, _AudioSession, _SessionControl, Meter = _pycaw()
    result: dict[str, float] = {}
    with _com_apartment():
        devices = _all_devices()
        logical_devices = _logical_devices(devices)
        by_name = {
            str(getattr(device, "FriendlyName", "")).strip().casefold(): device
            for device in devices
            if _is_active(device)
        }
        by_id = {
            str(getattr(device, "id", "")).casefold(): device
            for device in devices
            if _is_active(device)
        }
        for source in sources:
            logical = source[:-8] if source.casefold().endswith(".monitor") else source
            device = logical_devices.get(logical)
            if device is None:
                device = by_name.get(str(source).casefold()) or by_id.get(str(source).casefold())
            if device is None:
                result[source] = 0.0
                continue
            try:
                meter = device.EndpointVolume.QueryInterface(Meter)
                peak = max(0.0, min(1.0, float(meter.GetPeakValue())))
                result[source] = 0.0 if peak < 1e-7 else peak
            except Exception as exc:
                log.debug("could not read Windows peak meter for %s: %s", source, exc)
                result[source] = 0.0
    return result


def short_entries(*, capture: bool = False) -> list[ShortEntry]:
    """Pactl-compatible inventory rows for the current logical bus mapping."""
    from hearth.audio.pactl import ShortEntry

    keys = CAPTURE_CHANNELS if capture else RENDER_CHANNELS
    entries: list[ShortEntry] = []
    with _com_apartment():
        devices = _logical_devices(_all_devices())
        for index, logical in enumerate(keys, start=1):
            device = devices.get(logical)
            if device is None:
                continue
            entries.append(
                ShortEntry(
                    index=index,
                    name=logical,
                    driver="Windows Core Audio",
                    spec=str(getattr(device, "FriendlyName", "")),
                    state="RUNNING",
                )
            )
    return entries


def default_sink() -> str:
    AudioUtilities, _AudioSession, _SessionControl, _Meter = _pycaw()
    with _com_apartment():
        default_device = AudioUtilities.GetSpeakers()
        default_id = str(getattr(default_device, "id", ""))
        for logical, device in _logical_devices().items():
            if str(getattr(device, "id", "")) == default_id:
                return logical
        return str(getattr(default_device, "FriendlyName", "") or "")


def routing_supported() -> bool:
    return False
