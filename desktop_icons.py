"""Verified Reicon Outline assets, rendered locally at consistent DPI sizes."""
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from desktop_theme import ICON_SIZE, MUTED

ICON_NAMES = frozenset(("calendar", "tasks", "chart", "display", "settings", "previous", "next", "play", "pause"))
ASSET_DIRECTORY = Path(__file__).resolve().parent / "assets" / "reicon"


@lru_cache(maxsize=32)
def ui_icon(name, color=MUTED):
    if name not in ICON_NAMES:
        raise ValueError(f"Unknown UI icon: {name}")
    svg = (ASSET_DIRECTORY / (name + ".svg")).read_bytes().replace(b"currentColor", color.encode("ascii"))
    renderer = QSvgRenderer(svg)
    if not renderer.isValid():
        raise RuntimeError(f"Invalid bundled UI icon: {name}")
    icon = QIcon()
    for density in (1, 1.5, 2, 3):
        pixmap = QPixmap(round(ICON_SIZE * density), round(ICON_SIZE * density))
        pixmap.setDevicePixelRatio(density)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, ICON_SIZE, ICON_SIZE))
        painter.end()
        icon.addPixmap(pixmap)
    return icon
