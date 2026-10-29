# Hearth UI quality audit — first visual-test slice

**Status:** working branch; representative GTK screenshots now run in CI.
**Intent:** improve loading clarity and action affordances without widening the
mixer or replacing working controls with unexplained icon-only buttons.

## What changed

- A channel before the first verified collector snapshot now renders as a quiet
ghost strip. It says `checking…` and `Loading apps…`, dims the value area, and
keeps mute/fader interaction disabled until state is known. This is distinct
from a confirmed missing device (`no device`), which gets the fault treatment.
- Four original, hand-authored SVG action icons were added: collapse, refresh,
save, and sliders/remap. The icon source is simple SVG geometry only; there are
no embedded raster images, external `href`s, AI-generated illustrations, or
third-party icon files. `tests/test_action_icons_assets.py` checks this contract.
- Important Setup actions pair icons with visible words. The icon-only minimise
control has a descriptive tooltip and an explicit GTK accessible label. The
Save button keeps its icon through `Saving…`, `Save failed`, `Saved*`, and
`Saved` states.
- Setup's LPD8 tooltip no longer suggests NOTE TOGGLE mode can make the classic
LPD8 mirror Hearth's mute state. The installed controller cannot receive MIDI
Note messages for pad-light feedback; its physical lamps are not a source of
mixer state.

## Real UI test coverage

`tests/test_gui_visual.py` launches the real GTK4 `MixerApp`, `MixerWindow`,
`SetupWindow`, and `RemapWindow` under Xvfb. It does **not** connect to live
PipeWire, invoke systemd, access the installed LPD8 script, or mutate user
settings. Backends and state are replaced with deterministic fixtures while
the real GTK widgets and production styles render. It captures ten named PNGs:

1. `mixer-loading.png`
2. `mixer-ready.png`
3. `mixer-app-move-focus.png`
4. `mixer-fader-focus.png`
5. `mixer-missing-device.png`
6. `mixer-fault-banner.png`
7. `setup.png`
8. `lpd8-controls.png`
9. `mixer-rail-focus.png`
10. `mixer-reorder-grip-focus.png`

The test checks meaningful widget state (including loading controls disabled,
ready controls enabled, missing-device state, and all eight pads/knobs), then
captures the actual root window and crops the unused black Xvfb desktop. CI
uploads the captures as the `hearth-gtk-visual-audit` artifact for 14 days, even
when the test fails. The package smoke job also verifies all four SVGs are
present after installing the built wheel.

This is a first real-rendering/visual-evidence gate, **not yet a full visual
regression suite**. It saves screenshots and checks that they're nonempty and
appropriately sized, but there are not yet approved golden images with a pixel
diff threshold. It doesn't yet automate every popup/menu, save failure,
interaction flow, DPI scale, or Plasma theme. The focus captures prove the
GTK focus ring can be rendered in Xvfb, not that every desktop theme or AT-SPI
screen reader presents it optimally.

## First visual review

- The mixer is deliberately narrow and information-dense. On the 1600x1200
  virtual display its representative window is about 715x253 px. Channel names,
  values, route chips, mute states and meters fit without horizontal scrolling.
- Loading now visibly differs from the live state without introducing a large
  spinner or a second loader surface. The dashed outline and dimming are enough
  for this compact screen; avoid adding pulsing shimmer unless user testing
  shows that loading is otherwise easy to miss. The app already has restrained
  140-160 ms CSS color/opacity transitions and eased window-resize steps; this
  slice reuses them rather than adding motion to every widget.
- Setup is a larger, vertically organized control surface; icon-plus-text buttons
  improve scanning without making it a wall of pictograms. The remap visual is
  mostly an actual controller layout, so its own labels are more useful than
  decorative icons on every pad.
- The screenshots use the GTK/Xvfb environment in CI. A later pass must also
  inspect the real Plasma desktop, the user's preferred scale factor, keyboard
  navigation/focus, and high-contrast themes. A screenshot alone is not an
  accessibility test.

## Code-review notes and remaining opportunities

1. **First-use icon lookup stall — fixed in this slice.** Cold profiling showed
   a 2.58-second recursive icon-file index build and a 180 ms desktop-entry scan
   on this host. Icon rows now use a non-blocking lookup: themed icons load
   immediately, a styled letter chip appears when a file index is needed, and
   a background worker builds/caches the desktop-entry and icon-file indexes.
   The actual icon replaces the chip from the GTK main loop once available.
   A failed/unknown lookup safely keeps the chip. Cold-cache responsiveness
   should still be measured under CI and at least once on the actual desktop.
2. **No golden screenshot baseline yet.** Add baselines only after the user has
   approved the captured layout; use stable fonts/theme/DPI in CI and review
   pixel diffs rather than blindly updating snapshots. Capture failure artifacts
   unconditionally so a failed assertion does not erase the most useful evidence.
3. **Interaction tests need a separate layer.** The visual test intentionally
   does not click controls that would mutate the host. The next step is fake
   backend interaction tests for popovers, focus/keyboard navigation, drag/drop,
   progress/error states, and window close/reopen; a smaller explicit hardware
   smoke checklist can cover real PipeWire/systemd/controller actions.
4. **Avoid ornamental AI imagery.** Hearth's controls are status/action UI, not a
   marketing page. Prefer original SVG glyphs or clearly licensed, provenance-
   recorded icons; do not generate images for buttons, meters, logos or placeholder
   artwork. Keep repeated icons on a shared grid/stroke, use familiar metaphors,
   and leave visible text on consequential actions.

## External guidance used

- [GTK4 accessibility](https://docs.gtk.org/gtk4/section-accessibility.html):
  standard GTK controls expose accessibility semantics; icon-only buttons should
  have explicit user-readable labels, and tooltips/labels should describe action.
- [Lucide's public source and license](https://github.com/lucide-icons/lucide):
  useful as a consistency reference for simple outline glyphs; Hearth's four new
  assets were drawn for this project rather than copied from the library.
- [Playwright visual comparisons](https://playwright.dev/docs/test-snapshots):
  useful general principles for deterministic screenshot environments and
  reviewed image baselines. Playwright itself is browser-oriented; native GTK
  should keep using its actual GTK widgets under Xvfb plus explicit interaction
  and accessibility checks rather than pretending this is a web app.

## Accessibility follow-up — 2026-10-29

- Icon-only per-application mute buttons now expose a descriptive accessible
  label (`Mute Firefox` / `Unmute Firefox`) and update it as the action changes.
  The app name's muted styling is kept in sync with the button.
- The rail-docking arrow now exposes the action it will perform (move the
  minimised-channel rail to the bottom or right), rather than announcing only
  the arrow character.
- The custom-drawn volume fader is now focusable and presents the GTK slider
  role with minimum, maximum, current-value and value-text properties. Up/Right
  adjust by one point, Down/Left decrease by one, Page Up/Down adjust by ten,
  and Home/End jump to 0/150%. Changes use the same `moved` signal as pointer
  input so the established coalesced audio-write path remains in charge.
- Marking a channel unavailable also disables the fader. Dead/loading faders
  ignore keyboard and scroll changes and return to normal sensitivity when the
  channel is available again. The app-mute cache now reconciles stale snapshots
  after optimistic updates rather than holding a false state indefinitely.
- Automated regressions cover stale mute snapshots, changing accessible names,
  the rail action name, fader role/focusability, key increments, range endpoints,
  and unavailable-state input suppression.
- The full local gate passes with **474 tests**; hosted CI should be checked on
  the commit carrying this follow-up. The automated fader test invokes the real
  GTK key handler under Xvfb; a live Plasma/AT-SPI screen-reader and keyboard
  pass remains an outstanding manual verification, not a claim made by Xvfb.

## Keyboard-accessible minimized-channel rail — 2026-10-29

- Minimized channel slivers are native `Gtk.Button` widgets with a decorative
  custom-drawn child. They expose the button role and `Restore {channel}` name,
  and rely on GTK's built-in button action for pointer, Enter/Space and
  assistive-technology activation instead of simulating those behaviors on a
  `DrawingArea`. The sliver now has a neutral border plus an explicit focus
  border/outline; the focus screenshot restores the normal device/banner state
  so this cue is not mistaken for a missing-device warning.
- Xvfb regression coverage checks that the sliver is a native button, is
  focusable, has a decorative child and routes its `clicked` signal. The test
  verifies that both focused-control captures differ visibly from their
  unfocused reference state; it does not synthesize key events, because keyboard
  and assistive-technology activation are delegated to GTK's native button
  implementation. The test remains isolated from the host desktop and audio graph.
- Local `./scripts/check.sh` passes with **475 tests** and the complete lint,
  format, mypy, metadata and ShellCheck gates. The previous accessible-fader
  commit (`4e834c4`) passed hosted CI run [#111](https://github.com/Roaring1/Hearth/actions/runs/40951877992).
- Live Plasma focus traversal and AT-SPI/screen-reader inspection remain manual
  verification items. Xvfb establishes the widget type/role, focusability and
  visible focus state, but does not establish the user's desktop screen-reader
  announcements or theme-specific behavior.


## Keyboard strip reordering — 2026-10-29

- The grip for reordering strips is focusable, has the accessible group label
  `Reorder {channel}; press Left or Right to move`, and preserves its original
  pointer drag-source behavior. Left/Right moves one visible strip within its
  own group through the existing reorder/save path; focus returns to the new
  grip after the strip tree rebuilds. Movement past a group edge is a no-op.
- Test coverage includes role/focusability, order and persistence calls,
  group-edge handling, focus restoration, and a production-window Xvfb test
  that reorders Game right and back left and confirms focus survives both
  real GTK rebuilds. The nine-image visual suite also captures a focused reorder
  grip and asserts a visible pixel change from its unfocused reference.
- The local full gate passes with **476 tests**. Hosted CI is being checked for
  the most recent real-window focus regression; live Plasma/AT-SPI verification
  is still a separate manual item.


## Per-app volume accessible names — 2026-10-29

Each application volume scale receives an explicit accessible label such as
`Firefox volume`, in addition to its current percentage/value and tooltip. The
shared GTK helper now correctly accepts `Gtk.Widget` instead of only buttons;
regression coverage checks this name at row construction. This does not change
the slider's range, gesture handling, volume target or write-coalescing path.


## Setup latency control name — 2026-10-29

The numeric latency spin button explicitly announces `Routing latency in
milliseconds`. The real GTK Setup-window visual test verifies that the label
helper is called for the actual spin button and that its native spin-button role
is preserved. This is an accessible-name change only; the value/range/save
behavior remains unchanged.


## LPD8 pad/knob names — 2026-10-29

Each physical-layout pad and knob is a native button with an explicit accessible
name such as `LPD8 pad 3: Mute Game` or `LPD8 knob 5: Game volume`. Unassigned
controls announce that they are unassigned, and map refresh updates the label.
The GUI regression observes GTK's LABEL property updates while constructing and
remapping actual GTK controls; it does not claim live screen-reader verification.


## Mixer routing menu names — 2026-10-29

The compact `OUT`/`IN` menu buttons have explicit contextual accessible labels
that name the route direction and channel. Tooltips carry the same phrase; a
production-window regression checks the GTK LABEL property on both a playback
bus output selector and a microphone input selector.


## Main bus mute names — 2026-10-29

Each channel's toggle keeps its visual `MUTE`/`MUTED` caption but names the
actual action and channel for assistive technology (`Mute Game`, `Unmute Chat`).
The name is synchronized from the verified snapshot and updated on user action;
command-failure rollback restores the matching action name too.


## Mixer knob-remap chip names — 2026-10-29

The short binding chip in a bus strip remains visually compact (for example,
`K7`) but exposes a contextual accessible name that identifies opening LPD8
remapping for the channel controlled by that knob. The production GTK test
checks the explicit label property.


## LPD8 assignment dropdown context — 2026-10-29

The one reused LPD8 assignment dropdown explicitly announces which hardware
control it edits (`Choose assignment for LPD8 pad N` or `... knob N`) each time
the menu model is refreshed. A production-window GTK regression checks Pad 3 and
Knob 5 without selecting an option or triggering a map write/restart.


## Keyboard movement of app streams — 2026-10-29

App rows can now be focused and operated with Left/Right to move the stream to
an adjacent visible bus; the visible app name remains the pointer drag source.
The accessible group label explains the action. Keyboard and pointer moves share
the same move method; focus restoration is keyed to both the stream ID and the
intended destination, so a stale snapshot on the old bus cannot clear it. The
regression uses a synthetic stream and mocks the move command, so it does not
mutate the live audio graph.


## Accessible live audio meter — 2026-10-29

The Cairo meter now reports a read-only GTK `METER` role, a per-channel name
(e.g. `Game audio level`), a 0–100 numeric range and a percentage text/current
value. The value is quantized to 5% increments to avoid emitting accessibility
updates at the full animation rate. A production-window regression exercises
one deterministic level update without starting PipeWire or touching the host
audio graph.


## Per-unit Setup restart names — 2026-10-29

Every dynamically generated service restart button has a contextual accessible
name and tooltip identifying the exact systemd unit, while retaining its compact
visible `Restart` caption. The production-window regression injects a synthetic
failed unit and verifies the label without invoking the restart handler.


## Mixer fault-banner Restart name — 2026-10-29

The banner's `RESTART` button names the actual unit(s) it will restart based on
which previously available buses disappeared. The tooltip is kept in sync, and
the hidden no-target state gets a neutral name. The production GTK test checks a
synthetic Laptop disappearance without invoking the restart handler.


## Setup health feedback — 2026-10-29

The Setup verdict label now exposes GTK's semantic `STATUS` role while keeping
its existing compact text/visibility behavior. The production-window regression
checks the status role and one synthetic failed-unit message together with that
unit's accessible Restart name. No real service is restarted by the test.


## Fault-banner cleared-state name — 2026-10-29

On recovery, the hidden banner Restart control resets its accessible label to
`Restart affected audio services` and clears its tooltip after disarming the
stale target list. The production-window test verifies both active and healthy
states without invoking systemd.

## App-row keyboard focus ownership — 2026-10-29

The app row only handles Left/Right stream movement when the row itself owns
focus. If the nested per-app volume scale is focused, those arrows are left for
slider adjustment rather than moving the stream. The production GTK test checks
the child-slider no-op and successful row-focused move, including focus return
after rebuild.



## Main repair-banner status role — 2026-10-29

When a previously available bus disappears, the visible repair-banner message
uses the GTK `STATUS` role. Production GTK coverage confirms the role and
message after the visibility transition, because GTK may recalculate a standard
label's role when its parent changes from hidden to visible.


## Meter mute/unavailable accessibility truthfulness — 2026-10-29

The accessible meter value now matches its visible dark state immediately: when
muted or unavailable it announces `0%` and does not report the private smoothed
level until it becomes active again. The production-window test checks active
35%, mute reset, persistence across ticks, and unavailable-state zero. This
changes accessible metadata only; the meter drawing and smoothing remain intact.


## Decorative dB ruler role — 2026-10-29

The non-interactive tick ruler beside the bus meter is now marked
`PRESENTATION`, while the adjacent live level and volume controls remain a
named `METER` and keyboard-operable `SLIDER`. The production GTK test asserts
all three roles so the decorative ruler doesn't become a spurious unnamed item.


## Bus fader unavailable value — 2026-10-29

When a channel is loading or disappears, the custom slider now reports value
text `Unavailable` and numeric zero. Its old numeric value is held internally
but not exposed during absence; on recovery the current actual percentage is
published again. The production GTK regression covers disable, suppressed
updates during unavailability, and recovery without talking to PipeWire.


## Route-popover busy-state consistency — 2026-10-29

A route mutation now disables every currently rendered output selector, raw
microphone input selector and Carla input toggle, then restores each control's
prior sensitivity. An input popover opened during a job also renders Carla
insensitive with a busy tooltip. Tests cover both controls already present when
the worker starts and controls created while the worker is already running.
