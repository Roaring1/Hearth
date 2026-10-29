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
the real GTK widgets and production styles render. It captures six named PNGs:

1. `mixer-loading.png`
2. `mixer-ready.png`
3. `mixer-missing-device.png`
4. `mixer-fault-banner.png`
5. `setup.png`
6. `lpd8-controls.png`

The test checks meaningful widget state (including loading controls disabled,
ready controls enabled, missing-device state, and all eight pads/knobs), then
captures the actual root window and crops the unused black Xvfb desktop. CI
uploads the captures as the `hearth-gtk-visual-audit` artifact for 14 days, even
when the test fails. The package smoke job also verifies all four SVGs are
present after installing the built wheel.

This is a first real-rendering/visual-evidence gate, **not yet a full visual
regression suite**. It saves screenshots and checks that they're nonempty and
appropriately sized, but there are not yet approved golden images with a pixel
diff threshold. It doesn't yet automate every popup/menu, save failure, focus
state, interaction flow, DPI scale, or Plasma theme.

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

- Minimized channel slivers now behave as real keyboard-accessible controls:
  they are focusable, expose the GTK button role, and announce `Restore
  {channel}` to assistive technology instead of being anonymous drawing areas.
- Enter, keypad Enter and Space emit a `restore-requested` signal. The main
  window wires this into the same existing restore operation used by pointer
  activation, so keyboard restoration doesn't fork persistence/layout logic.
- Xvfb regression coverage checks the button role and focusability, Enter/Space
  activation and ignored unrelated keys. The test remains isolated from the
  host desktop and audio graph.
- Local `./scripts/check.sh` passes with **475 tests** and the complete lint,
  format, mypy, metadata and ShellCheck gates. The previous accessible-fader
  commit (`4e834c4`) passed hosted CI run [#111](https://github.com/Roaring1/Hearth/actions/runs/40951877992).
- Live Plasma focus traversal, AT-SPI label inspection and visual focus-ring
  confirmation are still manual verification items; Xvfb tests only establish
  the widget semantics and key handler behavior.
