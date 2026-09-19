"""The background thread that keeps :mod:`hearth.status` fresh for a window.

This is the seam the Phase 4 simple view plugs into. It exists as its own
module, and its own thread, for one measured reason: the two things the
window needs run at incompatible speeds.

==============  ==========  =========================================
what            cost        cadence
==============  ==========  =========================================
mixer snapshot  ~0.02s      0.25-0.5s while visible -- a fader must
                            follow the hardware, not lag behind it
whole-rig       ~0.3s cold  seconds -- a service does not stop and
report          ~0.1s warm  start between two blinks
==============  ==========  =========================================

Putting the report on the collector's tick would multiply the main loop's
cost by more than ten for information that cannot change that fast. Running
it on its own slower thread costs one thread and keeps both honest.

Three rules, each learned somewhere earlier in this project:

* **A failed gather never kills the thread.** Same as the collector: the
  next tick may well succeed, and a dead reporter means a window that
  quietly stops telling the truth -- worse than one that says so.
* **Unchanged reports are not delivered.** "Everything is working" arriving
  every three seconds is a repaint, a flicker and, on a health surface, a
  reason to stop reading it. The UI is told only when the answer changes.
* **The callback runs on this thread.** A GTK consumer marshals with
  ``GLib.idle_add`` itself, which is what keeps this module free of ``gi``.

After the user does something -- unmuting a bus, restarting a service,
starting a screen share -- call :meth:`Reporter.refresh_now`, which also
drops the cached share answer so the change is visible immediately rather
than up to :data:`hearth.status.SHARE_TTL` seconds later.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from hearth import status

log = logging.getLogger(__name__)

#: Cadence while the window is on screen. Fast enough that a restarted
#: service is reflected before the user reaches for the mouse again.
VISIBLE_INTERVAL = 3.0

#: Cadence while the window is hidden. The tray icon and notifications are
#: the only consumers then, and neither is watched second by second.
HIDDEN_INTERVAL = 30.0


def digest(report: status.Report) -> str:
    """The report reduced to what a person would actually see.

    Two reports with the same text are the same report as far as a window is
    concerned, even if some timestamp underneath them moved.
    """
    return "\n".join(report.lines())


class Reporter(threading.Thread):
    """Poll :func:`hearth.status.gather` and announce only real changes.

    The callback receives the whole :class:`~hearth.status.Report`, so a
    consumer can render the headline, the issues, or any one area from it.
    """

    def __init__(
        self,
        on_report: Callable[[status.Report], None],
        *,
        visible_interval: float = VISIBLE_INTERVAL,
        hidden_interval: float = HIDDEN_INTERVAL,
    ) -> None:
        super().__init__(daemon=True, name="hearth-reporter")
        self._on_report = on_report
        self._visible_interval = visible_interval
        self._hidden_interval = hidden_interval
        self._visible = threading.Event()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._fresh_share = threading.Event()
        self._lock = threading.Lock()
        self._last: status.Report | None = None
        self._last_digest: str | None = None

    @property
    def last(self) -> status.Report | None:
        """The most recent report, for a consumer that starts mid-flight.

        A window built after the thread started must be able to paint
        immediately instead of showing an empty panel until the next tick.
        """
        with self._lock:
            return self._last

    def set_window_visible(self, visible: bool) -> None:
        """Switch cadence, and refresh at once when the window appears."""
        if visible:
            self._visible.set()
            self._wake.set()
        else:
            self._visible.clear()

    def refresh_now(self, *, fresh_share: bool = True) -> None:
        """Re-read now, bypassing the cached screen-share answer by default.

        Call this straight after an action that could change the answer.
        """
        if fresh_share:
            self._fresh_share.set()
        self._wake.set()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        if self.is_alive():
            self.join(timeout=timeout)

    def _delay(self) -> float:
        return self._visible_interval if self._visible.is_set() else self._hidden_interval

    def poll_once(self) -> status.Report | None:
        """Gather one report; return it only when it differs from the last.

        Returns ``None`` on an unchanged report and on a failed gather, so
        the caller cannot accidentally treat a failure as "nothing changed
        and all is well".
        """
        max_age = 0.0 if self._fresh_share.is_set() else status.SHARE_TTL
        self._fresh_share.clear()
        try:
            report = status.gather(max_share_age=max_age)
        except Exception as exc:
            # Mirrors the collector: one bad tick must not end the thread.
            log.warning("reporter tick failed: %s", exc)
            return None
        current = digest(report)
        with self._lock:
            changed = current != self._last_digest
            self._last = report
            self._last_digest = current
        return report if changed else None

    def run(self) -> None:
        while not self._stop.is_set():
            report = self.poll_once()
            if report is not None and not self._stop.is_set():
                try:
                    self._on_report(report)
                except Exception as exc:
                    # A broken consumer is the consumer's problem; the thread
                    # that feeds every other panel keeps running.
                    log.warning("reporter callback failed: %s", exc)
            self._wake.wait(timeout=self._delay())
            self._wake.clear()
