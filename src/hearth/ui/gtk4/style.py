"""Palette and stylesheet for the GTK4 mixer.

The palette comes from the desktop (``hearth.theme``) so the window matches
Plasma instead of guessing. Everything the widgets draw and every CSS rule is
derived from that one dict, which is why there are no hard-coded colours in
the layout code.

Colour meanings are fixed by the design brief and must not drift:

* accent  - hardware touched this control
* red     - a fault the user should act on
* green   - running, or signal present
* amber   - a meter above -6 dBFS, nothing else
* lilac   - could not be checked
"""

from __future__ import annotations

from hearth import theme as theme_mod

__all__ = ["CSS", "palette"]

#: Fixed meanings. These are not theme tokens: a fault is red on every desktop.
_SIGNAL = {
    "ok": "#30d158",
    "hot": "#e0c14a",
    "bad": "#ff453a",
    "unknown": "#c8a2e0",
    "meter-off": "#1b1b1b",
    "meter-dim": "#3b3b3b",
}


def palette() -> dict[str, str]:
    """The token dict the widgets and the stylesheet both read."""
    try:
        desktop = theme_mod.read()
        tokens = dict(desktop.tokens)
        accent = desktop.accent
    except Exception:  # pragma: no cover - a themeless session still gets a window
        tokens = dict(theme_mod.FALLBACK)
        accent = theme_mod.FALLBACK.get("selection-bg", "#e93a9a")

    def token(name: str, default: str) -> str:
        value = tokens.get(name) or default
        return value if isinstance(value, str) and value.startswith("#") else default

    pal = {
        "win": token("window-bg", "#363636"),
        "win-alt": token("window-bg-alt", "#424242"),
        "view": token("view-bg", "#242424"),
        "view-alt": token("view-bg-alt", "#303030"),
        "fg": token("window-fg", "#fcfcfc"),
        "fg-dim": token("window-fg-dim", "#a0a0a0"),
        "button": token("button-bg", "#656565"),
        "selection": token("selection-bg", "#ad3376"),
        "accent": accent or "#e93a9a",
    }
    pal.update(_SIGNAL)
    # Derived drawing tokens, named for what they are rather than where they sit.
    pal["slot"] = pal["meter-off"]
    pal["slot-fill"] = pal["view-alt"]
    pal["cap"] = pal["button"]
    pal["cap-groove"] = pal["view"]
    pal["rule"] = pal["button"]
    return pal


def CSS(pal: dict[str, str]) -> str:
    """The whole stylesheet, built from *pal*."""
    return f"""
window.hearth {{
  background: {pal["win"]};
  color: {pal["fg"]};
  font-family: Tahoma, "Trebuchet MS", Verdana, sans-serif;
  font-size: 11px;
}}

/* ---- groups ------------------------------------------------------- */
.grp {{
  background: {pal["win-alt"]};
  border: 1px solid {pal["view"]};
  border-radius: 5px;
  padding: 4px 5px 5px 5px;
}}
.grp-tag {{
  font-family: "Andale Mono", monospace;
  color: {pal["fg-dim"]};
  font-size: 9px;
  letter-spacing: 0.5px;
  padding: 0 2px 2px 2px;
}}

/* ---- strips ------------------------------------------------------- */
.strip {{
  background: {pal["view"]};
  border: 1px solid {pal["win-alt"]};
  border-radius: 4px;
  padding: 4px;
}}
.strip.dead {{
  background: {pal["view-alt"]};
  border-color: {pal["bad"]};
}}
/* A muted bus is a state of the whole channel, so the whole channel
   shifts: desaturated panel, dimmed text. Slight on purpose - it must
   read at a glance without competing with a real fault. */
.strip.muted-bus {{
  background: {pal["win-alt"]};
  border-color: {pal["button"]};
}}
.strip.muted-bus .strip-name, .strip.muted-bus .value {{ color: {pal["fg-dim"]}; }}

/* Tweens. GTK animates these properties for free once a duration is
   set, which is the cheapest way to stop the window snapping. */
.strip, .grp, .sliver, button.mute, .appmute, .minbox, .outbtn {{
  transition: background 140ms ease-out, border-color 140ms ease-out,
              color 140ms ease-out, opacity 160ms ease-out;
}}

/* ---- routing popover --------------------------------------------- */
.outbtn {{
  background: {pal["view-alt"]};
  color: {pal["fg-dim"]};
  border: 1px solid {pal["win-alt"]};
  border-radius: 3px;
  padding: 2px 6px;
  min-height: 0;
  font-size: 9.5px;
}}
.outbtn:hover {{ color: {pal["fg"]}; border-color: {pal["button"]}; }}
/* A Gtk.MenuButton is a wrapper: the CSS class lands on the wrapper while
   the desktop theme goes on styling the real button inside it. That is why
   OUT and the knob chip came out fat and Breeze-shaped. */
.outbtn > button, .bind > button {{
  background: transparent;
  background-image: none;
  box-shadow: none;
  border: none;
  outline: none;
  min-height: 0;
  min-width: 0;
  padding: 0;
  margin: 0;
  color: inherit;
  font-size: inherit;
}}
.outbtn > button > *, .bind > button > * {{ min-height: 0; }}
/* The arrow is the widest thing in the chip and says nothing the pointer
   does not already say. */
.outbtn arrow, .bind arrow {{ min-width: 0; min-height: 0; -gtk-icon-size: 8px; }}
.outpop contents {{
  background: {pal["win-alt"]};
  border: 1px solid {pal["view"]};
  border-radius: 5px;
  padding: 6px 8px;
}}
.outpop checkbutton {{ font-size: 10px; color: {pal["fg"]}; }}
.outhint {{
  color: {pal["fg-dim"]};
  font-size: 9px;
  padding-bottom: 3px;
}}

.strip.ghost {{
  background: transparent;
  border: 1px dashed {pal["button"]};
}}
.strip-name {{
  font-size: 10px;
  font-weight: bold;
  letter-spacing: 0.3px;
}}
.strip.dead .strip-name {{ color: {pal["fg-dim"]}; }}

.minbox, .showbtn, .railbtn {{
  background: {pal["view-alt"]};
  color: {pal["fg-dim"]};
  border: 1px solid {pal["win-alt"]};
  border-radius: 3px;
  padding: 1px 6px;
  min-height: 0;
  font-size: 10px;
}}
.minbox {{ padding: 0 5px; }}
/* GTK rings the focused widget in the desktop accent. In this window the
   accent means "hardware just moved this", so the first strip's minimise
   button lighting up on open reads as a false hardware signal. Focus gets
   a neutral ring instead. */
.hearth *:focus, .hearth *:focus-visible {{
  outline: 1px solid {pal["button"]};
  outline-offset: -2px;
}}
.minbox:hover, .showbtn:hover, .railbtn:hover {{
  color: {pal["fg"]};
  border-color: {pal["button"]};
}}
.value {{
  font-family: "Andale Mono", monospace;
  font-size: 17px;
  color: {pal["fg"]};
}}
.strip.dead .value {{ color: {pal["fg-dim"]}; }}
.bind {{
  font-family: "Andale Mono", monospace;
  font-size: 9px;
  color: {pal["fg-dim"]};
  background: {pal["view-alt"]};
  border-radius: 3px;
  padding: 1px 4px;
}}
.bind.hw {{ color: {pal["accent"]}; }}
/* B2 has no knob. Rather than let its strip end higher than the rest, it
   keeps the chip's footprint and draws nothing in it. */
.bind.empty {{ background: transparent; color: transparent; }}

button.mute {{
  background: {pal["view-alt"]};
  color: {pal["fg-dim"]};
  border: 1px solid {pal["win-alt"]};
  border-radius: 3px;
  padding: 1px 0;
  min-height: 0;
  min-width: 0;
  margin: 0;
  background-image: none;
  box-shadow: none;
  font-size: 9.5px;
  letter-spacing: 1px;
}}
/* Same story as the MenuButton: the theme's minimum metrics have to be
   beaten on every button this window draws, or one stray control sets the
   height of the whole row. */
.strip button, .grp button {{ min-height: 0; min-width: 0; }}
button.mute:hover {{ color: {pal["fg"]}; }}
button.mute:checked {{
  background: {pal["bad"]};
  color: #17110f;
  border-color: {pal["bad"]};
  font-weight: bold;
}}

/* A readout, not a chooser: the boxed-and-bordered version read as a
   dropdown and invited clicks that go nowhere. */
.device {{
  font-family: "Andale Mono", monospace;
  font-size: 9px;
  color: {pal["fg-dim"]};
  background: transparent;
  border: none;
  padding: 0 1px;
}}
.device.missing {{ color: {pal["bad"]}; }}
.device.unknown {{ color: {pal["unknown"]}; }}

.mark {{
  font-size: 9px;
  font-weight: bold;
  color: #10100f;
  border-radius: 3px;
  padding: 0 4px;
  min-width: 10px;
  background: #5a8f3a;
}}
.mark.discord {{ background: #5865f2; }}
.mark.firefox {{ background: #d6642a; }}
.mark.spotify {{ background: #1db954; }}
.mark.chrome {{ background: #4a7fa5; }}
.mark.obs {{ background: #8f949a; }}
.mark.steam {{ background: #3f7fbf; }}
.mark.vlc {{ background: #e8792a; }}
.mark.more {{ background: {pal["view-alt"]}; color: {pal["fg-dim"]}; }}
.listener {{ font-size: 9.5px; color: {pal["fg-dim"]}; }}
.appmute {{
  font-size: 8px;
  color: {pal["fg-dim"]};
  background: {pal["view-alt"]};
  border-radius: 2px;
  padding: 0 3px;
  /* It is a real button now, so strip the default button chrome. */
  border: none;
  box-shadow: none;
  min-height: 0;
  min-width: 0;
  margin: 0;
}}
.appmute:hover {{ color: {pal["fg"]}; }}
.appmute:disabled {{ opacity: 0.45; }}
.appmute.on {{ color: {pal["bad"]}; }}
.listener.muted {{ color: {pal["bad"]}; }}

/* ---- share destination -------------------------------------------- */
.dest {{
  background: {pal["view"]};
  border: 1px solid {pal["win-alt"]};
  border-radius: 4px;
  padding: 4px;
}}
.dest.bad {{ border-color: {pal["bad"]}; }}
.dest-tag {{
  font-family: "Andale Mono", monospace;
  font-size: 9px;
  color: {pal["fg-dim"]};
}}
.dest-warn {{ font-size: 9.5px; color: {pal["bad"]}; }}

/* ---- rail ---------------------------------------------------------- */
.rail-handle {{
  background: transparent;
  border: 1px solid {pal["win-alt"]};
  border-radius: 3px;
  color: {pal["fg-dim"]};
  font-size: 10px;
  min-height: 16px;
  min-width: 16px;
  padding: 0;
}}
.rail-handle:hover {{ border-color: {pal["accent"]}; color: {pal["fg"]}; }}
.banner {{
  background: {pal["view"]};
  border-left: 3px solid {pal["bad"]};
  border-radius: 3px;
  padding: 3px 8px;
}}
.banner-text {{ color: {pal["bad"]}; font-size: 10px; }}
.banner-restart {{
  background: transparent;
  border: 1px solid {pal["bad"]};
  border-radius: 3px;
  color: {pal["bad"]};
  font-size: 9px;
  letter-spacing: 0.6px;
  min-height: 0;
  padding: 1px 7px;
}}
.banner-restart:hover {{ background: {pal["bad"]}; color: {pal["view"]}; }}
.banner-restart:disabled {{ color: {pal["fg-dim"]}; border-color: {pal["fg-dim"]}; }}

/* ---- setup window -------------------------------------------------- */
/* One focal line at the top, everything else quiet underneath. The
   verdict is the only thing in the window allowed to be loud, and only
   when something is actually wrong. */
.setup-verdict {{ font-size: 17px; color: {pal["fg"]}; }}
.setup-verdict.bad {{ color: {pal["bad"]}; }}
.setup-tag {{
  font-family: "Andale Mono", monospace;
  font-size: 9px;
  letter-spacing: 0.5px;
  color: {pal["fg-dim"]};
}}
.setup-note {{ font-size: 10px; color: {pal["fg-dim"]}; }}
.setup-note.bad {{ color: {pal["bad"]}; }}
.setup-clash {{ font-size: 10px; color: {pal["hot"]}; }}
.setup-row {{
  border-left: 3px solid {pal["bad"]};
  padding: 2px 0 2px 8px;
}}
.setup-act {{
  background: transparent;
  border: 1px solid {pal["button"]};
  border-radius: 3px;
  color: {pal["fg"]};
  font-size: 10px;
  min-height: 0;
  padding: 2px 9px;
}}
.setup-act:hover {{ border-color: {pal["accent"]}; }}

/* ---- the drawn LPD8 ------------------------------------------------ */
/* The controller is drawn the way it looks on the desk: a dark slab, two
   rows of four pads, two rows of four knobs beside them. It is a picture
   a person can point at, so the boxes carry the label and nothing else. */
.lpd8 {{
  background: {pal["view"]};
  border: 1px solid {pal["win-alt"]};
  border-radius: 6px;
  padding: 10px;
}}
button.lpd8-pad {{
  background: {pal["view-alt"]};
  background-image: none;
  box-shadow: none;
  border: 1px solid {pal["button"]};
  border-radius: 4px;
  color: {pal["fg"]};
  padding: 5px 6px;
  min-width: 74px;
  min-height: 54px;
}}
button.lpd8-pad:hover {{ border-color: {pal["accent"]}; }}
/* A pad the script does not use is still a pad: same box, nothing in it. */
button.lpd8-pad.free {{ background: transparent; border-style: dashed; }}
button.lpd8-pad.sel {{
  border-color: {pal["accent"]};
  border-width: 2px;
  padding: 4px 5px;
}}
button.lpd8-knob {{
  background: transparent;
  background-image: none;
  box-shadow: none;
  border: none;
  padding: 2px;
  min-height: 0;
  min-width: 62px;
  color: {pal["fg"]};
}}
button.lpd8-knob:hover {{ background: {pal["view-alt"]}; border-radius: 5px; }}
.lpd8-cap {{
  font-family: "Andale Mono", monospace;
  font-size: 8.5px;
  letter-spacing: 0.6px;
  color: {pal["fg-dim"]};
}}
.lpd8-job {{ font-size: 10px; color: {pal["fg"]}; }}
button.lpd8-pad.free .lpd8-cap {{ opacity: 0.6; }}
.lpd8-sel {{ font-size: 12px; color: {pal["fg"]}; }}
.lpd8-detail {{ padding: 0 2px; }}
"""
