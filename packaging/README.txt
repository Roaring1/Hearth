Packaging extras (not part of the git repo)
============================================

hearth.desktop      - Desktop launcher entry (paths hardcoded to roaring's
                       primary PC: /home/roaring/bin/hearth.py and the icon
                       below). Edit the Exec=/Icon= paths for the new machine.
hearth.png          - App icon referenced by the .desktop file.
example-rac_settings.json
                    - Example of the local UI-preferences file Hearth writes
                      to ~/.config/hearth/rac_settings.json. Harmless to copy
                      as a starting point.

Deliberately NOT included: ~/.config/hearth/config.json. It stores an SSH
host/user/key-path for a remote machine on this LAN and isn't relevant to
debugging Hearth's own code on a second PC.

Install on the second PC:
  git clone git@github.com:Roaring1/Hearth.git
  # or: git clone https://github.com/Roaring1/Hearth.git
  cd Hearth
  python3 hearth.py            # run directly, or
  mkdir -p ~/bin && ln -s "$(pwd)/hearth.py" ~/bin/hearth.py
  # optionally install packaging/hearth.desktop into
  # ~/.local/share/applications/ (after fixing its paths) and
  # packaging/hearth.png into ~/.local/share/icons/hicolor/256x256/apps/
