Install data
============

These files are part of the repository and are meant to be installed by hand;
see the Install section of ../README.md.

io.github.roaring1.Hearth.desktop
    Main Hearth launcher. Uses Exec=hearth and expects a `hearth` executable
    on PATH -- create one with:
        mkdir -p ~/.local/bin && ln -sf "$PWD/hearth.py" ~/.local/bin/hearth

io.github.roaring1.Hearth.Capture.desktop
    Separate **Use Capture Card** launcher. Opens the read-only Capture
    Controls window in the existing Hearth instance when available, or starts
    Hearth directly into Controls. Opening performs one bounded, read-only
    USB video-device inventory; Refresh explicitly repeats it. It does not
    start video, query PipeWire, or change audio. Start/Stop are not available
    until the production session and audio-route cleanup path are connected.

Install both launchers to ~/.local/share/applications/ and run:
    update-desktop-database ~/.local/share/applications

io.github.roaring1.Hearth.png
    App icon. Install to
    ~/.local/share/icons/hicolor/256x256/apps/io.github.roaring1.Hearth.png so
    the theme name Icon=io.github.roaring1.Hearth in the desktop entries resolves.

example-config.json
    Template for ~/.config/hearth/config.json. Machine-specific configuration
    remains outside the source tree.

Runtime and private data
    Hearth keeps its IPC socket and PID fallback under the private
    XDG_RUNTIME_DIR/hearth directory (or a per-UID temporary fallback when
    XDG_RUNTIME_DIR is absent/invalid). The socket and PID file use private
    permissions. Hearth refuses to replace a non-socket IPC path or a
    non-regular PID path, and writes the PID file with atomic replace. If the
    IPC listener cannot start, Hearth closes its new socket and removes only
    that socket's inode, then continues without IPC.

    Configuration, state, cache and dump directories are restricted to mode
    0700; preference/settings, log and status-dump files use mode 0600. The
    diagnostic bundle script uses a private umask because its output includes
    a host/config snapshot. Existing directories are tightened on startup;
    existing dump contents are not deleted.

Not included, by design: a populated config.json. It holds host details for
    the author's LAN and would be of no use on another rig.
