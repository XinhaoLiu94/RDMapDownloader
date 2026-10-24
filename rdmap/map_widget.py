"""基于 QPainter 的自绘瓦片地图控件。

功能：
* 高德矢量 / 影像 / 路网瓦片底图；
* 鼠标拖拽平移、滚轮以光标为中心缩放；
* 经纬度框选、实时预览、网格显示；
* 内存 + 磁盘两级瓦片缓存，异步网络加载。
"""

from __future__ import annotations

import math
import os
import shutil
import time
from collections import OrderedDict, deque
from functools import partial
from pathlib import Path

from PyQt5.QtCore import (
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QUrl,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt5.QtWidgets import QSizePolicy, QWidget

from . import tile_math as tm
from .tile_images import compose_tile
from .theme import COLORS
from .config import (
    DEFAULT_LATITUDE,
    DEFAULT_LONGITUDE,
    DEFAULT_ZOOM,
    MIN_MAP_ZOOM,
)
from .paths import tile_cache_dir
from .providers import (
    DEFAULT_PROVIDER_KEY,
    PROVIDER_MAP,
    provider_by_key,
    tile_headers,
)

TILE = tm.TILE_SIZE


class TileMapWidget(QWidget):
    """一个轻量的 Web Mercator 瓦片地图控件。"""

    positionChanged = pyqtSignal(float, float)  # lat, lng
    zoomChanged = pyqtSignal(int)

    selectionModeChanged = pyqtSignal(bool)
    selectionChanged = pyqtSignal(float, float, float, float)  # north, south, west, east
    selectionCleared = pyqtSignal()

    MEMORY_CACHE_LIMIT = 256
    MAX_INFLIGHT = 10
    FAILED_RETRY_SECONDS = 30.0

    def __init__(self, parent: QWidget | None = None, cache_dir: str | Path | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(360, 260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.OpenHandCursor)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

        self._provider = provider_by_key(DEFAULT_PROVIDER_KEY)
        self._center_lat = float(DEFAULT_LATITUDE)
        self._center_lng = float(DEFAULT_LONGITUDE)
        self._zoom = int(DEFAULT_ZOOM)
        self._grid_visible = False

        self._cache_dir = Path(cache_dir) if cache_dir else tile_cache_dir()
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        self._mem_cache: OrderedDict[tuple[str, int, int, int], QPixmap] = OrderedDict()
        self._failed_until: dict[tuple[str, int, int, int], float] = {}
        self._queue: deque[tuple[tuple[str, int, int, int], int, str]] = deque()
        self._pending: set[tuple[str, int, int, int]] = set()
        self._inflight: dict[QNetworkReply, tuple[tuple[str, int, int, int], int]] = {}
        self._tile_parts: dict[tuple[str, int, int, int], list[bytes | None]] = {}
        self._api_key = ""
        self._active = True

        self._nam = QNetworkAccessManager(self)
        try:
            self._nam.setTransferTimeout(20000)
        except AttributeError:  # pragma: no cover - Qt < 5.15
            pass

        # 框选状态
        self._selection_mode = False
        self._first_point: tuple[float, float] | None = None
        self._preview_point: tuple[float, float] | None = None
        self._bounds: tuple[float, float, float, float] | None = None

        # 鼠标状态
        self._mouse_pos: QPoint | None = None
        self._press_pos: QPoint | None = None
        self._last_pos: QPoint | None = None
        self._panning = False
        self._did_drag = False

    # ------------------------------------------------------------------
    # 公共属性 / 接口
    # ------------------------------------------------------------------
    @property
    def zoom(self) -> int:
        return self._zoom

    @property
    def center_latitude(self) -> float:
        return self._center_lat

    @property
    def center_longitude(self) -> float:
        return self._center_lng

    @property
    def provider(self):
        return self._provider

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    @property
    def selection_bounds(self) -> tuple[float, float, float, float] | None:
        return self._bounds

    @property
    def selection_mode(self) -> bool:
        return self._selection_mode

    def set_provider(self, key: str) -> None:
        provider = provider_by_key(key)
        if provider == self._provider:
            return
        self._provider = provider
        self.set_zoom(self._zoom)
        self.update()

    def set_active(self, active: bool) -> None:
        """隐藏/关闭视图时停止预览请求，下载任务由独立 worker 管理。"""
        self._active = bool(active)
        if not self._active:
            self._queue.clear()
            self._pending.clear()
            self._tile_parts.clear()
            replies = list(self._inflight)
            self._inflight.clear()
            for reply in replies:
                reply.abort()
        else:
            self.update()

    def set_api_key(self, api_key: str) -> None:
        if self._api_key == api_key.strip():
            return
        self._api_key = api_key.strip()
        self._failed_until.clear()
        # 中止旧密钥的请求，避免其失败结果影响新密钥。
        self._queue.clear()
        self._pending.clear()
        self._tile_parts.clear()
        for reply in list(self._inflight):
            self._inflight.pop(reply, None)
            reply.abort()
        self.update()

    def set_grid_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._grid_visible:
            return
        self._grid_visible = visible
        self.update()

    def set_zoom(self, zoom: int) -> None:
        zoom = int(max(MIN_MAP_ZOOM, min(self._provider.max_zoom, int(zoom))))
        if zoom == self._zoom:
            return
        self._zoom = zoom
        self.update()
        self.zoomChanged.emit(zoom)

    def set_center(self, latitude: float, longitude: float) -> None:
        self._center_lat = tm.clamp_latitude(latitude)
        self._center_lng = tm.normalize_longitude(longitude)
        self.update()

    def reset_view(self) -> None:
        self._center_lat = float(DEFAULT_LATITUDE)
        self._center_lng = float(DEFAULT_LONGITUDE)
        changed = self._zoom != int(DEFAULT_ZOOM)
        self._zoom = int(DEFAULT_ZOOM)
        self.update()
        if changed:
            self.zoomChanged.emit(self._zoom)

    def visible_bounds(self) -> tuple[float, float, float, float]:
        return tm.visible_bounds(
            self._center_lat,
            self._center_lng,
            self._zoom,
            max(1, self.width()),
            max(1, self.height()),
        )

    def show_bounds(
        self,
        north: float,
        south: float,
        west: float,
        east: float,
        zoom: int | None = None,
    ) -> None:
        """把给定经纬度范围移到视野中央（可同时指定级别）。"""
        if north < south:
            north, south = south, north
        if west > east:
            west, east = east, west
        if zoom is not None:
            self._zoom = int(max(MIN_MAP_ZOOM, min(self._provider.max_zoom, int(zoom))))
        self._center_lat = tm.clamp_latitude((north + south) / 2.0)
        if west <= east:
            center_lng = (west + east) / 2.0
        else:  # pragma: no cover - show_bounds 内部已交换
            center_lng = tm.normalize_longitude((west + 360.0 + east) / 2.0)
        self._center_lng = tm.normalize_longitude(center_lng)
        self.update()
        self.zoomChanged.emit(self._zoom)

    # ------------------------------------------------------------------
    # 框选
    # ------------------------------------------------------------------
    def start_selection(self) -> None:
        self._selection_mode = True
        self._bounds = None
        self._first_point = None
        self._preview_point = None
        self.setCursor(Qt.CrossCursor)
        self.setFocus()
        self.selectionModeChanged.emit(True)
        self.update()

    def clear_selection(self) -> None:
        had_selection = self._bounds is not None
        was_selecting = self._selection_mode or self._first_point is not None
        self._selection_mode = False
        self._first_point = None
        self._preview_point = None
        self._bounds = None
        self.setCursor(Qt.OpenHandCursor)
        self.update()
        if had_selection:
            self.selectionCleared.emit()
        if was_selecting:
            self.selectionModeChanged.emit(False)

    def set_selection(self, north: float, south: float, west: float, east: float) -> None:
        """外部（例如数值输入框）设置框选范围，不触发 selectionChanged。"""
        self._bounds = (
            tm.clamp_latitude(north),
            tm.clamp_latitude(south),
            float(west),
            float(east),
        )
        self._first_point = None
        self._preview_point = None
        was_selecting = self._selection_mode
        self._selection_mode = False
        self.setCursor(Qt.OpenHandCursor)
        self.update()
        if was_selecting:
            self.selectionModeChanged.emit(False)

    # ------------------------------------------------------------------
    # 坐标换算
    # ------------------------------------------------------------------
    def _view_origin(self) -> tuple[float, float]:
        center_x, center_y = tm.latlng_to_pixel(self._center_lat, self._center_lng, self._zoom)
        return center_x - self.width() / 2.0, center_y - self.height() / 2.0

    def _screen_to_latlng(self, pos: QPoint) -> tuple[float, float]:
        left, top = self._view_origin()
        lat, lng = tm.pixel_to_latlng(left + pos.x(), top + pos.y(), self._zoom)
        return tm.clamp_latitude(lat), tm.normalize_longitude(lng)

    def _latlng_to_screen(self, lat: float, lng: float) -> tuple[float, float]:
        left, top = self._view_origin()
        x, y = tm.latlng_to_pixel(lat, lng, self._zoom)
        return x - left, y - top

    def _pan_by(self, dx: float, dy: float) -> None:
        center_x, center_y = tm.latlng_to_pixel(self._center_lat, self._center_lng, self._zoom)
        center_x -= dx
        center_y -= dy
        world = tm.world_size(self._zoom)
        center_y = tm.clamp(center_y, 0.0, world)
        lat, lng = tm.pixel_to_latlng(center_x, center_y, self._zoom)
        self._center_lat = tm.clamp_latitude(lat)
        self._center_lng = tm.normalize_longitude(lng)
        self.update()

    def _zoom_at(self, pos: QPoint, delta: int) -> None:
        new_zoom = max(MIN_MAP_ZOOM, min(self._provider.max_zoom, self._zoom + delta))
        if new_zoom == self._zoom:
            return
        anchor_lat, anchor_lng = self._screen_to_latlng(pos)
        self._zoom = new_zoom
        anchor_x, anchor_y = tm.latlng_to_pixel(anchor_lat, anchor_lng, new_zoom)
        left = anchor_x - pos.x()
        top = anchor_y - pos.y()
        lat, lng = tm.pixel_to_latlng(
            left + self.width() / 2.0,
            top + self.height() / 2.0,
            new_zoom,
        )
        self._center_lat = tm.clamp_latitude(lat)
        self._center_lng = tm.normalize_longitude(lng)
        self.update()
        self.zoomChanged.emit(new_zoom)

    # ------------------------------------------------------------------
    # 瓦片缓存与网络
    # ------------------------------------------------------------------
    def _tile_key(self, col: int, row: int) -> tuple[str, int, int, int]:
        return self._provider.key, self._zoom, int(col), int(row)

    def _tile_path(self, key: tuple[str, int, int, int]) -> Path:
        provider_key, zoom, col, row = key
        return self._cache_dir / provider_key / str(zoom) / str(col) / f"{row}.png"

    def _remember_pixmap(self, key: tuple[str, int, int, int], pixmap: QPixmap) -> None:
        self._mem_cache[key] = pixmap
        self._mem_cache.move_to_end(key)
        while len(self._mem_cache) > self.MEMORY_CACHE_LIMIT:
            self._mem_cache.popitem(last=False)

    def _pixmap_for(self, key: tuple[str, int, int, int]) -> QPixmap | None:
        cached = self._mem_cache.get(key)
        if cached is not None:
            self._mem_cache.move_to_end(key)
            return cached

        path = self._tile_path(key)
        if path.is_file():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                self._remember_pixmap(key, pixmap)
                return pixmap
            try:
                path.unlink()
            except OSError:
                pass

        retry_at = self._failed_until.get(key)
        now = time.monotonic()
        if retry_at is not None:
            if now < retry_at:
                return None
            self._failed_until.pop(key, None)

        self._enqueue_tile(key)
        return None

    def _enqueue_tile(self, key: tuple[str, int, int, int]) -> None:
        if not self._active:
            return
        if key in self._pending or key in self._mem_cache:
            return
        provider_key, zoom, col, row = key
        try:
            urls = PROVIDER_MAP[provider_key].urls(zoom, col, row, self._api_key)
        except ValueError:
            return  # 缺少 Key 时由主窗口展示填写提示，不发送无效请求。
        self._pending.add(key)
        self._tile_parts[key] = [None] * len(urls)
        self._queue.extend((key, index, url) for index, url in enumerate(urls))
        self._pump_queue()

    def _pump_queue(self) -> None:
        if not self._active:
            return
        while self._queue and len(self._inflight) < self.MAX_INFLIGHT:
            key, layer_index, url = self._queue.popleft()
            if key not in self._tile_parts:
                continue
            if key in self._mem_cache:
                self._pending.discard(key)
                continue
            provider = PROVIDER_MAP.get(key[0])
            if provider is None:
                self._pending.discard(key)
                continue
            _provider_key, zoom, col, row = key
            request = QNetworkRequest(QUrl(url))
            for name, value in tile_headers(col, row, provider.referer).items():
                request.setRawHeader(name.encode("ascii"), value.encode("utf-8"))
            request.setAttribute(QNetworkRequest.FollowRedirectsAttribute, True)
            reply = self._nam.get(request)
            self._inflight[reply] = (key, layer_index)
            reply.finished.connect(partial(self._on_tile_reply, reply))

    def _on_tile_reply(self, reply: QNetworkReply) -> None:
        request_info = self._inflight.pop(reply, None)
        if request_info is None:
            reply.deleteLater()
            return
        error = reply.error()
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        data = reply.readAll()
        reply.deleteLater()

        if request_info is not None and request_info[0] in self._tile_parts:
            key, layer_index = request_info
            loaded = False
            if (
                error == QNetworkReply.NoError
                and status is not None
                and 200 <= int(status) < 300
                and not data.isEmpty()
            ):
                try:
                    # 每层都验证，全部到齐才缓存，避免保存缺少标注的影像。
                    part = compose_tile([bytes(data)])
                    parts = self._tile_parts[key]
                    parts[layer_index] = part
                    if all(p is not None for p in parts):
                        merged = compose_tile(parts)
                        pixmap = QPixmap()
                        pixmap.loadFromData(merged)
                        self._remember_pixmap(key, pixmap)
                        self._save_tile_file(key, merged)
                        self._tile_parts.pop(key, None)
                        self._pending.discard(key)
                    loaded = True
                except ValueError:
                    pass
            if not loaded:
                self._tile_parts.pop(key, None)
                self._pending.discard(key)
                self._failed_until[key] = time.monotonic() + self.FAILED_RETRY_SECONDS
        self._pump_queue()
        self.update()

    def _save_tile_file(self, key: tuple[str, int, int, int], data: bytes) -> None:
        path = self._tile_path(key)
        temp_path = path.with_name(f"{path.name}.{os.getpid()}.part")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_bytes(data)
            os.replace(temp_path, path)
        except OSError:
            try:
                temp_path.unlink()
            except OSError:
                pass

    def clear_cache(self) -> None:
        replies = list(self._inflight)
        self._inflight.clear()
        self._pending.clear()
        self._tile_parts.clear()
        self._queue.clear()
        for reply in replies:
            reply.abort()
        self._mem_cache.clear()
        self._failed_until.clear()
        shutil.rmtree(self._cache_dir, ignore_errors=True)
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.update()

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), QColor(COLORS["surface_container"]))

        width = max(1, self.width())
        height = max(1, self.height())
        left, top = self._view_origin()
        left_i = int(round(left))
        top_i = int(round(top))
        count = 1 << self._zoom

        first_col = int(math.floor(left_i / TILE))
        last_col = int(math.floor((left_i + width - 1) / TILE))
        first_row = int(math.floor(top_i / TILE))
        last_row = int(math.floor((top_i + height - 1) / TILE))

        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        placeholder_pen = QPen(QColor(COLORS["divider"]))
        painter.setPen(placeholder_pen)

        for row in range(first_row, last_row + 1):
            if row < 0 or row >= count:
                continue
            screen_y = row * TILE - top_i
            for col in range(first_col, last_col + 1):
                screen_x = col * TILE - left_i
                tile_rect = QRect(screen_x, screen_y, TILE, TILE)
                if not tile_rect.intersects(self.rect()):
                    continue
                key = self._tile_key(col % count, row)
                pixmap = self._pixmap_for(key)
                if pixmap is not None:
                    painter.drawPixmap(tile_rect, pixmap)
                else:
                    painter.fillRect(tile_rect, QColor(COLORS["surface_container_low"]))
                    painter.drawRect(tile_rect.adjusted(0, 0, -1, -1))

        if self._grid_visible:
            self._paint_grid(
                painter,
                left_i,
                top_i,
                width,
                height,
                first_col,
                last_col,
                first_row,
                last_row,
                count,
            )

        painter.setRenderHint(QPainter.Antialiasing, True)
        self._paint_selection(painter)
        self._paint_crosshair(painter)
        self._paint_scale(painter)
        self._paint_attribution(painter)
        if self._provider.requires_key and not self._api_key:
            card = QRectF(self.width() / 2 - 190, self.height() / 2 - 52, 380, 104)
            painter.setPen(QPen(QColor(COLORS["divider"])))
            painter.setBrush(QColor(COLORS["surface_lowest"]))
            painter.drawRoundedRect(card, 16, 16)
            painter.setPen(QColor(COLORS["on_surface"]))
            font = QFont(self.font())
            font.setPointSize(12)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(card.adjusted(14, 16, -14, -50), Qt.AlignCenter, "连接天地图影像")
            font.setPointSize(9)
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QColor(COLORS["on_surface_variant"]))
            painter.drawText(card.adjusted(14, 48, -14, -16), Qt.AlignCenter, "在右侧填写 Key，加载卫星影像与中文标注。")

    def _paint_grid(
        self,
        painter: QPainter,
        left_i: int,
        top_i: int,
        width: int,
        height: int,
        first_col: int,
        last_col: int,
        first_row: int,
        last_row: int,
        count: int,
    ) -> None:
        world_px = count * TILE
        top_world = max(0, -top_i)
        bottom_world = min(height, world_px - top_i)
        if bottom_world <= top_world:
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, False)
        grid_color = QColor(COLORS["map_accent"])
        grid_color.setAlpha(80)
        pen = QPen(grid_color)
        pen.setWidth(1)
        painter.setPen(pen)

        for col in range(first_col, last_col + 2):
            x = col * TILE - left_i
            painter.drawLine(x, top_world, x, bottom_world)
        for row in range(first_row, last_row + 2):
            y = row * TILE - top_i
            if 0 <= y <= height:
                painter.drawLine(0, y, width, y)
        painter.restore()

    def _selection_bounds_for_paint(self) -> tuple[float, float, float, float] | None:
        if self._bounds is not None:
            return self._bounds
        if self._selection_mode and self._first_point and self._preview_point:
            return self._combine_points(self._first_point, self._preview_point)
        return None

    @staticmethod
    def _combine_points(
        first: tuple[float, float],
        second: tuple[float, float],
    ) -> tuple[float, float, float, float]:
        lat1, lng1 = first
        lat2, lng2 = second
        return max(lat1, lat2), min(lat1, lat2), min(lng1, lng2), max(lng1, lng2)

    def _paint_selection(self, painter: QPainter) -> None:
        bounds = self._selection_bounds_for_paint()
        if bounds is not None:
            north, south, west, east = bounds
            x1, y1 = self._latlng_to_screen(north, west)
            x2, y2 = self._latlng_to_screen(south, east)
            rect = QRectF(
                min(x1, x2),
                min(y1, y2),
                max(1.0, abs(x2 - x1)),
                max(1.0, abs(y2 - y1)),
            )

            is_preview = self._bounds is None
            painter.save()
            selection_color = QColor(COLORS["map_accent"])
            selection_color.setAlpha(24)
            painter.setBrush(selection_color)
            border = QPen(QColor(COLORS["map_accent"]))
            border.setWidthF(1.6)
            border.setStyle(Qt.DashLine if is_preview else Qt.SolidLine)
            painter.setPen(border)
            painter.drawRect(rect)

            if not is_preview:
                painter.setBrush(QColor("#ffffff"))
                handle_pen = QPen(QColor(COLORS["map_accent"]))
                handle_pen.setWidthF(1.4)
                painter.setPen(handle_pen)
                for point in (
                    rect.topLeft(),
                    rect.topRight(),
                    rect.bottomLeft(),
                    rect.bottomRight(),
                ):
                    painter.drawRect(QRectF(point.x() - 3.5, point.y() - 3.5, 7.0, 7.0))
            painter.restore()

            if rect.width() > 46 and rect.height() > 24 and rect.intersects(QRectF(self.rect())):
                label = (
                    "框选中…"
                    if is_preview
                    else f"{abs(north - south):.4f}° × {abs(east - west):.4f}°"
                )
                self._paint_selection_label(painter, rect, label)

        if self._first_point is not None and self._bounds is None:
            lat, lng = self._first_point
            x, y = self._latlng_to_screen(lat, lng)
            painter.save()
            painter.setPen(QPen(QColor("#ffffff"), 2.0))
            painter.setBrush(QColor(COLORS["map_accent"]))
            painter.drawEllipse(QPointF(x, y), 5.0, 5.0)
            painter.restore()

    def _paint_selection_label(self, painter: QPainter, rect: QRectF, text: str) -> None:
        font = QFont(self.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() - 1.0))
        font.setBold(True)
        metrics = QFontMetrics(font)
        text_width = metrics.horizontalAdvance(text) + 14
        text_height = metrics.height() + 4
        x = min(max(6.0, rect.left() + 8.0), max(6.0, self.width() - text_width - 6.0))
        y = min(max(6.0, rect.top() + 8.0), max(6.0, self.height() - text_height - 6.0))
        label_rect = QRectF(x, y, text_width, text_height)

        painter.save()
        painter.setFont(font)
        path = QPainterPath()
        path.addRoundedRect(label_rect, 4.0, 4.0)
        painter.fillPath(path, QColor(14, 24, 19, 220))
        painter.setPen(QPen(QColor(COLORS["primary_container"]), 1.0))
        painter.drawPath(path)
        painter.setPen(QColor(COLORS["map_on_overlay"]))
        painter.drawText(label_rect, Qt.AlignCenter, text)
        painter.restore()

    def _paint_crosshair(self, painter: QPainter) -> None:
        if self._mouse_pos is None or self._panning:
            return
        if not self.rect().contains(self._mouse_pos):
            return
        painter.save()
        pen = QPen(QColor(71, 85, 105, 110))
        pen.setStyle(Qt.DashLine)
        pen.setWidth(1)
        painter.setPen(pen)
        x = self._mouse_pos.x()
        y = self._mouse_pos.y()
        painter.drawLine(0, y, self.width(), y)
        painter.drawLine(x, 0, x, self.height())
        painter.restore()

    def _paint_scale(self, painter: QPainter) -> None:
        meters, pixels = tm.map_scale(self._center_lat, self._zoom)
        label = f"{meters / 1000:g} km" if meters >= 1000 else f"{meters:g} m"
        x, y = 20, self.height() - getattr(self, "scale_bottom_margin", 62)
        painter.save()
        font = QFont("Consolas")
        font.setPointSizeF(8.5)
        painter.setFont(font)
        painter.setPen(QColor(14, 24, 19))
        painter.drawText(x + 1, y - 8, label)
        painter.setPen(QColor("white"))
        painter.drawText(x, y - 9, label)
        for color, width in ((QColor(14, 24, 19, 200), 4), (QColor("white"), 2)):
            painter.setPen(QPen(color, width))
            painter.drawLine(x, y - 5, x, y)
            painter.drawLine(x, y, x + round(pixels), y)
            painter.drawLine(x + round(pixels), y, x + round(pixels), y - 5)
        painter.restore()

    def _paint_attribution(self, painter: QPainter) -> None:
        font = QFont(self.font())
        font.setPointSizeF(8.0)
        metrics = QFontMetrics(font)
        text = self._provider.attribution
        text_width = metrics.horizontalAdvance(text) + 12
        text_height = metrics.height() + 4
        rect = QRectF(
            self.width() - text_width - 8,
            self.height() - text_height - getattr(self, "attribution_bottom_margin", 8),
            text_width,
            text_height,
        )
        painter.save()
        painter.setFont(font)
        path = QPainterPath()
        path.addRoundedRect(rect, 4.0, 4.0)
        painter.fillPath(path, QColor(14, 24, 19, 205))
        painter.setPen(QColor(COLORS["map_on_overlay"]))
        painter.drawText(rect, Qt.AlignCenter, text)
        painter.restore()

    # ------------------------------------------------------------------
    # 鼠标 / 键盘
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() in (Qt.LeftButton, Qt.MiddleButton):
            self.setFocus()
            self._panning = True
            self._did_drag = False
            self._press_pos = event.pos()
            self._last_pos = event.pos()
            self._mouse_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._mouse_pos = event.pos()
        lat, lng = self._screen_to_latlng(event.pos())
        self.positionChanged.emit(lat, lng)

        if (
            self._panning
            and self._last_pos is not None
            and self._press_pos is not None
            and event.buttons() & (Qt.LeftButton | Qt.MiddleButton)
        ):
            delta = event.pos() - self._last_pos
            if not self._did_drag:
                moved = event.pos() - self._press_pos
                if abs(moved.x()) + abs(moved.y()) >= 4:
                    self._did_drag = True
            if self._did_drag:
                self._pan_by(delta.x(), delta.y())
                self._last_pos = event.pos()

        if self._selection_mode and self._first_point is not None:
            self._preview_point = (lat, lng)

        self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() in (Qt.LeftButton, Qt.MiddleButton) and self._panning:
            was_click = not self._did_drag
            self._panning = False
            self._did_drag = False
            self._last_pos = None
            self._press_pos = None
            self.setCursor(Qt.CrossCursor if self._selection_mode else Qt.OpenHandCursor)
            if was_click and event.button() == Qt.LeftButton and self._selection_mode:
                self._pick_point(event.pos())
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and not self._selection_mode:
            self._zoom_at(event.pos(), 1)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta:
            self._zoom_at(event.pos(), 1 if delta > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key_Escape:
            self.clear_selection()
        elif key in (Qt.Key_Plus, Qt.Key_Equal):
            self.set_zoom(self._zoom + 1)
        elif key == Qt.Key_Minus:
            self.set_zoom(self._zoom - 1)
        elif key == Qt.Key_Left:
            self._pan_by(-64, 0)
        elif key == Qt.Key_Right:
            self._pan_by(64, 0)
        elif key == Qt.Key_Up:
            self._pan_by(0, -64)
        elif key == Qt.Key_Down:
            self._pan_by(0, 64)
        elif key == Qt.Key_Home:
            self.reset_view()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._mouse_pos = None
        self.update()
        super().leaveEvent(event)

    def _pick_point(self, pos: QPoint) -> None:
        lat, lng = self._screen_to_latlng(pos)
        point = (lat, lng)
        if self._first_point is None:
            self._first_point = point
            self._preview_point = point
            self.update()
            return

        north, south, west, east = self._combine_points(self._first_point, point)
        self._bounds = (north, south, west, east)
        self._first_point = None
        self._preview_point = None
        self._selection_mode = False
        self.setCursor(Qt.OpenHandCursor)
        self.selectionModeChanged.emit(False)
        self.selectionChanged.emit(north, south, west, east)
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(920, 720)
