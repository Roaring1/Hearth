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
    Icon=hearth in the desktop entry resolves.

example-config.json
    Template for ~/.config/hearth/config.json -- the machine-specific values
    (sink names, network-audio labels, SSH target). Every key is optional.
    Copy it, fill in what applies, and leave the rest blank.

example-rac_settings.json
    Template for ~/.config/hearth/rac_settings.json, the UI preferences file
    Hearth writes itself. Safe to copy as a starting point.

Not included, by design: a populated config.json. It holds host details for
the machines on the author's LAN and would be of no use on another rig.
