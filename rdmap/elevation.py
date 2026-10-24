"""Skadi/SRTM（EGM96）采样，以及 EGM96 到 WGS84 椭球高的转换。"""

from __future__ import annotations

from array import array
from contextlib import ExitStack
import math
import mmap
from pathlib import Path
import struct
import sys

from .tile_math import pixel_to_latlng

HEIGHTMAP_SIZE = 65
TERRAIN_START_LEVEL = 9


class GeoidGrid:
    def __init__(self, path: Path):
        raw = path.read_bytes()
        if len(raw) < 40:
            raise ValueError("高程基准格网文件损坏")
        self.latitude, self.longitude, self.dy, self.dx, self.rows, self.cols = (
            struct.unpack(">4d2i", raw[:40])
        )
        if (
            self.rows < 2
            or self.cols < 2
            or self.dx <= 0
            or self.dy <= 0
            or len(raw) != 40 + self.rows * self.cols * 4
        ):
            raise ValueError("高程基准格网尺寸无效")
        self.values = array("f")
        self.values.frombytes(raw[40:])
        if sys.byteorder == "little":
            self.values.byteswap()

    def undulation(self, latitude: float, longitude: float) -> float:
        y = min(self.rows - 1.0, max(0.0, (latitude - self.latitude) / self.dy))
        x = ((longitude - self.longitude) % 360) / self.dx
        x0, y0 = int(math.floor(x)) % self.cols, int(math.floor(y))
        x1, y1 = (x0 + 1) % self.cols, min(y0 + 1, self.rows - 1)
        fx, fy = x - math.floor(x), y - y0
        a = (
            self.values[y0 * self.cols + x0] * (1 - fx)
            + self.values[y0 * self.cols + x1] * fx
        )
        b = (
            self.values[y1 * self.cols + x0] * (1 - fx)
            + self.values[y1 * self.cols + x1] * fx
        )
        value = a * (1 - fy) + b * fy
        if not math.isfinite(value) or abs(value) > 200:
            raise ValueError("高程基准格网值无效")
        return value


def hgt_name(south: int, west: int) -> str:
    return f"{'N' if south >= 0 else 'S'}{abs(south):02d}{'E' if west >= 0 else 'W'}{abs(west):03d}"


class HgtTile:
    def __init__(self, path: Path, south: int, west: int):
        self.south, self.west = south, west
        self.file = path.open("rb")
        length = path.stat().st_size
        self.size = math.isqrt(length // 2)
        if self.size < 2 or self.size * self.size * 2 != length:
            self.file.close()
            raise ValueError("HGT 文件尺寸无效")
        self.data = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)

    def close(self):
        self.data.close()
        self.file.close()

    def sample(self, latitude: float, longitude: float) -> float:
        x = min(self.size - 1.0, max(0.0, (longitude - self.west) * (self.size - 1)))
        y = min(
            self.size - 1.0, max(0.0, (self.south + 1 - latitude) * (self.size - 1))
        )
        x0, y0 = int(x), int(y)
        x1, y1 = min(x0 + 1, self.size - 1), min(y0 + 1, self.size - 1)
        fx, fy = x - x0, y - y0
        total = weight = 0.0
        for col, row, w in (
            (x0, y0, (1 - fx) * (1 - fy)),
            (x1, y0, fx * (1 - fy)),
            (x0, y1, (1 - fx) * fy),
            (x1, y1, fx * fy),
        ):
            value = struct.unpack_from(">h", self.data, (row * self.size + col) * 2)[0]
            if value != -32768 and w > 0:
                total += value * w
                weight += w
        if weight == 0:
            raise ValueError("所选位置没有有效高程")
        return total / weight


def terrain_heightmap(
    level: int,
    col: int,
    row: int,
    tile_path,
    geoid: GeoidGrid | None,
    size=HEIGHTMAP_SIZE,
) -> bytes:
    """同一全球采样坐标在相邻瓦片共享边界上得到完全相同的高度。"""
    if not (0 <= level <= 20 and 0 <= col < 2**level and 0 <= row < 2**level):
        raise ValueError("地形瓦片编号无效")
    values = array("f")
    if level < TERRAIN_START_LEVEL:
        values = array("f", [0.0]) * (
            size * size
        )  # 远景椭球近似，靠近后才读取真实高程。
    else:
        if geoid is None:
            raise ValueError("缺少 EGM96 高程基准格网")
        with ExitStack() as stack:
            opened = {}
            for y in range(size):
                for x in range(size):
                    lat, lon = pixel_to_latlng(
                        (col + x / (size - 1)) * 256,
                        (row + y / (size - 1)) * 256,
                        level,
                    )
                    lon = (lon + 180) % 360 - 180
                    south, west = math.floor(lat), math.floor(lon)
                    key = south, west
                    if key not in opened:
                        tile = HgtTile(tile_path(south, west), south, west)
                        stack.callback(tile.close)
                        opened[key] = tile
                    # 椭球高 h = EGM96 海拔 H + 大地水准面起伏 N。
                    values.append(
                        opened[key].sample(lat, lon) + geoid.undulation(lat, lon)
                    )
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()
