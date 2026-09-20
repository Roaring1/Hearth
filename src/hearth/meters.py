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
        self.available = proc.have("parec")

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
        """Exit the loop and reap every child, leaving no stray ``parec``."""
        self._stopping.set()
        if self.is_alive():
            self.join(timeout=timeout)
        for source, child in list(self._procs.items()):
            proc.terminate(child, timeout=0.2)
            self._procs.pop(source, None)

    def _ensure(self, source: str, now: float) -> None:
        child = self._procs.get(source)
        if child is not None and child.poll() is None:
            return
        if child is not None:
            self._procs.pop(source, None)
            self._dead_at[source] = now
        if now - self._dead_at.get(source, 0.0) < RESTART_COOLDOWN:
            return
        try:
            self._procs[source] = proc.stream(parec_argv(source))
        except CommandError as exc:
            self._dead_at[source] = now
            log.warning("cannot meter %s: %s", source, exc)

    def run(self) -> None:
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
                proc.terminate(self._procs.pop(source), timeout=0.2)

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
            readable, _, _ = select.select(list(pipes), [], [], 0.1)
            for pipe in readable:
                source = pipes[pipe]
                data = pipe.read(READ_CHUNK) or b""
                if not data:
                    continue
                buffered = self._leftover.pop(source, b"") + data
                usable = len(buffered) - (len(buffered) % SAMPLE_BYTES)
                self._leftover[source] = buffered[usable:]
                rms, _peak = rms_peak(buffered[:usable])
                with self._lock:
                    self._levels[source] = rms
