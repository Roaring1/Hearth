"""Cairo VU bar and status LED, moved verbatim from the legacy module."""

import math
import time

__all__ = ["VU", "make_led"]

try:
    import cairo as _cairo

    _HAVE_CAIRO = True
except ImportError:
    _HAVE_CAIRO = False


class VU:
    # smooth Cairo VU bar, L+R correlated stereo, gradient fill, peak hold dot
    # hard idle decay -- drops to zero instantly when parec stops feeding
    def __init__(self, speed=0.35, w=28, h=120):
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        self.da = Gtk.DrawingArea()
        self.da.set_size_request(w, h)
        self._w, self._h = w, h
        self._running = False
        self._speed = speed
        self._vol_scale = 1.0
        self._lv = [0.0, 0.0]
        self._tg = [0.0, 0.0]
        self._tg_shared = 0.0
        self._pk = [0.0, 0.0]
        self._pkt = [0, 0]
        self._real_level = 0.0
        self._real_ts = 0.0
        #: what the last queued frame will paint, in whole pixels
        self._painted: tuple[int, int, int, int] | None = None
        self.da.connect("draw", self._draw)

    def feed_level(self, level: float):
        self._real_level = max(0.0, min(1.0, float(level)))
        self._real_ts = time.monotonic()

    def set_state(self, state, vol=50):
        self._running = state == "RUNNING"
        self._vol_scale = max(0.08, vol / 100.0)

    def tick(self):
        use_real = (time.monotonic() - self._real_ts) < 2.0
        if use_real:
            level = self._real_level
            for i in range(2):
                tgt = max(0.0, min(1.0, level))
                self._lv[i] += (tgt - self._lv[i]) * self._speed
                self._lv[i] = max(0.0, min(1.0, self._lv[i]))
                if self._lv[i] >= self._pk[i]:
                    self._pk[i] = self._lv[i]
                    self._pkt[i] = 55
                elif self._pkt[i] > 0:
                    self._pkt[i] -= 1
                else:
                    self._pk[i] = max(0.0, self._pk[i] - 0.015)
        else:
            # idle -- instantly drop, no phantom blips
            for i in range(2):
                self._lv[i] = 0.0
                self._pk[i] = 0.0
                self._tg[i] = 0.0
            self._tg_shared = 0.0
        # Only ask for a frame that would differ from the one already on
        # screen. The bar is drawn in whole pixels, so a level that eases by
        # a thousandth paints identically -- and a silent bar paints
        # identically forever. Seven meters at 20fps were repainting 140
        # times a second to show the same picture.
        frame = (
            int(self._lv[0] * self._h),
            int(self._lv[1] * self._h),
            int(self._pk[0] * self._h),
            int(self._pk[1] * self._h),
        )
        if frame != self._painted:
            self._painted = frame
            self.da.queue_draw()

    def _draw(self, widget, cr):
        ww, hh = self._w, self._h
        bar_w = (ww - 6) // 2

        for i, x0 in enumerate([2, bar_w + 4]):
            lv = max(0.0, min(1.0, self._lv[i]))
            pk = max(0.0, min(1.0, self._pk[i]))
            fill_h = int(lv * hh)

            cr.set_source_rgba(0.10, 0.10, 0.12, 0.92)
            cr.rectangle(x0, 0, bar_w, hh)
            cr.fill()

            if fill_h > 0:
                if _HAVE_CAIRO:
                    pat = _cairo.LinearGradient(x0, hh, x0, 0)
                    pat.add_color_stop_rgba(0.00, 0.19, 0.82, 0.35, 0.96)
                    pat.add_color_stop_rgba(0.65, 0.19, 0.82, 0.35, 0.96)
                    pat.add_color_stop_rgba(0.82, 1.00, 0.62, 0.04, 0.96)
                    pat.add_color_stop_rgba(1.00, 1.00, 0.27, 0.22, 0.96)
                    cr.set_source(pat)
                    cr.rectangle(x0, hh - fill_h, bar_w, fill_h)
                    cr.fill()
                else:
                    # fallback: 3-band solid blocks
                    g_h = min(fill_h, int(0.65 * hh))
                    a_h = min(fill_h - g_h, int(0.17 * hh))
                    r_h = fill_h - g_h - a_h
                    y = hh - fill_h
                    if g_h:
                        cr.set_source_rgba(0.19, 0.82, 0.35, 0.92)
                        cr.rectangle(x0, y, bar_w, g_h)
                        cr.fill()
                        y += g_h
                    if a_h:
                        cr.set_source_rgba(1.0, 0.62, 0.04, 0.92)
                        cr.rectangle(x0, y, bar_w, a_h)
                        cr.fill()
                        y += a_h
                    if r_h:
                        cr.set_source_rgba(1.0, 0.27, 0.22, 0.92)
                        cr.rectangle(x0, y, bar_w, r_h)
                        cr.fill()

                cr.set_source_rgba(1.0, 1.0, 1.0, 0.14)
                cr.rectangle(x0, hh - fill_h, 1, fill_h)
                cr.fill()

            if pk > 0.015:
                pk_y = int((1.0 - pk) * hh)
                if pk < 0.65:
                    cr.set_source_rgba(0.19, 0.92, 0.35, 1.0)
                elif pk < 0.84:
                    cr.set_source_rgba(1.00, 0.72, 0.04, 1.0)
                else:
                    cr.set_source_rgba(1.00, 0.37, 0.22, 1.0)
                cr.rectangle(x0, max(0, pk_y - 1), bar_w, 2)
                cr.fill()

            cr.set_source_rgba(1.0, 1.0, 1.0, 0.04)
            cr.set_line_width(1.0)
            cr.rectangle(x0 + 0.5, 0.5, bar_w - 1, hh - 1)
            cr.stroke()


def make_led(sz=8):
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    _c = ["#3a3a3c"]
    da = Gtk.DrawingArea()
    da.set_size_request(sz + 4, sz + 4)

    def draw(w, cr):
        try:
            r = int(_c[0][1:3], 16) / 255
            g = int(_c[0][3:5], 16) / 255
            b = int(_c[0][5:7], 16) / 255
        except Exception:
            r = g = b = 0.25
        cr.set_source_rgba(r, g, b, 0.18)
        cr.arc(sz / 2 + 2, sz / 2 + 2, sz / 2 + 2, 0, 6.2832)
        cr.fill()
        cr.set_source_rgba(r, g, b, 1.0)
        cr.arc(sz / 2 + 2, sz / 2 + 2, sz / 2, 0, 6.2832)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.32)
        cr.arc(sz / 2 + 1, sz / 2 + 1, sz / 4, 0, 6.2832)
        cr.fill()

    def set_c(h):
        _c[0] = h
        da.queue_draw()

    da.connect("draw", draw)
    return da, set_c
