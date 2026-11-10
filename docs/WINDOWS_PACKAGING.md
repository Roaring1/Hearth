# Hearth Windows packaging contract

**Research date:** 2026-10-30
**Status:** Experimental Windows Core Audio/GTK4 pilot is installed and verified on the development laptop. A supported end-user installer and full Linux feature parity are not yet delivered.

## What the wheel declares

The project requires Python >=3.11. Its runtime dependency list contains:

```toml
dependencies = [
  "pycaw>=20260927; sys_platform == 'win32'",
]
```

Pycaw is the Windows Core Audio adapter used by `hearth.windows_audio`. Its package metadata brings `comtypes>=1.4.8` and `psutil>=5.9.0` transitively. The Windows marker is intentional: a Linux pip installation does not install Pycaw and continues to use the distribution's PipeWire/PulseAudio stack.

The selected Pycaw release documents support for Python 3.10 through 3.14 on Windows. Hearth's Python >=3.11 floor is within that range:

- [Pycaw on PyPI](https://pypi.org/project/pycaw/20260927/)
- [Pycaw installation and requirements](https://andremiras.github.io/pycaw/quickstart.html)

The Windows package metadata now also lists the Microsoft Windows OS classifier and describes Windows as an experimental Core Audio backend. This does not mean every Linux Hearth feature has been ported.

## What the wheel does not bundle

PyGObject/GTK is deliberately not a pip dependency. The supported development environment uses the MSYS2 UCRT64 packages for the native GTK stack. The live laptop's package inventory included:

| MSYS2 package | Observed version | Role |
| --- | --- | --- |
| `mingw-w64-ucrt-x86_64-python` | 3.14.9-1 | UCRT64 Python runtime |
| `mingw-w64-ucrt-x86_64-python-gobject` | 3.58.1-1 | PyGObject / `gi` |
| `mingw-w64-ucrt-x86_64-gtk4` | 4.24.1-1 | GTK4 widgets |
| `mingw-w64-ucrt-x86_64-glib2` | 2.90.1-1 | GLib / GObject runtime |
| `mingw-w64-ucrt-x86_64-gdk-pixbuf2` | 2.44.8-1 | Image loading |
| `mingw-w64-ucrt-x86_64-gobject-introspection-runtime` | 1.86.0-4 | Typelib loading |
| `mingw-w64-ucrt-x86_64-cairo` | 1.18.6-2 | Cairo graphics |
| `mingw-w64-ucrt-x86_64-python-cairo` | 1.29.2-1 | Python Cairo bindings |
| `mingw-w64-ucrt-x86_64-python-comtypes` | 1.4.17-1 | COM bindings |
| `mingw-w64-ucrt-x86_64-python-psutil` | 7.2.2-1 | Process/session support |

The laptop's theme package `mingw-w64-ucrt-x86_64-adwaita-icon-theme` is present too, but should be treated as a desktop theme/appearance component rather than a Hearth audio dependency.

These are **observed package names and versions on the current laptop**, not yet a pinned or validated clean-machine manifest. A real installer still needs to resolve/pin the UCRT64 package set, GTK typelibs, runtime DLL search paths, icons, and upgrades/uninstall.

## Why the current virtual environment sees GTK

The active `.venv-win/pyvenv.cfg` explicitly sets:

```ini
home = C:\msys64\ucrt64\bin
include-system-site-packages = true
version = 3.14.9
```

It was created with the MSYS2 UCRT64 interpreter and `python -m venv --system-site-packages`. This is deliberate: PyGObject, GTK, COM, and psutil are provided by pacman-managed UCRT64 packages, while Pycaw is installed into the project venv through pip. The inherited site packages also satisfy Pycaw's declared `comtypes` and `psutil` requirements.

A future bootstrap must preserve this arrangement, or replace it with a separately validated runtime strategy. Creating a default isolated venv without the MSYS2 system packages will not provide `gi`, GTK typelibs, or the native COM/psutil packages.

## Current development install shape

The verified environment uses:
- MSYS2 UCRT64 Python, not an unrelated Windows Python on PATH;
- a project venv created using `--system-site-packages`;
- PyGObject/GTK4 and native support packages installed through MSYS2;
- Pycaw installed in that venv via pip and declared through the Windows-only project marker;
- `PYTHONPATH=.../Hearth-Windows/src` while running from the checkout.

For a future installed wheel, pip must run from the same UCRT64 Python/venv so the Windows marker applies and Pycaw can load. GTK/PyGObject remains separately installed by MSYS2. The current checkout's `PYTHONPATH` is only needed for source-tree development, not for a correctly installed wheel.

## Verification completed

- The Windows interpreter reports `sys.platform == "win32"`.
- Installed Pycaw in the actual project venv: 20260927.
- Pycaw's transitive runtime dependencies in the project environment: comtypes 1.4.17 and psutil 7.2.2.
- The wheel's `METADATA` contains `Requires-Dist: pycaw>=20260927; sys_platform == 'win32'`.
- Evaluating the packaged marker returns true for Windows and false for Linux.
- The source distribution contains the same pyproject declaration.
- The Windows non-GUI suite after the metadata change: **393 passed, 85 skipped, 56 deselected**.

## Remaining before calling this installable

1. Define a clean-machine MSYS2 UCRT64 bootstrap and pinned runtime package manifest.
2. Validate a fresh `--system-site-packages` venv installing the built wheel and all PyPI-marked dependencies without relying on packages that happen to be preinstalled globally.
3. Add deterministic environment/DLL/typelib discovery and a launcher that invokes the intended UCRT64 Python.
4. Add an installer/uninstaller or documented MSYS2 package workflow, with backup and upgrade behavior.
5. Test the visible GTK application on a disposable/test audio profile before any real playback/capture acceptance.

The current wheel is a Python distribution artifact, **not** a standalone Windows installer. It does not install GTK, the MSYS2 runtime, a virtual audio driver, Voicemeeter, or a complete Hearth routing engine.

## Fresh-venv install validation

A new temporary venv was created using the MSYS2 UCRT64 Python with `--system-site-packages`, separate from the active project venv. Pip installed the just-built Hearth wheel normally, without `--no-deps`:

- Pip fetched and installed `pycaw 20260927` from its Windows-only marker.
- The existing MSYS2 system packages satisfied `comtypes 1.4.17` and `psutil 7.2.2`.
- Hearth 5.1.0 and Pycaw imported successfully from the temporary venv.
- The installed `python -m hearth --status` exited 0 and returned the expected read-only Windows service/endpoint inventory.
- The temporary venv was deleted by the probe's cleanup block.

This validates a fresh venv on the current MSYS2-equipped laptop. It does **not** replace a clean-machine install test: the temporary venv intentionally inherited the already-installed MSYS2 GTK/COM/psutil packages.

## Bootstrap helper

Added two repository scripts:

- `scripts/windows_bootstrap.ps1` checks the MSYS2 UCRT64 package set, validates the venv's base interpreter and `include-system-site-packages = true`, creates the venv when missing, installs the newest local Hearth wheel, and runs an import-only runtime probe.
- `scripts/windows_runtime_probe.py` imports GTK4/PyGObject, Pycaw, COM, psutil, and Hearth's Windows status module. It does not initialize GTK, enumerate audio endpoints, start services, or perform audio I/O.

The PowerShell helper sets `MSYSTEM=UCRT64`, prepends the UCRT64 and MSYS2 `usr/bin` directories to its child-process `PATH`, and sets `PYGI_DLL_PATH` before invoking Python. It can therefore be launched from ordinary PowerShell without requiring the caller to open an MSYS2 shell first; these environment changes are process-local. It normalizes the `home` field in `pyvenv.cfg` with `System.IO.Path.GetFullPath` before comparing the configured base interpreter to the expected UCRT64 directory, so both slash styles are accepted without weakening the path identity check. This was verified by creating the fresh venv through the helper from native PowerShell, installing the just-built wheel, importing the package/runtime, and running the installed CLI version command; the temporary environment was then removed.

Examples from PowerShell at the repository root:

```powershell
# Read-only package/venv/import validation
powershell -ExecutionPolicy Bypass -File .\scripts\windows_bootstrap.ps1 -CheckOnly

# Create the UCRT64 venv if needed and install the latest wheel in dist/
powershell -ExecutionPolicy Bypass -File .\scripts\windows_bootstrap.ps1

# Explicitly allow pacman to install missing runtime packages first
powershell -ExecutionPolicy Bypass -File .\scripts\windows_bootstrap.ps1 -InstallRuntimePackages

# Select a wheel or venv location
powershell -ExecutionPolicy Bypass -File .\scripts\windows_bootstrap.ps1 -WheelPath .\dist\hearth-5.1.0-py3-none-any.whl -VenvPath .\.venv-win
```

The default behavior reports missing MSYS2 packages and exits; it does not install them. The `-InstallRuntimePackages` switch is required to let pacman install missing packages. The script does not update the entire MSYS2 distribution automatically.

### Bootstrap validation performed

- `-CheckOnly` exited 0 on the current laptop: it found all required MSYS2 packages, validated the existing UCRT64 venv, imported GTK 4.24/Pycaw/comtypes/psutil/Hearth, and performed no endpoint or signal queries.
- Install mode was exercised with a dedicated temporary venv under `%LOCALAPPDATA%\Temp`. It created the venv with `--system-site-packages`, installed the built wheel, fetched Pycaw 20260927, and verified imports.
- The temporary test venv was removed and its absence confirmed.
- The active `.venv-win` was not overwritten by the install-mode test.

This is a working development bootstrap, not yet an end-user installer or a clean-machine acceptance test. The MSYS2 package manifest remains observational and will need version/pin/update policy before release.


## Per-user Windows pilot installation - 2026-10-30

A local pilot install of Hearth 5.1.0 was created separately from the repository development venv using the UCRT64 bootstrap helper:

- %LOCALAPPDATA%\Programs\Hearth\venv contains the installed wheel environment.
- Launch-Hearth.ps1 and a per-user Start Menu shortcut are installed below the same program root / Start Menu.
- %LOCALAPPDATA%\Hearth remains the separate user data root.
- The program root's ACL is restricted to the current user, SYSTEM, and local Administrators.
- The install reuses the laptop's already provisioned MSYS2 UCRT64 GTK/PyGObject, GLib, COM and psutil runtime packages. This is a laptop pilot, not a clean-machine bootstrap guarantee.

Wheel QA found that GTK's two custom mute-state images were missing from the original wheel. The GTK module now prefers hearth/ui/gtk4/assets/art beside the installed module and falls back to the source checkout's root assets/art. pyproject.toml force-includes root assets/art at that package path. The rebuilt wheel includes valid app-muted@2x.png and app-unmuted@2x.png; the installed package probe confirmed both files resolve from the installed venv.

Verification after the correction:

- Installed hearth --version reports 5.1.0; pip check is clean.
- Read-only hearth --status enumerates active Windows audio services/endpoints without modifying them.
- Hidden installed-wheel Mixer GUI smoke passed, including five mapped strips, the bundled artwork check, slider bounds, unsupported-action guards, and clean meter/collector shutdown.
- Hidden installed-wheel Setup/Remap smoke passed; Linux-only operations and LPD8/Moonlight actions remained disabled and no mapping writes occurred.
- Full Windows non-GUI suite: 393 passed, 85 skipped, 56 deselected. Ruff lint/format, strict MyPy (40 source files), compileall, and Git whitespace checks passed.

This does not constitute a signed end-user installer, clean-machine test, or finished update/uninstall product workflow. The MSYS2 package manifest still needs pinning and a clean-machine/release strategy. The Start Menu shortcut target was checked, but a visible-window user acceptance test was not performed. Windows routing graph and audio signal-path behavior remain unverified; no audio setters, default endpoint changes, drivers, or services were modified.


## Current pilot verification - 2026-10-30

The current Windows checkout was verified on the laptop's MSYS2 UCRT64 Python 3.14 environment:

- Full Windows suite: **397 passed, 141 skipped**. Skips are platform-specific POSIX/Linux paths and GUI cases requiring a visible test display.
- Focused Windows theme, Core Audio, filesystem/ACL and IPC tests: **47 passed**.
- Ruff: clean. MyPy: no issues in 40 source files.
- Windows registry integration reads dark app mode and the configured accent #e90082, with accent-on-title-bars enabled. The native DWM titlebar callback runs after GTK maps its HWND and now records whether DWM accepted the requested attributes.
- The shared mixer fader adapter reaches Windows Core Audio endpoint volume control; a same-value write/readback was verified against vm_game without changing its audible state.
- The running Windows pilot exposes four mapped playback endpoints and two mapped capture endpoints. This inventory is not proof of a complete virtual mixer, per-app route reassignment, microphone signal, or end-to-end audio graph.

The interactive Hearth process that was open during this audit started before the updated Windows theme files were installed. A graceful close/relaunch is required before the visible window can load the Windows accent palette and DWM caption hook.
