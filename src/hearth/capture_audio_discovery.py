"""Adapter boundary for discovering capture-card audio without coupling UI to PipeWire."""

from __future__ import annotations

from typing import Protocol

from hearth.capture_devices import (
    AudioAssociation,
    AudioAssociationStatus,
    associate_audio_identity,
)
from hearth.capture_pipewire import PipeWireAudioInventory, PipeWireAudioProvider
from hearth.capture_session import UsbIdentity


class CaptureAudioBackend(Protocol):
    """One-shot source inventory provider; implementations must not mutate routes."""

    def discover(self) -> PipeWireAudioInventory: ...


class PipeWireCaptureAudioBackend:
    """Production adapter around the read-only pw-dump audio provider."""

    def __init__(self, provider: PipeWireAudioProvider | None = None) -> None:
        self._provider = provider or PipeWireAudioProvider()

    def discover(self) -> PipeWireAudioInventory:
        return self._provider.discover()


def associate_capture_audio(
    video_identity: UsbIdentity, backend: CaptureAudioBackend
) -> AudioAssociation:
    """Match by stable USB identity only; partial inventories remain UNKNOWN."""
    try:
        inventory = backend.discover()
    except Exception:
        return AudioAssociation(AudioAssociationStatus.UNKNOWN)
    return associate_audio_identity(
        video_identity, inventory.sources if inventory.complete else None
    )
