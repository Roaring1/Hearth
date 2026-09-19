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

button.mute {{
  background: {pal["view-alt"]};
  color: {pal["fg-dim"]};
  border: 1px solid {pal["win-alt"]};
  border-radius: 3px;
  padding: 2px 0;
  min-height: 0;
  font-size: 9.5px;
  letter-spacing: 1px;
}}
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
.rail-tag {{
  font-family: "Andale Mono", monospace;
  font-size: 9px;
  color: {pal["fg-dim"]};
}}
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
"""
