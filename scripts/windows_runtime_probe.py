from __future__ import annotations

import importlib
import importlib.metadata
import sys


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit(f"expected Windows Python, got {sys.platform!r}")

    gi = importlib.import_module("gi")
    gi.require_version("Gtk", "4.0")
    gtk_repository = __import__("gi.repository", fromlist=["Gtk"])
    gtk = gtk_repository.Gtk

    for module_name in (
        "comtypes",
        "psutil",
        "pycaw.pycaw",
        "hearth.windows_status",
    ):
        importlib.import_module(module_name)

    print("python=" + sys.executable)
    print("gtk=" + str(gtk.get_major_version()) + "." + str(gtk.get_minor_version()))
    try:
        hearth_version = importlib.metadata.version("hearth")
    except importlib.metadata.PackageNotFoundError:
        hearth_version = "source checkout (not installed)"
    print("hearth=" + hearth_version)
    print("pycaw=" + importlib.metadata.version("pycaw"))
    print("comtypes=" + importlib.metadata.version("comtypes"))
    print("psutil=" + importlib.metadata.version("psutil"))
    print("Windows GTK/Core Audio imports succeeded; no audio devices were queried.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
