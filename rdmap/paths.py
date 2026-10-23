"""路径、缓存与格式化辅助函数。"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

APP_DIR_NAME = "RDMapDownloader"


def project_root() -> Path:
    """源码运行或 PyInstaller 打包后的资源根目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def resource_path(relative: str) -> Path:
    return project_root() / relative


def cache_root() -> Path:
    """瓦片缓存根目录（可用环境变量 RDMAP_CACHE_DIR 覆盖，便于测试）。"""
    override = os.environ.get("RDMAP_CACHE_DIR")
    if override:
        return Path(override).expanduser()

    try:
        from PyQt5.QtCore import QStandardPaths

        location = QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
        if location:
            return Path(location)
    except Exception:
        pass
    return Path.home() / ".rdmapdownloader"


def tile_cache_dir() -> Path:
    return cache_root() / "tiles"


def task_database_path() -> Path:
    """任务记录属于应用数据，清理地图缓存不会删除它们。"""
    override = os.environ.get("RDMAP_TASK_DIR")
    if override:
        return Path(override).expanduser() / "tasks.sqlite3"
    from PyQt5.QtCore import QStandardPaths

    location = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    root = Path(location) if location else Path.home() / ".rdmapdownloader"
    return root / "tasks.sqlite3"


def ensure_dir(path: Path | str) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def directory_size(path: Path | str) -> int:
    """递归统计目录大小，忽略无法访问的文件。"""
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
    except OSError:
        pass
    return total


def format_bytes(size: float) -> str:
    size = float(max(0.0, size))
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while size >= 1024.0 and index < len(units) - 1:
        size /= 1024.0
        index += 1
    if index == 0:
        return f"{int(size)} B"
    return f"{size:.2f} {units[index]}"


def format_duration(seconds: float | None) -> str:
    """把秒数格式化为 mm:ss 或 h:mm:ss。"""
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "--:--"
    total = int(seconds + 0.5)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
