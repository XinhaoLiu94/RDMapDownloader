"""瓦片下载与图片拼接的后台线程。"""

from __future__ import annotations

import concurrent.futures
import os
import random
import threading
import time
import traceback
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import requests
from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtGui import QImage, QPainter

from .paths import format_duration
from .providers import MapProvider, tile_headers
from .tile_images import compose_tile
from .tile_math import TILE_SIZE, TileRange
from .tasks import TaskStore

# 拼接图片的安全上限：约 400 MB 的 ARGB 像素缓冲
MAX_OUTPUT_PIXELS = 100_000_000
MAX_OUTPUT_DIMENSION = 50_000
WARN_OUTPUT_PIXELS = 25_000_000

SUPPORTED_OUTPUT_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")

_OUTPUT_FORMATS: dict[str, tuple[str, int]] = {
    ".png": ("PNG", -1),
    ".jpg": ("JPEG", 92),
    ".jpeg": ("JPEG", 92),
    ".bmp": ("BMP", -1),
    ".webp": ("WEBP", 90),
    ".tif": ("TIFF", -1),
    ".tiff": ("TIFF", -1),
}


def output_image_format(path: str | Path) -> tuple[str, int] | None:
    """根据扩展名推断 QImage 保存格式，返回 (格式名, 质量)。"""
    suffix = Path(path).suffix.lower()
    return _OUTPUT_FORMATS.get(suffix)


def _looks_like_image(data: bytes) -> bool:
    if len(data) < 12:
        return False
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith((b"GIF87a", b"GIF89a")):
        return True
    if data.startswith(b"BM"):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False


def _short_error(error: BaseException, limit: int = 180) -> str:
    text = f"{type(error).__name__}: {error}"
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _system_proxies() -> dict[str, str]:
    """读取 Windows 系统代理（或环境变量代理），让 requests 与 QNAM 行为一致。"""
    env_names = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    )
    if any(os.environ.get(name) for name in env_names):
        # 交给 requests 自己按环境变量处理。
        return {}
    try:
        proxies = urllib.request.getproxies()
    except Exception:
        return {}
    result: dict[str, str] = {}
    for scheme in ("http", "https"):
        value = proxies.get(scheme)
        if value and not value.lower().startswith("socks"):
            result[scheme] = value
    if "https" not in result and "http" in result:
        result["https"] = result["http"]
    return result


@dataclass(frozen=True)
class DownloadOptions:
    output_dir: str
    provider: MapProvider
    tile_range: TileRange
    concurrency: int = 8
    skip_existing: bool = True
    retries: int = 3
    connect_timeout: float = 8.0
    read_timeout: float = 20.0
    api_key: str = field(default="", repr=False)
    task_id: str = ""
    task_db: str = ""


class DownloadWorker(QThread):
    """按行优先顺序下载瓦片，使用线程池并发。"""

    progress = pyqtSignal(int, int, int)  # 已完成, 总数, 失败数
    log = pyqtSignal(str)
    completed = pyqtSignal(bool, str, dict)

    def __init__(self, options: DownloadOptions, parent=None) -> None:
        super().__init__(parent)
        self._options = options
        self._stop_event = threading.Event()
        self._tls = threading.local()
        self._sessions: list[requests.Session] = []
        self._sessions_lock = threading.Lock()
        self._failures: list[dict[str, object]] = []
        self._system_proxies = _system_proxies()
        self._previous_done: set[int] = set()
        self._force_retry: set[int] = set()

    def cancel(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    def _session(self) -> requests.Session:
        session = getattr(self._tls, "session", None)
        if session is None:
            workers = max(4, min(32, self._options.concurrency))
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=workers,
                pool_maxsize=workers,
                max_retries=0,
            )
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            if self._system_proxies:
                session.proxies.update(self._system_proxies)
            self._tls.session = session
            with self._sessions_lock:
                self._sessions.append(session)
        return session

    def _iter_tasks(self) -> Iterator[tuple[int, int, str]]:
        options = self._options
        tile_range = options.tile_range
        base = Path(options.output_dir) / str(tile_range.zoom)
        for row in range(tile_range.min_row, tile_range.max_row + 1):
            row_dir = base / str(row)
            for col in range(tile_range.min_col, tile_range.max_col + 1):
                yield col, row, str(row_dir / f"{col}.png")

    def _atomic_write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.part")
        try:
            temp_path.write_bytes(data)
            os.replace(temp_path, path)
        except OSError:
            try:
                temp_path.unlink()
            except OSError:
                pass
            raise

    def _fetch_one(self, task: tuple[int, int, str]) -> tuple[str, str]:
        options = self._options
        col, row, path_text = task
        if self._stop_event.is_set():
            return "cancelled", ""

        destination = Path(path_text)
        tile_index = (row << options.tile_range.zoom) + col
        if (options.skip_existing or tile_index in self._previous_done) and tile_index not in self._force_retry:
            try:
                if destination.is_file() and destination.stat().st_size > 0:
                    image = QImage(str(destination))
                    if not image.isNull() and image.width() == TILE_SIZE and image.height() == TILE_SIZE:
                        return "skipped", ""
            except OSError:
                pass

        try:
            urls = options.provider.urls(options.tile_range.zoom, col, row, options.api_key)
        except ValueError as exc:
            return "failed", str(exc)
        headers = tile_headers(col, row, options.provider.referer)
        last_error = "未知错误"

        for attempt in range(options.retries + 1):
            if self._stop_event.is_set():
                return "cancelled", ""
            try:
                parts = []
                for url in urls:
                    if self._stop_event.is_set():
                        return "cancelled", ""
                    response = self._session().get(
                        url, headers=headers,
                        timeout=(options.connect_timeout, options.read_timeout),
                    )
                    try:
                        if response.status_code != 200:
                            raise ValueError(f"HTTP {response.status_code}")
                        parts.append(response.content)
                    finally:
                        response.close()
                content = compose_tile(parts)
                self._atomic_write(destination, content)
                return "success", ""
            except ValueError as exc:
                last_error = str(exc)
            except requests.RequestException as exc:
                last_error = "网络请求失败（请检查网络、代理或服务密钥）" if options.provider.requires_key else _short_error(exc)
            except OSError as exc:
                last_error = f"写入文件失败：{_short_error(exc)}"
            except Exception as exc:  # pragma: no cover - 防御性处理
                last_error = _short_error(exc)

            if attempt < options.retries:
                delay = min(3.0, 0.5 * (2**attempt)) + random.uniform(0.0, 0.25)
                if self._stop_event.wait(delay):
                    return "cancelled", ""

        return "failed", last_error

    def _record_failure(self, col: int, row: int, error: str) -> None:
        if len(self._failures) < 100:
            self._failures.append({"col": col, "row": row, "error": error})
        if len(self._failures) <= 10:
            z = self._options.tile_range.zoom
            self.log.emit(f"失败：Z{z} 行{row} 列{col} — {error}")

    def run(self) -> None:  # noqa: D401 - QThread 入口
        options = self._options
        tile_range = options.tile_range
        total = tile_range.count
        stats: dict[str, object] = {
            "total": total,
            "success": 0,
            "skipped": 0,
            "failed": 0,
            "cancelled": 0,
            "processed": 0,
            "zoom": tile_range.zoom,
            "output_dir": options.output_dir,
            "failures": self._failures,
        }
        started_at = time.monotonic()
        processed = 0
        success = skipped = failed = 0
        ok = False
        message = ""
        executor: concurrent.futures.ThreadPoolExecutor | None = None
        futures: dict[concurrent.futures.Future, tuple[int, int, str]] = {}
        store: TaskStore | None = None
        checkpoint: list[tuple[int, int, str, str]] = []
        last_checkpoint = time.monotonic()

        def flush_checkpoint(force: bool = False) -> None:
            nonlocal last_checkpoint
            if store is not None and checkpoint and (
                force or len(checkpoint) >= 32 or time.monotonic() - last_checkpoint >= 0.5
            ):
                store.record_tiles(options.task_id, checkpoint)
                checkpoint.clear()
                last_checkpoint = time.monotonic()

        def consume(future, task) -> None:
            nonlocal success, skipped, failed, processed
            if future.cancelled():
                return
            col, row, _path = task
            try:
                status, error = future.result()
            except Exception as exc:
                status, error = "failed", _short_error(exc)
            if status == "success":
                success += 1
            elif status == "skipped":
                skipped += 1
            elif status == "cancelled":
                stats["cancelled"] = int(stats["cancelled"]) + 1
            else:
                failed += 1
                status = "failed"
                self._record_failure(col, row, error)
            if store is not None and status != "cancelled":
                checkpoint.append((col, row, status, error))
            processed = success + skipped + failed

        try:
            if options.task_id:
                store = TaskStore(options.task_db or None)
                self._previous_done, self._force_retry = store.tile_states(options.task_id, tile_range.zoom)
                store.start(options.task_id)
            self.log.emit(
                f"开始下载 {total:,} 张瓦片（{options.provider.name} · Z{tile_range.zoom}，"
                f"并发 {options.concurrency}）→ {options.output_dir}"
            )
            self.progress.emit(0, total, 0)
            executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, options.concurrency),
                thread_name_prefix="tile-download",
            )
            tasks = self._iter_tasks()
            last_emit = 0.0

            while not self._stop_event.is_set():
                while len(futures) < options.concurrency * 4 and not self._stop_event.is_set():
                    try:
                        task = next(tasks)
                    except StopIteration:
                        break
                    futures[executor.submit(self._fetch_one, task)] = task

                if not futures:
                    break

                finished, _pending = concurrent.futures.wait(
                    futures,
                    timeout=0.2,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in finished:
                    task = futures.pop(future)
                    consume(future, task)
                    now = time.monotonic()
                    if now - last_emit >= 0.15 or processed >= total:
                        self.progress.emit(processed, total, failed)
                        last_emit = now
                flush_checkpoint()

            if self._stop_event.is_set():
                for future in futures:
                    future.cancel()
                self.log.emit("已请求停止下载，正在等待进行中的请求结束…")
        except Exception as exc:  # pragma: no cover - 防御性处理
            self.log.emit("下载线程异常：\n" + traceback.format_exc())
            message = f"下载异常：{_short_error(exc)}"
        finally:
            if executor is not None:
                executor.shutdown(wait=True)
            # 停止时，仍在途的请求可能成功或失败；等待结束后也必须记入任务。
            for future, task in futures.items():
                consume(future, task)
            try:
                flush_checkpoint(force=True)
            except Exception as exc:
                message = f"保存任务进度失败：{_short_error(exc)}"
            with self._sessions_lock:
                sessions = list(self._sessions)
                self._sessions.clear()
            for session in sessions:
                try:
                    session.close()
                except Exception:
                    pass

        elapsed = max(0.001, time.monotonic() - started_at)
        stats.update(
            {
                "success": success,
                "skipped": skipped,
                "failed": failed,
                "processed": processed,
                "elapsed": round(elapsed, 3),
                "speed": round(success / elapsed, 2),
                "failures": self._failures,
            }
        )

        cancelled = self._stop_event.is_set()
        if not message:
            if cancelled:
                message = (
                    f"已停止：已下载 {success:,} 张，跳过 {skipped:,} 张，"
                    f"失败 {failed:,} 张，用时 {format_duration(elapsed)}"
                )
            elif failed:
                message = (
                    f"下载结束（有失败）：成功 {success:,} 张，跳过 {skipped:,} 张，"
                    f"失败 {failed:,} 张，用时 {format_duration(elapsed)}"
                )
            elif total:
                message = (
                    f"下载完成：成功 {success:,} 张，跳过 {skipped:,} 张，"
                    f"用时 {format_duration(elapsed)}"
                )
            else:
                message = "没有需要下载的瓦片"

        ok = (not cancelled) and failed == 0 and message.startswith("下载完成")
        if store is not None:
            try:
                stats["task_status"] = store.finish(options.task_id, cancelled, message)
            except Exception as exc:
                ok = False
                message = f"保存任务状态失败：{_short_error(exc)}"
        stats["task_id"] = options.task_id
        self.progress.emit(processed, total, failed)
        self.completed.emit(ok, message, stats)


@dataclass(frozen=True)
class StitchOptions:
    tile_root: str
    output_path: str
    zoom: int
    tile_range: TileRange


class StitchWorker(QThread):
    """把 XYZ 瓦片目录拼接为一张大图。"""

    progress = pyqtSignal(int, int)  # 已处理, 总数
    log = pyqtSignal(str)
    completed = pyqtSignal(bool, str, dict)

    def __init__(self, options: StitchOptions, parent=None) -> None:
        super().__init__(parent)
        self._options = options
        self._stop_event = threading.Event()

    def cancel(self) -> None:
        self._stop_event.set()

    def _find_tile(self, col: int, row: int) -> Path | None:
        base = Path(self._options.tile_root) / str(self._options.zoom) / str(row)
        for suffix in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            candidate = base / f"{col}{suffix}"
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return candidate
            except OSError:
                continue
        return None

    def run(self) -> None:  # noqa: D401 - QThread 入口
        options = self._options
        tile_range = options.tile_range
        total = tile_range.count
        width = tile_range.pixel_width
        height = tile_range.pixel_height
        stats: dict[str, object] = {
            "total": total,
            "width": width,
            "height": height,
            "drawn": 0,
            "missing": 0,
            "processed": 0,
            "output_path": options.output_path,
        }

        fmt = output_image_format(options.output_path)
        if fmt is None:
            self.completed.emit(
                False,
                f"不支持的输出格式：{Path(options.output_path).suffix or '（无扩展名）'}",
                stats,
            )
            return
        if width > MAX_OUTPUT_DIMENSION or height > MAX_OUTPUT_DIMENSION or width * height > MAX_OUTPUT_PIXELS:
            self.completed.emit(
                False,
                f"拼接尺寸过大（{width:,} × {height:,} 像素），请缩小范围或降低级别",
                stats,
            )
            return

        format_name, quality = fmt
        try:
            image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        except Exception as exc:  # pragma: no cover - 内存不足等极端情况
            self.completed.emit(False, f"创建图片失败：{_short_error(exc)}", stats)
            return
        if image.isNull():
            self.completed.emit(
                False,
                f"无法分配 {width:,} × {height:,} 像素的图片内存，请缩小拼接范围",
                stats,
            )
            return
        image.fill(Qt.white if format_name == "JPEG" else Qt.transparent)

        painter = QPainter(image)
        if not painter.isActive():
            self.completed.emit(False, "无法创建绘图上下文", stats)
            return

        drawn = 0
        missing = 0
        processed = 0
        missing_examples: list[tuple[int, int]] = []
        cancelled = False
        started_at = time.monotonic()
        last_emit = 0.0

        self.log.emit(
            f"开始拼接：{tile_range.cols} × {tile_range.rows} 张瓦片，"
            f"输出 {width:,} × {height:,} 像素"
        )

        try:
            for row in range(tile_range.min_row, tile_range.max_row + 1):
                if self._stop_event.is_set():
                    cancelled = True
                    break
                destination_y = (row - tile_range.min_row) * TILE_SIZE
                for col in range(tile_range.min_col, tile_range.max_col + 1):
                    if self._stop_event.is_set():
                        cancelled = True
                        break
                    tile_path = self._find_tile(col, row)
                    tile_image = QImage(str(tile_path)) if tile_path is not None else QImage()
                    if tile_image.isNull():
                        missing += 1
                        if len(missing_examples) < 10:
                            missing_examples.append((col, row))
                    else:
                        destination_x = (col - tile_range.min_col) * TILE_SIZE
                        if tile_image.width() == TILE_SIZE and tile_image.height() == TILE_SIZE:
                            painter.drawImage(destination_x, destination_y, tile_image)
                        else:
                            painter.drawImage(
                                destination_x,
                                destination_y,
                                TILE_SIZE,
                                TILE_SIZE,
                                tile_image,
                            )
                        drawn += 1
                    processed += 1
                    now = time.monotonic()
                    if now - last_emit >= 0.15 or processed >= total:
                        self.progress.emit(processed, total)
                        last_emit = now
        except Exception as exc:  # pragma: no cover - 防御性处理
            self.log.emit("拼接线程异常：\n" + traceback.format_exc())
            self.completed.emit(False, f"拼接异常：{_short_error(exc)}", stats)
            return
        finally:
            painter.end()

        if cancelled:
            self.progress.emit(processed, total)
            self.completed.emit(
                False,
                f"已停止拼接：已处理 {processed:,} / {total:,} 张瓦片",
                stats,
            )
            return

        if missing_examples:
            examples = "、".join(f"({col},{row})" for col, row in missing_examples)
            self.log.emit(f"缺失瓦片 {missing:,} 张，示例（列,行）：{examples}")

        output_path = Path(options.output_path)
        temp_path = output_path.with_name(output_path.name + ".part")
        self.log.emit("瓦片绘制完成，正在写入图片…")
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if quality >= 0:
                saved = image.save(str(temp_path), format_name, quality)
            else:
                saved = image.save(str(temp_path), format_name)
            if not saved:
                raise OSError("QImage.save 返回失败")
            os.replace(temp_path, output_path)
        except Exception as exc:
            try:
                temp_path.unlink()
            except OSError:
                pass
            self.completed.emit(
                False,
                f"保存图片失败：{_short_error(exc)}",
                stats,
            )
            return

        elapsed = max(0.001, time.monotonic() - started_at)
        stats.update(
            {
                "drawn": drawn,
                "missing": missing,
                "processed": processed,
                "elapsed": round(elapsed, 3),
            }
        )
        self.progress.emit(total, total)
        message = (
            f"拼接完成：{width:,} × {height:,} 像素，"
            f"写入 {drawn:,} 张瓦片，用时 {format_duration(elapsed)}"
        )
        if missing:
            message += f"，缺失 {missing:,} 张（已留空）"
        self.completed.emit(True, message, stats)
