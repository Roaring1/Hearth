from pathlib import Path

from hearth.capture_audio_discovery import (
    CaptureAudioBackend,
    PipeWireCaptureAudioBackend,
    associate_capture_audio,
)
from hearth.capture_devices import AudioAssociationStatus
from hearth.capture_pipewire import (
    PipeWireAudioInventory,
    PipeWireAudioIssue,
    parse_pw_dump_audio_inventory,
)
from hearth.capture_session import UsbIdentity

FIXTURES = Path(__file__).parent / "fixtures"
VIDEO_IDENTITY = UsbIdentity("1abc", "02ef", serial_number="GUV302G-001")


class FakeBackend:
    def __init__(self, inventory=None, error=None):
        self.inventory = inventory or PipeWireAudioInventory()
        self.error = error
        self.calls = 0

    def discover(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.inventory


def test_recorded_pw_dump_fixture_parses_and_matches_stable_usb_identity():
    fixture = (FIXTURES / "pw_dump_capture_audio.json").read_text(encoding="utf-8")
    inventory = parse_pw_dump_audio_inventory(fixture)
    backend: CaptureAudioBackend = FakeBackend(inventory)

    result = associate_capture_audio(VIDEO_IDENTITY, backend)

    assert result.status is AudioAssociationStatus.MATCHED
    assert result.selected is not None
    assert result.selected.source_selector == "alsa_input.usb-IOGEAR_GUV302G.analog-stereo"


def test_incomplete_inventory_and_backend_errors_fail_closed():
    backend = FakeBackend(error=RuntimeError("probe unavailable"))
    assert associate_capture_audio(VIDEO_IDENTITY, backend).status is AudioAssociationStatus.UNKNOWN
    assert backend.calls == 1

    partial = PipeWireAudioInventory(issues=(PipeWireAudioIssue("source", "identity unknown"),))
    assert (
        associate_capture_audio(VIDEO_IDENTITY, FakeBackend(partial)).status
        is AudioAssociationStatus.UNKNOWN
    )


def test_production_adapter_delegates_to_injected_read_only_provider():
    class Provider:
        def __init__(self):
            self.calls = 0

        def discover(self):
            self.calls += 1
            return PipeWireAudioInventory()

    provider = Provider()
    adapter = PipeWireCaptureAudioBackend(provider)
    assert adapter.discover() == PipeWireAudioInventory()
    assert provider.calls == 1
