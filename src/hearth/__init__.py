"""Hearth - GTK control panel for a PipeWire virtual-mixer audio rig.

The version here is the only place the version is written down. Packaging
metadata reads it via hatchling, and the CLI reads it via importlib.metadata
when installed, falling back to this constant when run from a checkout.
"""

__all__ = ["APP_ID", "APP_NAME", "__version__"]

__version__ = "5.1.0"

#: Reverse-DNS application ID. Used for the desktop entry, the AppStream
#: metainfo file, the icon name and the GTK application id, which is what lets
#: Plasma match a running window to its launcher.
APP_ID = "io.github.roaring1.Hearth"

#: Short name used for config/state directories and the console command.
APP_NAME = "hearth"
