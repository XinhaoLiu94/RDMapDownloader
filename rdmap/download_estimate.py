"""无需联网的下载流量粗估，按图层类型和瓦片数量计算。"""

from __future__ import annotations

from collections.abc import Iterable

from .providers import MapProvider
from .tile_math import TileRange


def estimated_tile_bytes(provider: MapProvider, zoom: int) -> int:
    """模型假设值，不是服务商保证值；包含透明注记层的下载量。"""
    if provider.key == "road":
        kib = 6
    elif provider.key in ("vector", "google_street"):
        kib = 18
    elif provider.key == "google_terrain":
        kib = 24
    elif provider.key == "google_hybrid":
        # Google 混合图由一个端点直接返回，已经包含注记。
        kib = 36
    else:
        kib = 28
    kib += 6 * len(provider.overlay_templates)
    # 只使用宽泛级别区间，避免把粗略模型表现为逐级精确预测。
    factor = 0.75 if zoom <= 7 else (1.15 if zoom >= 16 else 1.0)
    return round(kib * 1024 * factor)


def estimate_download_bytes(provider: MapProvider, ranges: Iterable[TileRange]) -> int:
    """完整范围的估计网络流量，不扫描目录、不扣除已有文件或计算重试。"""
    return sum(item.count * estimated_tile_bytes(provider, item.zoom) for item in ranges)
