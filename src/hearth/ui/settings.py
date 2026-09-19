"""The modal settings dialog, moved verbatim from the legacy module."""

from hearth.legacy_core import (
    MXCONF,
    save_settings,
    write_conf_key,
)

__all__ = ["settings_dialog"]


def settings_dialog(parent, S, on_save):
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    dlg = Gtk.Dialog(title="Settings", transient_for=parent, modal=True)
    dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
    dlg.set_default_size(420, 0)
    box = dlg.get_content_area()
    box.set_spacing(0)
    box.set_border_width(20)

    def sec(txt):
        l = Gtk.Label(label=txt)
        l.set_xalign(0)
        l.get_style_context().add_class("ptitle")
        l.set_margin_top(14)
        l.set_margin_bottom(6)
        box.pack_start(l, False, False, 0)

    def row(lbl_txt, widget, note=None):
        hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hb.set_margin_bottom(6)
        lb = Gtk.Label(label=lbl_txt)
        lb.set_xalign(0)
        lb.set_width_chars(26)
        lb.get_style_context().add_class("cfg-label")
        hb.pack_start(lb, False, False, 0)
        hb.pack_end(widget, False, False, 0)
        if note:
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            vb.pack_start(hb, False, False, 0)
            nl = Gtk.Label(label=note)
            nl.set_xalign(0)
            nl.get_style_context().add_class("cfg-note")
            vb.pack_start(nl, False, False, 0)
            box.pack_start(vb, False, False, 0)
        else:
            box.pack_start(hb, False, False, 0)

    def hsep():
        s = Gtk.Separator()
        s.set_margin_top(8)
        s.set_margin_bottom(2)
        box.pack_start(s, False, False, 0)

    sec("POLLING")
    r_map = {"500 ms": 500, "1 s": 1000, "2 s": 2000, "5 s": 5000, "10 s": 10000}
    r_combo = Gtk.ComboBoxText()
    for k in r_map:
        r_combo.append_text(k)
    cur_r = S.get("refresh_ms", 2000)
    r_combo.set_active(list(r_map.keys()).index(min(r_map, key=lambda k: abs(r_map[k] - cur_r))))
    row("Data refresh interval", r_combo)

    hsep()
    sec("VU METERS")
    vu_sw = Gtk.Switch()
    vu_sw.set_active(S.get("vu_enabled", True))
    row("Show VU meters", vu_sw, "Correlated stereo simulation (no DSP tap)")
    sp_adj = Gtk.Adjustment(S.get("vu_speed", 0.35) * 100, 10, 80, 5)
    sp_sc = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=sp_adj)
    sp_sc.set_digits(0)
    sp_sc.set_size_request(140, -1)
    row("VU response speed (%)", sp_sc, "Higher = more reactive")

    hsep()
    sec("MEMORY WARNINGS  (pipewire-pulse)")
    w_sp = Gtk.SpinButton.new_with_range(20, 800, 10)
    w_sp.set_value(S.get("mem_warn_mb", 80))
    w_sp.set_size_request(80, -1)
    row("Warning threshold (MB)", w_sp)
    c_sp = Gtk.SpinButton.new_with_range(50, 2000, 10)
    c_sp.set_value(S.get("mem_crit_mb", 200))
    c_sp.set_size_request(80, -1)
    row("Critical threshold (MB)", c_sp)

    hsep()
    sec("LOOPBACK LATENCY")
    lat_sp = Gtk.SpinButton.new_with_range(1, 200, 1)
    lat_sp.set_value(S.get("latency_msec", 12))
    lat_sp.set_size_request(80, -1)
    row("Latency (ms)", lat_sp, "Written to roaring_mixer.conf on save")

    hsep()
    sec("BEHAVIOUR")
    min_sw = Gtk.Switch()
    min_sw.set_active(S.get("start_minimized", False))
    row("Start minimized to tray", min_sw)
    ntf_sw = Gtk.Switch()
    ntf_sw.set_active(S.get("notify_fail", True))
    row("Desktop notification on failures", ntf_sw)

    box.show_all()
    resp = dlg.run()
    if resp == Gtk.ResponseType.OK:
        S["refresh_ms"] = r_map[r_combo.get_active_text()]
        S["vu_enabled"] = vu_sw.get_active()
        S["vu_speed"] = sp_adj.get_value() / 100.0
        S["mem_warn_mb"] = int(w_sp.get_value())
        S["mem_crit_mb"] = int(c_sp.get_value())
        S["latency_msec"] = int(lat_sp.get_value())
        S["start_minimized"] = min_sw.get_active()
        S["notify_fail"] = ntf_sw.get_active()
        save_settings(S)
        write_conf_key(MXCONF, "LATENCY_MSEC", str(S["latency_msec"]))
        on_save(S)
    dlg.destroy()
