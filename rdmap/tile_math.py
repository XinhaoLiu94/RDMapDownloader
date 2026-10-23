"""Web Mercator 投影与 XYZ 瓦片编号计算。

坐标约定：
* 输入经纬度与高德底图一致（GCJ-02），投影采用 Web Mercator（EPSG:3857）；
* 瓦片编号采用 Google / XYZ 规则：原点在左上角，行向下递增；
* 单个瓦片 256 × 256 像素。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterator

TILE_SIZE = 256
MAX_LATITUDE = 85.0511287798066

MIN_ZOOM = 0
MAX_ZOOM = 30


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def clamp_latitude(latitude: float) -> float:
    return clamp(float(latitude), -MAX_LATITUDE, MAX_LATITUDE)


def normalize_longitude(longitude: float) -> float:
    """把经度规整到 [-180, 180)。"""
    wrapped = math.fmod(float(longitude) + 180.0, 360.0)
    if wrapped < 0.0:
        wrapped += 360.0
    return wrapped - 180.0


def _validate_zoom(zoom: int) -> int:
    if isinstance(zoom, bool) or not isinstance(zoom, int):
        raise TypeError("zoom 必须是整数")
    if not MIN_ZOOM <= zoom <= MAX_ZOOM:
        raise ValueError(f"zoom 超出范围：{zoom}")
    return zoom


def tile_count(zoom: int) -> int:
    """某个级别每行/每列的瓦片数，即 2^zoom。"""
    return 1 << _validate_zoom(zoom)


def world_size(zoom: int) -> float:
    """某个级别整张世界地图的像素宽度。"""
    return float(TILE_SIZE * tile_count(zoom))


def lon_to_pixel_x(longitude: float, zoom: int) -> float:
    return (float(longitude) + 180.0) / 360.0 * world_size(zoom)


def lat_to_pixel_y(latitude: float, zoom: int) -> float:
    lat = math.radians(clamp_latitude(latitude))
    return (1.0 - math.asinh(math.tan(lat)) / math.pi) / 2.0 * world_size(zoom)


def pixel_x_to_lon(pixel_x: float, zoom: int) -> float:
    return float(pixel_x) / world_size(zoom) * 360.0 - 180.0


def pixel_y_to_lat(pixel_y: float, zoom: int) -> float:
    world = world_size(zoom)
    y = clamp(float(pixel_y), 0.0, world)
    return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / world))))


def latlng_to_pixel(latitude: float, longitude: float, zoom: int) -> tuple[float, float]:
    """经纬度 -> 世界像素坐标。经度不做规整，便于跨 180° 计算。"""
    return lon_to_pixel_x(longitude, zoom), lat_to_pixel_y(latitude, zoom)


def pixel_to_latlng(pixel_x: float, pixel_y: float, zoom: int) -> tuple[float, float]:
    """世界像素坐标 -> 经纬度（纬度限制在 Web Mercator 有效范围内）。"""
    return pixel_y_to_lat(pixel_y, zoom), pixel_x_to_lon(pixel_x, zoom)


def latlng_to_tile(latitude: float, longitude: float, zoom: int) -> tuple[int, int]:
    """经纬度 -> (列 col, 行 row)，并限制到该级别的有效瓦片范围内。"""
    count = tile_count(zoom)
    col = int(math.floor(lon_to_pixel_x(longitude, zoom) / TILE_SIZE))
    row = int(math.floor(lat_to_pixel_y(latitude, zoom) / TILE_SIZE))
    return int(clamp(col, 0, count - 1)), int(clamp(row, 0, count - 1))


def tile_to_latlng(col: int, row: int, zoom: int) -> tuple[float, float]:
    """瓦片左上角经纬度。"""
    return pixel_to_latlng(col * TILE_SIZE, row * TILE_SIZE, zoom)


def tile_center_latlng(col: int, row: int, zoom: int) -> tuple[float, float]:
    """瓦片中心点经纬度。"""
    return pixel_to_latlng(
        (col + 0.5) * TILE_SIZE,
        (row + 0.5) * TILE_SIZE,
        zoom,
    )


@dataclass(frozen=True)
class TileRange:
    """一个矩形瓦片范围（含边界）。"""

    zoom: int
    min_row: int
    max_row: int
    min_col: int
    max_col: int

    def __post_init__(self) -> None:
        _validate_zoom(self.zoom)
        for name in ("min_row", "max_row", "min_col", "max_col"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} 必须是整数")
            if value < 0:
                raise ValueError(f"{name} 不能为负数")
        if self.min_row > self.max_row:
            raise ValueError("最小行不能大于最大行")
        if self.min_col > self.max_col:
            raise ValueError("最小列不能大于最大列")

    @property
    def rows(self) -> int:
        return self.max_row - self.min_row + 1

    @property
    def cols(self) -> int:
        return self.max_col - self.min_col + 1

    @property
    def count(self) -> int:
        return self.rows * self.cols

    @property
    def is_within_world(self) -> bool:
        limit = tile_count(self.zoom) - 1
        return self.max_row <= limit and self.max_col <= limit

    @property
    def pixel_width(self) -> int:
        return self.cols * TILE_SIZE

    @property
    def pixel_height(self) -> int:
        return self.rows * TILE_SIZE

    def contains(self, col: int, row: int) -> bool:
        return self.min_col <= col <= self.max_col and self.min_row <= row <= self.max_row

    def iter_tiles(self) -> Iterator[tuple[int, int]]:
        """按行优先顺序产出 (col, row)。"""
        for row in range(self.min_row, self.max_row + 1):
            for col in range(self.min_col, self.max_col + 1):
                yield col, row

    def as_dict(self) -> dict[str, int]:
        return {
            "zoom": self.zoom,
            "min_row": self.min_row,
            "max_row": self.max_row,
            "min_col": self.min_col,
            "max_col": self.max_col,
        }


def range_from_bounds(
    north: float,
    south: float,
    west: float,
    east: float,
    zoom: int,
) -> TileRange:
    """由经纬度范围计算覆盖该范围的瓦片范围。

    若北/南或西/东顺序颠倒会自动交换。跨 180° 经线的范围不做拆分处理。
    """
    values = (north, south, west, east)
    if any(not math.isfinite(float(value)) for value in values):
        raise ValueError("经纬度必须是有限数值")
    if north < south:
        north, south = south, north
    if west > east:
        west, east = east, west

    count = tile_count(zoom)
    min_col = int(math.floor(lon_to_pixel_x(west, zoom) / TILE_SIZE))
    max_col = int(math.floor(lon_to_pixel_x(east, zoom) / TILE_SIZE))
    min_row = int(math.floor(lat_to_pixel_y(north, zoom) / TILE_SIZE))
    max_row = int(math.floor(lat_to_pixel_y(south, zoom) / TILE_SIZE))

    min_col = int(clamp(min_col, 0, count - 1))
    max_col = int(clamp(max_col, 0, count - 1))
    min_row = int(clamp(min_row, 0, count - 1))
    max_row = int(clamp(max_row, 0, count - 1))

    return TileRange(
        zoom=zoom,
        min_row=min(min_row, max_row),
        max_row=max(min_row, max_row),
        min_col=min(min_col, max_col),
        max_col=max(min_col, max_col),
    )


def range_from_rows_cols(
    zoom: int,
    min_row: int,
    max_row: int,
    min_col: int,
    max_col: int,
) -> TileRange:
    """由手动填写的行/列范围构造，顺序颠倒时抛出 ValueError。"""
    return TileRange(
        zoom=zoom,
        min_row=min_row,
        max_row=max_row,
        min_col=min_col,
        max_col=max_col,
    )


def visible_bounds(
    center_latitude: float,
    center_longitude: float,
    zoom: int,
    width: int,
    height: int,
) -> tuple[float, float, float, float]:
    """当前视口覆盖的经纬度范围 (north, south, west, east)。"""
    center_x, center_y = latlng_to_pixel(center_latitude, center_longitude, zoom)
    width = max(1, int(width))
    height = max(1, int(height))
    left = center_x - width / 2.0
    right = center_x + width / 2.0
    top = center_y - height / 2.0
    bottom = center_y + height / 2.0

    north = pixel_y_to_lat(top, zoom)
    south = pixel_y_to_lat(bottom, zoom)
    west = normalize_longitude(pixel_x_to_lon(left, zoom))
    east = normalize_longitude(pixel_x_to_lon(right, zoom))
    return north, south, west, east


def zoom_to_fit(
    north: float,
    south: float,
    west: float,
    east: float,
    width: int,
    height: int,
    padding: int = 48,
) -> int:
    """选择一个能把给定范围完整放进视口的最大级别。"""
    if north < south:
        north, south = south, north
    if west > east:
        west, east = east, west
    usable_width = max(32, int(width) - padding * 2)
    usable_height = max(32, int(height) - padding * 2)
    for zoom in range(MAX_ZOOM, MIN_ZOOM - 1, -1):
        x1, y1 = latlng_to_pixel(north, west, zoom)
        x2, y2 = latlng_to_pixel(south, east, zoom)
        if abs(x2 - x1) <= usable_width and abs(y2 - y1) <= usable_height:
            return zoom
    return MIN_ZOOM


def format_bounds(north: float, south: float, west: float, east: float) -> str:
    """可读的范围描述。"""
    return (
        f"北 {north:.6f}° / 南 {south:.6f}° / "
        f"西 {west:.6f}° / 东 {east:.6f}°"
    )
