Packaging extras
================

These files are part of the repository and are meant to be installed by hand;
see the Install section of ../README.md.

io.github.roaring1.Hearth.desktop
    Desktop launcher entry. Uses Exec=hearth, so it expects a `hearth`
    executable on PATH -- create one with:
        mkdir -p ~/.local/bin && ln -sf "$PWD/hearth.py" ~/.local/bin/hearth
    Install to ~/.local/share/applications/ and run
    `update-desktop-database ~/.local/share/applications`.

io.github.roaring1.Hearth.png
    App icon. Install to
    ~/.local/share/icons/hicolor/256x256/apps/io.github.roaring1.Hearth.png so the theme name
    Icon=io.github.roaring1.Hearth in the desktop entry resolves.

example-config.json
    Template for ~/.config/hearth/config.json -- the machine-specific values
    (sink names, network-audio labels, SSH target). Every key is optional.
    Copy it, fill in what applies, and leave the rest blank.

example-rac_settings.json
    Template for ~/.config/hearth/rac_settings.json, the UI preferences file
    Hearth writes itself. Safe to copy as a starting point.

Runtime and private data
    Hearth keeps its IPC socket and PID fallback under the private
    XDG_RUNTIME_DIR/hearth directory (or a per-UID temporary fallback when
    XDG_RUNTIME_DIR is absent/invalid). The socket and PID file are created
    with private permissions. Hearth refuses to replace a non-socket IPC path
    or a non-regular PID path, and its PID file is written by atomic replace.
    If the IPC listener cannot start, Hearth closes its newly created socket
    and removes only that socket's inode, then continues without IPC rather
    than leaving a stale path that blocks the next launch.

    Configuration, state, cache and dump directories are restricted to mode
    0700, and preference/settings, log and status-dump files are written with
    mode 0600. The diagnostic bundle script also uses a private umask because
    its output includes a host/config snapshot. Existing directories are
    tightened on startup; existing dump contents are not deleted.

Not included, by design: a populated config.json. It holds host details for
the machines on the author's LAN and would be of no use on another rig.
