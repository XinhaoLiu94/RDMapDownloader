"""地图预览与下载共用的瓦片解码和透明图层合成。"""

from PyQt5.QtCore import QBuffer, QByteArray, QIODevice
from PyQt5.QtGui import QImage, QPainter


def compose_tile(parts: list[bytes]) -> bytes:
    if not parts:
        raise ValueError("瓦片没有图层")
    images = [QImage.fromData(part) for part in parts]
    if any(image.isNull() or image.width() != 256 or image.height() != 256 for image in images):
        raise ValueError("响应不是有效的 256 × 256 瓦片图片")
    if len(parts) == 1:
        return parts[0]
    image = images[0].convertToFormat(QImage.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    try:
        for overlay in images[1:]:
            painter.drawImage(0, 0, overlay)
    finally:
        painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ValueError("瓦片合成失败")
    return bytes(data)
