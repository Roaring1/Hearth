# Contributing to Hearth

Hearth is a personal project published so others can use it or learn from it.
Issues and pull requests are welcome; the rules below exist so the codebase
stays readable rather than to make contributing difficult.

## Getting set up

PyGObject is **not** installed with pip. It comes from your distribution:

```bash
# Fedora / Nobara
sudo dnf install python3-gobject gtk3 libayatana-appindicator-gtk3
# Debian / Ubuntu
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1
# Arch
sudo pacman -S python-gobject gtk3 libayatana-appindicator
```

Then, in a virtualenv that can see the system packages:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e '.[dev]'
pre-commit install
```

## Before you open a pull request

```bash
./scripts/check.sh
```

That runs exactly what CI runs: `ruff check`, `ruff format --check`, `mypy`,
`pytest`, `desktop-file-validate`, `appstreamcli validate` and `shellcheck`.
If it is green locally, CI should be green too.

## Layout rules

These are enforced by the linter, not just by convention.

- **`src/` layout.** Tests run against the installed package, never against a
  stray working directory.
- **Only `hearth.ui` may import `gi`.** The rest of the codebase stays
  GTK-free so it can be tested headlessly and so a future toolkit change is
  survivable.
- **All subprocess calls go through `hearth.proc`.** Argv lists only;
  `shell=True` is banned and the ban is a lint error. Every call has a
  timeout.
- **No `~` or `Path.home()` outside `hearth.paths`.** Ask `paths` for a
  location. It implements the XDG Base Directory Specification, including
  putting the socket in `$XDG_RUNTIME_DIR` and the log in `$XDG_STATE_HOME`.
- **No bare `except:` and no `print()`.** Raise something from
  `hearth.errors`, or log through `logging.getLogger(__name__)`. Logging is
  configured once, in `hearth.cli`, and nowhere else.

## Rules for anything the user reads

These are not style preferences. Each one is here because breaking it
produced a real bug in this app.

- **Judge the effect, not the unit.** A oneshot reports `inactive (dead)` on
  success and a timer reports `waiting` when healthy. Reading those as
  failures is how Hearth spent months reporting a working session as
  "portal: NOT RUNNING". Where a unit's state cannot answer the question,
  measure the PipeWire graph.
- **Unknown is not broken.** A probe that could not run must report
  *unknown*. `links.list_links()` raises rather than returning an empty list
  precisely so "no links" and "could not ask" stay distinguishable.
- **Name the problem, not the count.** "The mic busses service stopped
  (+3 more)", never "4 issues". Every issue carries a fix.
- **No internal names in user-facing text.** B1 and B2 are "Stream" and
  "Discord"; `mic_b1`, `vm_game`, `pactl` and `systemctl` must never reach a
  message. Several modules have tests asserting exactly this.
- **Empty states are designed, not blank.** "No recordings yet" says what to
  do next.
- **Read paths do no expensive work.** Anything slow belongs behind an
  explicit action in `hearth.tools`, not in a refresh tick.

## Tests

Tests live in `tests/` and mirror the module they cover. Tests that need GTK
and a display carry the `gui` marker and skip automatically when either is
missing; in CI they run under a virtual display. The audio layer is tested
against recorded `pactl` and `pw-dump` fixtures, so a full PipeWire rig is not
required to work on it.

## Commits and releases

- Conventional commits: `fix:`, `feat:`, `refactor:`, `docs:`, `chore:`,
  `test:`, `build:`, `ci:`.
- One logical change per commit, and the tree works at every commit.
- The version lives only in `src/hearth/__init__.py`. Releases update
  `CHANGELOG.md` (Keep a Changelog format), bump that constant, add a
  `<release>` entry to the AppStream metainfo file, and tag `vX.Y.Z`.

## Reporting a bug

Include your distribution, desktop environment and session type (X11 or
Wayland), `hearth --version`, the output of `hearth --status` and
`hearth --paths`, and the relevant part of
`$XDG_STATE_HOME/hearth/hearth.log`.
