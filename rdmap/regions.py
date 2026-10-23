"""用于快速框选的常用区域，均为近似外接矩形，不是行政边界裁剪。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegionPreset:
    key: str
    name: str
    north: float
    south: float
    west: float
    east: float

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return self.north, self.south, self.west, self.east


# 参考 Natural Earth 的国家数据，向外留余量，覆盖主要陆地及相邻海域。
# https://www.naturalearthdata.com/downloads/110m-cultural-vectors/110m-admin-0-countries/
# 主动区分美国本土、法国欧洲部分，避免把海外领土合并为横跨半个地球的矩形。
REGION_PRESETS = (
    RegionPreset("china_mainland", "中国大陆（含海南）", 53.6, 18.0, 73.5, 135.1),
    RegionPreset("us_contiguous", "美国本土（连续 48 州）", 49.5, 24.4, -125.0, -66.9),
    RegionPreset("canada", "加拿大", 83.3, 41.6, -141.1, -52.6),
    RegionPreset("uk", "英国", 61.0, 49.8, -8.7, 1.9),
    RegionPreset("france_europe", "法国（欧洲部分）", 51.2, 41.3, -5.2, 9.7),
    RegionPreset("germany", "德国", 55.1, 47.2, 5.8, 15.1),
    RegionPreset("japan", "日本（主要岛屿）", 45.6, 24.0, 122.9, 145.9),
    RegionPreset("south_korea", "韩国", 38.7, 33.0, 124.5, 130.0),
    RegionPreset("australia", "澳大利亚（含塔斯马尼亚）", -10.5, -43.8, 113.0, 153.7),
    RegionPreset("new_zealand", "新西兰（主要岛屿）", -34.3, -47.4, 166.3, 178.7),
    RegionPreset("brazil", "巴西", 5.4, -33.9, -74.0, -34.7),
)

REGION_MAP = {preset.key: preset for preset in REGION_PRESETS}
