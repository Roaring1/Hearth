# Hearth Windows routing architecture

**Research date:** 2026-10-30
**Status:** Research checkpoint; routing remains fail-closed pending a product-mode decision.

## Executive conclusion

The current Windows backend is an endpoint/session mixer foundation. It cannot be extended into an application-routing engine by adding another Core Audio setter.

Microsoft documents that a stream is assigned to an audio session when initialized, that session belongs to one endpoint, and an existing stream cannot be reassigned after creation. The owning application must release/recreate its stream against a new endpoint. The Core Audio session controls used here expose volume and mute, not an operation to transplant another process's live stream.

Therefore the existing fail-closed behavior is correct: routing_supported() is false, Snapshot.routes is None, and unsupported move controls stay disabled. Do not simulate a move by changing the system default, changing volume, or relying on an undocumented COM interface.

The realistic directions are:

1. **Standalone Windows mixer:** control a real virtual routing engine. Since VB-Audio endpoints already exist on this machine, Voicemeeter plus its official Remote API is the leading candidate *if its full engine is installed and accepted*.
2. **Windows companion:** retain the Linux Hearth host as the routing/processing engine; Windows becomes a control client. This best preserves the existing PipeWire/Carla graph.
3. **Native Windows-only limited mode:** retain local Core Audio controls and leave per-app output preferences to the Windows Volume Mixer. This is safe but is not a full Hearth replacement.

A custom Windows virtual audio driver is possible but is a much larger driver-development and deployment project, not a safe incremental change to the daily-use laptop.

## 1. Documented Core Audio boundary

The current Pycaw/Core Audio provider is appropriate for:
- IAudioEndpointVolume endpoint level and mute;
- ISimpleAudioVolume audio-session level and mute;
- IAudioMeterInformation endpoint peak reads;
- endpoint and audio-session inventory.

It does not create buses, connect endpoints, move an arbitrary process's stream, or expose a PipeWire-style routing graph.

Windows 10+ supports user-selected per-app preferred output devices through Volume Mixer / App volume and device preferences. This is Windows-owned policy, not a general-purpose Hearth routing graph. Keep it manual unless a documented management API is established for the exact supported Windows versions.

**Implementation rule:** do not use IPolicyConfig, IAudioPolicyConfigFactory, registry edits, or copied undocumented COM vtables as the basis for supported routing.

References:
- [Microsoft — Audio Sessions](https://learn.microsoft.com/en-us/windows/win32/coreaudio/audio-sessions)
- [Microsoft — Stream Routing](https://learn.microsoft.com/en-us/windows/win32/coreaudio/stream-routing)
- [Microsoft — Default Audio Endpoint Selection](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/default-audio-endpoint-selection)
- [Microsoft — Stream Routing Implementation Considerations](https://learn.microsoft.com/en-us/windows/win32/coreaudio/stream-routing-implementation-considerations)

## 2. Candidate A — Voicemeeter Remote API (preferred standalone investigation)

VB-Audio's official Remote API supports audio-device aggregation, application interconnection via virtual audio devices/virtual ASIO, Voicemeeter processing control, and access to internal streams. Its SDK includes examples such as an 8x8 gain matrix and a custom GUI. In this design, apps render into Voicemeeter virtual endpoints and the Voicemeeter engine owns the routing graph; Hearth talks to the vendor API.

References:
- [VB-Audio support — Voicemeeter Remote API](https://vb-audio.com/Services/support.htm)
- [VB-Audio — Voicemeeter SDK and examples](https://github.com/vburel2018/Voicemeeter-SDK)
- [VB-Audio — Remote API documentation](https://download.vb-audio.com/Download_CABLE/VoicemeeterRemoteAPI.pdf)

### Local prerequisite remains unverified

The current Windows inventory found VB-Audio Cable and Voicemeeter VAIO driver/control-panel components. It did **not** establish that the full Voicemeeter mixer/engine or VoicemeeterRemote.dll is installed, running, configured, or usable. Endpoint names alone are not proof of an active mixer engine.

Before writing an adapter, perform read-only discovery:
1. Check documented install locations for the app, engine process, and architecture-matched Remote API DLL.
2. Read version/type information only through documented calls that do not change parameters or device state.
3. Enumerate real virtual inputs/outputs and compare them with Hearth's six logical strips.
4. Confirm required input/output/bus/processing capacity.
5. Record what exists. Do not install, start, repair, update, or reconfigure Voicemeeter during discovery.

If accepted, prototype a narrow provider with read-only state and mocked parameter setters. Real parameter writes need separate approval and an isolated, reversible test plan.

## 3. Candidate B — custom Windows virtual audio device/engine

Microsoft's SYSVAD sample is a reference for WDM virtual audio-driver architecture, not a ready-made Hearth engine. Microsoft explicitly notes that the sample does not implement real audio mixing and simulates loopback with a tone.

Deployment requires Visual Studio, Windows SDK/WDK, driver packaging/signing, and a provisioned test target. Manual deployment guidance includes test-signing and a reboot. Those machine-level boot/security changes must not be performed silently.

A production Hearth version would also need a signed render/capture driver, a user-mode engine for mixing/buffering/clock drift/device loss, capture and loopback paths, service/configuration lifecycle, upgrade/uninstall, and compatibility/certification testing. Treat this as a separate long-term project.

References:
- [Microsoft — SYSVAD Virtual Audio Device Driver Sample](https://learn.microsoft.com/en-us/samples/microsoft/windows-driver-samples/sysvad-virtual-audio-device-driver-sample/)
- [Microsoft — Audio Driver Samples](https://learn.microsoft.com/en-us/windows-hardware/drivers/samples/audio-driver-samples)

## 4. Candidate C — Windows control client for Linux Hearth

The existing Linux host owns the PipeWire graph and Carla/plugin voice chain. A Windows companion could preserve those responsibilities, but it is a distinct architecture. It requires:
- versioned capability/health handshake;
- authenticated, encrypted transport to an explicitly trusted host;
- separate read-only inventory/health calls and mutating commands;
- per-command authorization;
- reconnect, stale-state, and host-disconnected handling;
- one authoritative route state (the Linux host).

The existing authenticated Windows named pipe is local process IPC, not a LAN protocol; do not expose it directly over the network. Do not represent local Windows endpoints as remote Linux buses.

## 5. Provider boundary

Keep responsibilities separate:

| Provider | Responsibility | Current state |
| --- | --- | --- |
| Core Audio inventory | Local endpoint/session inventory and meter reads | Implemented |
| Core Audio controls | Local endpoint/session volume and mute | Implemented; bounds tested |
| Routing provider | App-to-bus assignment, graph, loopback, sends | Unsupported / fail-closed |
| Processing provider | EQ, gate, compressor, plugin chain | Not implemented on Windows |
| Health provider | Audio service, selected engine/host, endpoint/worker health | Partial / not designed |

A future routing provider must report capabilities explicitly. Never infer routing from a friendly endpoint name such as vm_game or mic_b1. Missing engine, unsupported feature, unavailable bus, stale device identity, or disconnected host must return unavailable/unsupported with a reason—not an empty graph or successful no-op.

Use a fake provider in unit/hidden GTK tests, with synthetic buses/sessions and in-memory parameter writes. Keep the real provider out of those tests.

## 6. Recommended order

1. Finish and preserve the read-only Windows foundation.
2. Perform read-only Voicemeeter discovery.
3. Resolve standalone Windows engine versus Windows companion for Linux. Do not blend the two models.
4. Define directions, channel counts, ownership, and routing semantics for Game, Chat, Music, Laptop, Mic B1, and Mic B2. Current friendly-name mapping is inventory, not proof of a working bus topology.
5. Build a mocked routing-provider contract and UI capability model.
6. Only after explicit approval, test one synthetic source and a dedicated test output, with rollback. Never make a test endpoint the system default.
7. Then implement mic processing, share audio, engine health/recovery, and packaging.

## 7. Safety boundary retained

Until the architecture and test plan are approved:
- do not install/update audio drivers or Voicemeeter;
- do not start/reconfigure the Voicemeeter engine;
- do not change default endpoints or per-app output preferences;
- do not create/remove loopbacks or virtual devices;
- do not move real sessions or change actual endpoint/session volume/mute;
- do not open capture streams or run playback/capture tests;
- do not touch RoaringMic, Padfire, or the LPD8/MIDI device;
- do not present the Hearth GUI during hidden smoke tests.

This is a research checkpoint only and does not authorize any of those actions.

## 8. Read-only Voicemeeter installation evidence - 2026-10-30

The conventional 64-bit vendor directory inspected on the Windows laptop is `C:\Program Files\VB`. Its only top-level directories are `CABLE` and `VBVoicemeeterVAIOs`. A recursive filename search in that tree found:

- `VBVoicemeeterVAIO_ControlPanel.exe`
- `VBVoicemeeterVAIO_Setup_x64.exe`

It did not find a full Voicemeeter mixer executable or a `VoicemeeterRemote*.dll`. The conventional 32-bit vendor directory and common Voicemeeter engine/API paths checked were absent. This is a scoped read-only filesystem search, not an exhaustive scan of every disk, user directory, or registry location, so the careful conclusion is: **the full mixer engine and Remote API are not evidenced in the inspected installation; only the Cable/VAIO components are evidenced there**.

The hidden GTK task was also confirmed removed after the successful smoke run. No VAIO setup utility was launched, and no engine, driver, endpoint, service, or process was changed.
