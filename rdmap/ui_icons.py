"""与当前主题匹配的矢量图标和图层示意图，无外部资源请求。"""
from PyQt5.QtCore import QByteArray, Qt
from PyQt5.QtGui import QIcon, QPainter, QPixmap
from PyQt5.QtSvg import QSvgRenderer
from .theme import COLORS

_PATHS = {
    "reset": '<path d="M3 11a9 9 0 1 0 3-6L3 8M3 3v5h5"/>',
    "select": '<path d="M4 8V5a1 1 0 0 1 1-1h3m8 0h3a1 1 0 0 1 1 1v3m0 8v3a1 1 0 0 1-1 1h-3m-8 0H5a1 1 0 0 1-1-1v-3"/>',
    "locate": '<circle cx="12" cy="12" r="5"/><path d="M12 2v5m0 10v5M2 12h5m10 0h5"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "download": '<path d="M12 4v11m-5-4 5 5 5-5M5 20h14"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
    "moon": '<path d="M20.7 13.3A9 9 0 0 1 10.7 3.3 9 9 0 1 0 20.7 13.3z"/>',
    "system": '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>',
}

def svg_pixmap(svg: str, width: int, height: int) -> QPixmap:
    pixmap = QPixmap(width * 2, height * 2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return pixmap

def icon(name: str, color: str | None = None) -> QIcon:
    color = color or COLORS["on_surface_variant"]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
           f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
           + _PATHS[name] + '</svg>')
    return QIcon(svg_pixmap(svg, 20, 20))

def brand_pixmap() -> QPixmap:
    squares = ''.join(f'<rect x="{x}" y="{y}" width="7" height="7" rx="1.5" '
                      f'fill="{COLORS["on_primary"]}" opacity="{opacity}"/>'
                      for x, y, opacity in ((6.5, 6.5, 1), (14.5, 6.5, .5), (6.5, 14.5, .5), (14.5, 14.5, 1)))
    return svg_pixmap(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28">'
                      f'<rect width="28" height="28" rx="8" fill="{COLORS["primary"]}"/>{squares}</svg>', 28, 28)

def layer_icon(kind: str) -> QIcon:
    if kind in ("satellite", "hybrid"):
        background = '<defs><linearGradient id="terrain"><stop stop-color="#243620"/><stop offset="1" stop-color="#5D6B45"/></linearGradient></defs><rect width="120" height="38" rx="6" fill="url(#terrain)"/><path d="M0 18 30 6 62 14 95 2 120 9v10l-25 6-30-5L30 35 0 29z" fill="#44543A"/>'
    else:
        background = f'<rect width="120" height="38" rx="6" fill="{COLORS["surface_container_high"]}"/><path d="M0 26Q60 14 120 10" fill="none" stroke="{COLORS["muted"]}" stroke-width="3"/><path d="M44 0 64 38" stroke="#C9A04A" stroke-width="3"/>'
    roads = '<path d="M0 30Q28 10 58 23T120 7" fill="none" stroke="#F2A65A" stroke-width="2"/>'
    labels = '<rect x="69" y="24" width="30" height="4" rx="2" fill="#FFFFFF" opacity=".85"/>'
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 38">{background}'
    if kind in ("hybrid", "road"): svg += roads + labels
    return QIcon(svg_pixmap(svg + '</svg>', 104, 36))
