"""Static checks for the small set of original SVG action glyphs."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).parents[1]
ICON_DIR = ROOT / "src/hearth/ui/gtk4/art"
EXPECTED = {"collapse.svg", "refresh.svg", "save.svg", "sliders.svg"}
SVG_NS = "http://www.w3.org/2000/svg"


def test_action_icon_set_is_explicit_and_complete() -> None:
    files = {p.name for p in ICON_DIR.glob("*.svg")}
    assert files == EXPECTED


def test_action_svgs_are_valid_simple_hand_authored_vectors() -> None:
    """Only primitive vector shapes are allowed; no raster/embedded artwork."""
    allowed_tags = {
        f"{{{SVG_NS}}}{tag}" for tag in ("svg", "path", "circle", "rect", "line", "polyline")
    }
    for name in sorted(EXPECTED):
        root = ET.parse(ICON_DIR / name).getroot()  # noqa: S314 - bundled, reviewed source SVGs
        assert root.tag == f"{{{SVG_NS}}}svg"
        assert root.attrib.get("viewBox") == "0 0 24 24"
        assert root.attrib.get("width") == "24"
        assert root.attrib.get("height") == "24"
        assert all(element.tag in allowed_tags for element in root.iter())
        assert root.find(f"{{{SVG_NS}}}path") is not None or len(root) > 0
        assert not any(element.tag.endswith("image") for element in root.iter())
        assert not any("href" in key for element in root.iter() for key in element.attrib)


def test_action_icon_runtime_allowlist_matches_svg_assets() -> None:
    source = (ROOT / "src/hearth/ui/gtk4/action_icons.py").read_text()
    for name in (p.removesuffix(".svg") for p in EXPECTED):
        assert f'"{name}"' in source
