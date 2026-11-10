"""VU metering: one long-lived ``parec`` per monitor source.

Why a persistent child rather than sampling: ``pactl``'s peak sampling opens
a new PulseAudio stream per call, and PipeWire's compat layer needs more
than 40 ms to set up the link, so the sample always timed out and
WirePlumber logged link failures hundreds of times a second. One long-lived
``parec`` per source produces zero stream churn.

The frame maths is pure and tested; the process lifecycle is thin and uses
:mod:`hearth.proc`, so nothing here spawns a shell.
"""

from __future__ import annotations

import logging
import math
import os
import select
import struct
import subprocess
import threading
import time
from collections.abc import Sequence
from typing import IO, Any

from hearth import proc
from hearth.errors import CommandError

log = logging.getLogger(__name__)

RATE = 8000
CHANNELS = 1
SAMPLE_BYTES = 2  # s16le
READ_CHUNK = 4096
#: Wait before restarting a source whose parec died, so a missing device does
#: not become a process-spawn loop.
RESTART_COOLDOWN = 2.0
SILENCE_DBFS = -96.0


def rms_peak(frames: bytes) -> tuple[float, float]:
    """RMS and peak of signed 16-bit little-endian mono frames, 0.0-1.0.

    Odd trailing bytes are ignored rather than raising: a read can land in
    the middle of a frame, and the caller carries the remainder forward.
    """
    usable = len(frames) - (len(frames) % SAMPLE_BYTES)
    if usable <= 0:
        return 0.0, 0.0
    samples = struct.unpack(f"<{usable // SAMPLE_BYTES}h", frames[:usable])
    total = 0.0
    peak = 0
    for sample in samples:
        total += float(sample) * float(sample)
        magnitude = -sample if sample < 0 else sample
        if magnitude > peak:
            peak = magnitude
    return math.sqrt(total / len(samples)) / 32768.0, peak / 32768.0


def dbfs(level: float) -> float:
    """Linear 0.0-1.0 level as dBFS, floored at :data:`SILENCE_DBFS`."""
    if level <= 1e-7:
        return SILENCE_DBFS
    return max(SILENCE_DBFS, 20.0 * math.log10(min(level, 1.0)))


def parec_argv(source: str, *, latency_msec: int = 33) -> list[str]:
    """Argv for one monitor stream.

    The three ``--property`` flags are what keep these streams out of the
    Plasma volume applet's "Recording" list; without them the mixer appears
    to be recording the desktop.
    """
    return [
        "parec",
        f"--device={source}",
        f"--channels={CHANNELS}",
        f"--rate={RATE}",
        "--format=s16le",
        "--raw",
        f"--latency-msec={int(latency_msec)}",
        "--property=media.category=Monitor",
        "--property=stream.dont-record=1",
        "--property=application.id=io.github.roaring1.Hearth.vu",
    ]


class PeakPoller(threading.Thread):
    """Keeps one ``parec`` per source alive and exposes the latest levels.

    :meth:`stop` is not optional. This is a daemon thread, so its own cleanup
    does not reliably run at interpreter exit -- that is why older versions
    had to ``pkill parec`` on every startup.
    """

    def __init__(self, sources: Sequence[str] = ()) -> None:
        super().__init__(daemon=True, name="hearth-meters")
        self._sources: list[str] = list(sources)
        self._levels: dict[str, float] = {}
        self._procs: dict[str, subprocess.Popen[Any]] = {}
        self._dead_at: dict[str, float] = {}
        self._leftover: dict[str, bytes] = {}
        self._lock = threading.Lock()
        # Not ``_stop``: ``threading.Thread._stop`` is a real method, and
        # shadowing it with an Event makes ``join()`` raise TypeError on
        # CPython 3.11/3.12. CI caught this; 3.13+ happens not to call it.
        self._stopping = threading.Event()
        self.available = True if os.name == "nt" else proc.have("parec")

    def set_sources(self, sources: Sequence[str]) -> None:
        with self._lock:
            self._sources = list(sources)

    def level(self, source: str) -> float:
        with self._lock:
            return self._levels.get(source, 0.0)

    def levels(self) -> dict[str, float]:
        with self._lock:
            return dict(self._levels)

    def stop(self, *, timeout: float = 1.0) -> None:
        """Ask the worker to exit; its finally block owns child cleanup."""
        self._stopping.set()
        if self.ident is None:
            # Useful for a poller whose stream was prepared before start().
            self._cleanup_children()
            return
        if self.is_alive():
            self.join(timeout=timeout)
        if self.is_alive():
            # Do not concurrently mutate _procs while run() may still be
            # using it. The worker's finally block will reap children on exit.
            log.warning("meter worker did not stop within %.1fs", timeout)

    def _discard_source(self, source: str, *, dead_at: float | None = None) -> None:
        """Drop one stream and its cached meter state."""
        child = self._procs.pop(source, None)
        self._leftover.pop(source, None)
        if dead_at is None:
            self._dead_at.pop(source, None)
        else:
            self._dead_at[source] = dead_at
        with self._lock:
            self._levels.pop(source, None)

        if child is None:
            return
        try:
            proc.terminate(child, timeout=0.2)
        except Exception as exc:  # cleanup must continue for the other streams
            log.warning("could not stop meter stream %s: %s", source, exc)
        finally:
            if child.stdout is not None:
                try:
                    child.stdout.close()
                except OSError as exc:
                    log.debug("could not close meter pipe %s: %s", source, exc)

    def _cleanup_children(self) -> None:
        """Reap every stream on the worker thread that owns the process map."""
        for source in list(self._procs):
            self._discard_source(source)

    def _ensure(self, source: str, now: float) -> None:
        child = self._procs.get(source)
        if child is not None and child.poll() is None:
            return
        if child is not None:
            self._discard_source(source, dead_at=now)
        if now - self._dead_at.get(source, 0.0) < RESTART_COOLDOWN:
            return
        try:
            self._procs[source] = proc.stream(parec_argv(source))
        except CommandError as exc:
            self._dead_at[source] = now
            log.warning("cannot meter %s: %s", source, exc)

    def run(self) -> None:
        if os.name == "nt":
            from hearth import windows_audio

            try:
                while not self._stopping.is_set():
                    with self._lock:
                        sources = list(self._sources)
                    levels = windows_audio.peaks(sources)
                    with self._lock:
                        self._levels = levels
                    self._stopping.wait(0.05)
            except Exception as exc:
                log.warning("Windows Core Audio meter worker failed: %s", exc)
            return

        try:
            if not self.available:
                log.info("parec is not installed; VU meters are disabled")
                return
            while not self._stopping.is_set():
                now = time.monotonic()
                with self._lock:
                    sources = list(self._sources)
                for source in sources:
                    self._ensure(source, now)
                for source in [s for s in self._procs if s not in sources]:
                    self._discard_source(source)

                # Build the map from the pipe object itself, not from the child,
                # so the None case is eliminated once here rather than being
                # re-proved (or ignored) at every select() and read().
                pipes: dict[IO[bytes], str] = {}
                for source, child in self._procs.items():
                    pipe = child.stdout
                    if pipe is not None:
                        pipes[pipe] = source
                if not pipes:
                    self._stopping.wait(0.1)
                    continue
                try:
                    readable, _, _ = select.select(list(pipes), [], [], 0.1)
                except (OSError, ValueError) as exc:
                    log.warning("meter pipe selection failed: %s", exc)
                    for source in set(pipes.values()):
                        self._discard_source(source, dead_at=time.monotonic())
                    self._stopping.wait(0.1)
                    continue

                for pipe in readable:
                    source = pipes[pipe]
                    try:
                        data = pipe.read(READ_CHUNK) or b""
                    except (OSError, ValueError) as exc:
                        log.warning("meter read failed for %s: %s", source, exc)
                        self._discard_source(source, dead_at=time.monotonic())
                        continue
                    if not data:
                        # EOF is permanently readable. Leaving the child in
                        # the map would spin this thread at 100% CPU until
                        # poll() notices the exit (and can leak the pipe).
                        self._discard_source(source, dead_at=time.monotonic())
                        continue
                    buffered = self._leftover.pop(source, b"") + data
                    usable = len(buffered) - (len(buffered) % SAMPLE_BYTES)
                    self._leftover[source] = buffered[usable:]
                    rms, _peak = rms_peak(buffered[:usable])
                    with self._lock:
                        self._levels[source] = rms
        finally:
            self._cleanup_children()
