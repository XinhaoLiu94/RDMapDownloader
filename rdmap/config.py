"""全局配置与默认值。"""

from __future__ import annotations

from pathlib import Path

APP_NAME = "RDMapDownloader"
APP_TITLE = "RDMap · 地图工作台"

# 默认地图视野（广州）
DEFAULT_LATITUDE = 23.32
DEFAULT_LONGITUDE = 113.55
DEFAULT_ZOOM = 10

# 默认框选范围：北、南、西、东
DEFAULT_NORTH = 23.60
DEFAULT_SOUTH = 23.10
DEFAULT_WEST = 113.30
DEFAULT_EAST = 113.90

MIN_MAP_ZOOM = 1
MAX_MAP_ZOOM = 20
MIN_TILE_ZOOM = 1
MAX_TILE_ZOOM = 20

DEFAULT_CONCURRENCY = 8
MAX_CONCURRENCY = 32


def default_download_root() -> Path:
    """默认瓦片输出根目录。"""
    downloads = Path.home() / "Downloads"
    base = downloads if downloads.is_dir() else Path.home()
    return base / "RDMapTiles"


def default_download_dir(provider_key: str | None = None) -> Path:
    """默认瓦片输出目录。

    传入 provider_key 时按图源类型分目录，避免不同图源、不同图层之间
    的瓦片互相覆盖；不传时返回根目录。
    """
    root = default_download_root()
    return root / provider_key if provider_key else root
