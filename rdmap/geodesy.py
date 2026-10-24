"""视图间的水平坐标转换与保留原始范围的地理选区。"""

from __future__ import annotations

from dataclasses import dataclass
import math

WGS84 = "WGS84"
GCJ02 = "GCJ02"
CGCS2000 = "CGCS2000"


def provider_datum(provider) -> str:
    if provider.source == "amap" or provider.key in {"google_street", "google_terrain"}:
        return GCJ02
    if provider.source == "tianditu":
        return CGCS2000
    return WGS84


def _offset(latitude: float, longitude: float) -> tuple[float, float]:
    # GCJ-02 的常用数值近似；只用于地图视图衔接，不是测绘基准转换。
    if not (72.004 <= longitude <= 137.8347 and 0.8293 <= latitude <= 55.8271):
        return 0.0, 0.0
    x, y = longitude - 105.0, latitude - 35.0
    wave = (20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)) * 2 / 3
    dy = (
        -100
        + 2 * x
        + 3 * y
        + 0.2 * y * y
        + 0.1 * x * y
        + 0.2 * math.sqrt(abs(x))
        + wave
    )
    dy += (20 * math.sin(y * math.pi) + 40 * math.sin(y * math.pi / 3)) * 2 / 3
    dy += (160 * math.sin(y * math.pi / 12) + 320 * math.sin(y * math.pi / 30)) * 2 / 3
    dx = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x)) + wave
    dx += (20 * math.sin(x * math.pi) + 40 * math.sin(x * math.pi / 3)) * 2 / 3
    dx += (150 * math.sin(x * math.pi / 12) + 300 * math.sin(x * math.pi / 30)) * 2 / 3
    phi = math.radians(latitude)
    eccentricity = 0.006693421622965943
    magic = 1 - eccentricity * math.sin(phi) ** 2
    dy *= 180 / (6378245 * (1 - eccentricity) / (magic * math.sqrt(magic)) * math.pi)
    dx *= 180 / (6378245 / math.sqrt(magic) * math.cos(phi) * math.pi)
    return dy, dx


def from_wgs84(latitude: float, longitude: float, datum: str) -> tuple[float, float]:
    if datum == GCJ02:
        dy, dx = _offset(latitude, longitude)
        return latitude + dy, longitude + dx
    if datum not in (WGS84, CGCS2000):
        raise ValueError("未知坐标基准")
    # CGCS2000 的数值仅作近似定位，不声称完成历元/厘米级转换。
    return latitude, longitude


def to_wgs84(latitude: float, longitude: float, datum: str) -> tuple[float, float]:
    if datum != GCJ02:
        return from_wgs84(latitude, longitude, datum)
    lat, lon = latitude, longitude
    for _ in range(12):
        projected_lat, projected_lon = from_wgs84(lat, lon, GCJ02)
        dy, dx = projected_lat - latitude, projected_lon - longitude
        lat, lon = lat - dy, lon - dx
        if max(abs(dy), abs(dx)) < 1e-10:
            break
    return lat, lon


@dataclass(frozen=True)
class GeographicSelection:
    """保留原始边界，切视图只派生显示包络，不反复改写选区。"""

    native_bounds: tuple[float, float, float, float]
    datum: str

    def __post_init__(self):
        n, s, w, e = self.native_bounds
        if not all(math.isfinite(v) for v in self.native_bounds) or not (
            -90 <= s < n <= 90 and -180 <= w < e <= 180
        ):
            raise ValueError("无效的地理范围")
        if self.datum not in (WGS84, GCJ02, CGCS2000):
            raise ValueError("未知坐标基准")

    def wgs84_edge(self, steps: int = 32) -> list[tuple[float, float]]:
        n, s, w, e = self.native_bounds
        points = []
        for index in range(steps + 1):
            t = index / steps
            points.extend(
                (
                    (n, w + (e - w) * t),
                    (s, w + (e - w) * t),
                    (s + (n - s) * t, w),
                    (s + (n - s) * t, e),
                )
            )
        return [to_wgs84(lat, lon, self.datum) for lat, lon in points]

    def bounds_for(self, datum: str) -> tuple[float, float, float, float]:
        if datum == self.datum:
            return self.native_bounds
        points = [from_wgs84(lat, lon, datum) for lat, lon in self.wgs84_edge()]
        return (
            max(p[0] for p in points),
            min(p[0] for p in points),
            min(p[1] for p in points),
            max(p[1] for p in points),
        )

    def to_dict(self) -> dict:
        return {"bounds": list(self.native_bounds), "datum": self.datum}

    @classmethod
    def from_dict(cls, value: dict):
        return cls(tuple(float(v) for v in value["bounds"]), str(value["datum"]))
