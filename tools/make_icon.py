"""从 assets/icon.svg 生成多尺寸 assets/icon.ico。

开发辅助脚本，需要 Pillow：
    python -m pip install Pillow
    python tools/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtSvg import QSvgRenderer
from PIL import Image

ICON_SIZES = (256, 128, 64, 48, 32, 16)


def render_png(svg_path: Path, output_path: Path, size: int = 256) -> None:
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise RuntimeError(f"无法读取 SVG：{svg_path}")
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    if not image.save(str(output_path), "PNG"):
        raise RuntimeError(f"无法写入 PNG：{output_path}")


def main() -> int:
    svg_path = ROOT / "assets" / "icon.svg"
    png_path = ROOT / "assets" / "icon.png"
    ico_path = ROOT / "assets" / "icon.ico"

    render_png(svg_path, png_path, 256)
    source = Image.open(png_path).convert("RGBA")
    source.save(ico_path, format="ICO", sizes=[(size, size) for size in ICON_SIZES])
    print(f"已生成 {png_path} 和 {ico_path} ({ico_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
