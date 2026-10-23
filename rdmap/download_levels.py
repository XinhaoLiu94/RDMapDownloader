"""多级下载的级别输入、显示和范围换算。"""
from __future__ import annotations

import re

from .config import MAX_TILE_ZOOM, MIN_TILE_ZOOM
from .tile_math import TileRange, range_from_bounds


def parse_levels(text: str, max_zoom: int = MAX_TILE_ZOOM) -> tuple[int, ...]:
    text = text.strip().replace("，", ",").replace("–", "-").replace("—", "-").replace("~", "-")
    text = re.sub(r"\s*-\s*", "-", text)
    if not text:
        raise ValueError("请输入下载级别，例如 10-14, 16, 18")
    levels = set()
    for token in re.split(r"[,;；、\s]+", text):
        if not token:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if match is None:
            raise ValueError(f"无法识别级别“{token}”，请使用 10-14, 16 这样的格式")
        first = int(match[1])
        last = int(match[2]) if match[2] else first
        if first > last:
            raise ValueError("级别范围的起始值不能大于结束值")
        if first < MIN_TILE_ZOOM or last > max_zoom:
            raise ValueError(f"当前图源支持 Z{MIN_TILE_ZOOM}–Z{max_zoom}")
        levels.update(range(first, last + 1))
    if not levels:
        raise ValueError("请至少选择一个下载级别")
    return tuple(sorted(levels))


def format_levels(levels) -> str:
    values = sorted(set(levels))
    groups = []
    for value in values:
        if groups and value == groups[-1][-1] + 1:
            groups[-1].append(value)
        else:
            groups.append([value])
    return ", ".join(str(g[0]) if len(g) == 1 else f"{g[0]}-{g[-1]}" for g in groups)


def ranges_for_levels(
    levels: tuple[int, ...], bounds: tuple[float, float, float, float], reference: TileRange
) -> tuple[TileRange, ...]:
    """保持同一地理范围；手动行列范围使用精确的 XYZ 层级换算。"""
    if range_from_bounds(*bounds, reference.zoom) == reference:
        return tuple(range_from_bounds(*bounds, zoom) for zoom in levels)
    result = []
    for zoom in levels:
        delta = zoom - reference.zoom
        if delta >= 0:
            result.append(TileRange(
                zoom, reference.min_row << delta, ((reference.max_row + 1) << delta) - 1,
                reference.min_col << delta, ((reference.max_col + 1) << delta) - 1,
            ))
        else:
            result.append(TileRange(
                zoom, reference.min_row >> -delta, reference.max_row >> -delta,
                reference.min_col >> -delta, reference.max_col >> -delta,
            ))
    return tuple(result)
