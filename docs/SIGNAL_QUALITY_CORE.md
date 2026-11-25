# Signal-quality core

The `hearth.audio.signal_quality` module is pure, bounded, and measurement-only. It does not capture audio, query PipeWire, persist samples, or perform recovery. Results are candidates/context observations, not confirmed faults.

## Dropout candidate detection

`detect_dropouts(samples, sample_rate, config=None)` partitions normalized mono PCM into complete fixed windows (default 20 ms). It calculates each window's RMS and flags contiguous runs at/below a configurable threshold only when the run reaches the configured minimum (default three windows / 60 ms). It returns window counts, longest low-amplitude run, and start sample indices. Partial trailing windows are ignored. Input/rate/configuration bounds fail closed as `INCONCLUSIVE`.

Quiet passages, gate/ducking, mute, or a low-level source can look like dropout; detection is amplitude-only and must not be used as a fault verdict or recovery trigger.

## Transport-context replay

`detect_transport_context(before, after)` consumes `TransportSnapshot` pairs. It reports `STABLE`, `CHANGED`, or `UNKNOWN`, lists comparable changes in node, role, sample-rate, quantum, and ERR-counter context, and keeps counter deltas unknown when identity changed or a counter reset occurred. Missing or invalid snapshots remain unknown.

Context changes can correlate with a signal issue but do not prove causality or continuity.

## Replay coverage

The deterministic tests cover a sustained 80 ms low-amplitude gap, a single short quiet window, invalid and oversized input, same-context stability, sample-rate change, ERR increments, node replacement, and missing snapshots.

The core remains synthetic and uncalibrated; it is not wired into the live watchdog or offline analyzer.
