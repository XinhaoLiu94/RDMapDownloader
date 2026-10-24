"""仅监听本机的 Cesium 资源、影像缓存与 WGS84 地形服务。"""

from __future__ import annotations

import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
from pathlib import Path, PurePosixPath
import secrets
import threading
import time
from socketserver import TCPServer
from urllib.parse import unquote, urlsplit

import requests

from .elevation import GeoidGrid, hgt_name, terrain_heightmap
from .paths import cache_root, resource_path, tile_cache_dir
from .providers import PROVIDER_MAP, tile_headers
from .tile_images import compose_tile
from .workers import _system_proxies

# 固定已验证兼容 Qt 5 WebEngine 的引擎；运行时与资源均可离线复用。
CESIUM_VERSION = "1.117"
CESIUM_SHA256 = "d0032c172458660d2b80cbcdf51a3afe9620eff3fb1b8ecb60819e9bd2558ede"
CESIUM_BASE = (
    f"https://cesium.com/downloads/cesiumjs/releases/{CESIUM_VERSION}/Build/Cesium/"
)
GEOID_URL = "https://download.osgeo.org/proj/vdatum/egm96_15/egm96_15.gtx"
GEOID_SHA256 = "c02a6eb70a7a78efebe5adf3ade626eb75390e170bb8b3f36136a2c28f5326a0"
THREE_D_PROVIDERS = frozenset({"esri_satellite", "esri_hybrid", "google_satellite"})


def supports_3d(provider) -> bool:
    return provider.key in THREE_D_PROVIDERS


class ResourceStore:
    def __init__(self, root: Path | None = None, notify=None):
        self.root = root or cache_root() / "3d"
        self.notify = notify or (lambda text: None)
        self.closed = threading.Event()
        self._guard = threading.Lock()
        self._locks = {}
        self._failures = {}
        self._local = threading.local()
        self._proxies = _system_proxies()
        self._geoid = None
        self._engine_payloads = {}

    def lock(self, path):
        with self._guard:
            return self._locks.setdefault(str(path), threading.Lock())

    def session(self):
        if not hasattr(self._local, "session"):
            session = requests.Session()
            session.proxies.update(self._proxies)
            self._local.session = session
        return self._local.session

    def download(self, url: str, headers=None, limit=32_000_000) -> bytes:
        if self.closed.is_set():
            raise RuntimeError("三维服务已关闭")
        chunks, length = [], 0
        with self.session().get(
            url, headers=headers, timeout=(8, 20), stream=True
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_content(65536):
                if self.closed.is_set():
                    raise RuntimeError("三维服务已关闭")
                length += len(chunk)
                if length > limit:
                    raise ValueError("三维资源超过大小限制")
                chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def write(path: Path, data: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + secrets.token_hex(4) + ".part")
        try:
            temporary.write_bytes(data)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def cached(self, path: Path, produce, validate=None) -> Path:
        with self.lock(path):
            if path.is_file():
                if validate is None or validate(path.read_bytes()):
                    return path
                path.unlink()
            if self.closed.is_set():
                raise RuntimeError("三维服务已关闭")
            with self._guard:
                failure = self._failures.get(str(path), 0)
            if time.monotonic() < failure:
                raise RuntimeError("资源暂不可用，请稍后重试")
            try:
                data = produce()
                if validate is not None and not validate(data):
                    raise ValueError("资源内容校验失败")
                self.write(path, data)
                return path
            except Exception:
                with self._guard:
                    self._failures[str(path)] = time.monotonic() + 30
                raise

    def engine_asset(self, relative: str) -> Path:
        parts = PurePosixPath(relative).parts
        if not parts or any(p in ("..", ".") or ":" in p or "\\" in p for p in parts):
            raise ValueError("无效的资源路径")
        if relative not in ("Cesium.js",) and parts[0] not in (
            "Assets",
            "Workers",
            "ThirdParty",
            "Widgets",
        ):
            raise ValueError("不支持的三维资源")
        path = self.root / f"cesium-{CESIUM_VERSION}" / Path(*parts)

        def valid_script(data):
            valid = hashlib.sha256(data).hexdigest() == CESIUM_SHA256
            if valid:
                self._engine_payloads[relative] = data
            return valid

        validator = valid_script if relative == "Cesium.js" else None
        return self.cached(
            path, lambda: self.download(CESIUM_BASE + relative), validator
        )

    def engine_bytes(self, relative: str) -> bytes:
        if relative not in self._engine_payloads:
            path = self.engine_asset(relative)
            if relative not in self._engine_payloads:
                self._engine_payloads[relative] = path.read_bytes()
        return self._engine_payloads[relative]

    def prepare(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.notify("正在准备三维引擎，首次使用需要联网…")
        self.engine_bytes("Cesium.js")

    def geoid(self) -> GeoidGrid:
        with self.lock("geoid-object"):
            if self._geoid is None:
                self.notify("正在准备 EGM96 高程基准格网…")
                path = self.cached(
                    self.root / "geoid" / "egm96_15.gtx",
                    lambda: self.download(GEOID_URL),
                    lambda data: len(data) == 4_153_000
                    and hashlib.sha256(data).hexdigest() == GEOID_SHA256,
                )
                self._geoid = GeoidGrid(path)
            return self._geoid

    def hgt_path(self, south: int, west: int) -> Path:
        name = hgt_name(south, west)
        path = self.root / "elevation" / (name + ".hgt")
        sizes = (1201 * 1201 * 2, 3601 * 3601 * 2)
        # HGT 只按尺寸验证；每个网格被多块地形复用，不能每次读完整的 26 MB。
        if path.is_file() and path.stat().st_size in sizes:
            return path

        def fetch():
            self.notify("正在加载高程：" + name)
            compressed = self.download(
                f"https://s3.amazonaws.com/elevation-tiles-prod/skadi/{name[:3]}/{name}.hgt.gz"
            )
            # 固定 HGT 分辨率，限制解压尺寸。
            import io

            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as source:
                data = source.read(3601 * 3601 * 2 + 1)
            return data

        return self.cached(path, fetch, lambda data: len(data) in sizes)

    def heights(self, level: int, col: int, row: int) -> bytes:
        path = self.root / "terrain-ellipsoid-v1" / str(level) / str(col) / f"{row}.bin"

        def produce():
            geoid = self.geoid() if level >= 9 else None
            return terrain_heightmap(level, col, row, self.hgt_path, geoid)

        return self.cached(
            path, produce, lambda data: len(data) == 65 * 65 * 4
        ).read_bytes()

    def imagery(self, key: str, level: int, col: int, row: int) -> bytes:
        if key not in THREE_D_PROVIDERS:
            raise ValueError("三维模式仅支持 WGS84 图层")
        provider = PROVIDER_MAP[key]
        if not (
            0 <= level <= provider.max_zoom
            and 0 <= col < 2**level
            and 0 <= row < 2**level
        ):
            raise ValueError("影像瓦片编号无效")
        path = tile_cache_dir() / key / str(level) / str(col) / f"{row}.png"

        def fetch():
            return compose_tile(
                [
                    self.download(
                        url, tile_headers(col, row, provider.referer), 4_000_000
                    )
                    for url in provider.urls(level, col, row)
                ]
            )

        def valid(data):
            try:
                compose_tile([data])
                return True
            except ValueError:
                return False

        return self.cached(path, fetch, valid).read_bytes()


class TerrainServer:
    def __init__(self, store: ResourceStore, report=None):
        self.store = store
        self.report = report or (lambda text: None)
        self.token = secrets.token_urlsafe(20)
        self._reported = set()
        self._report_guard = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                try:
                    path = unquote(urlsplit(self.path).path)
                    prefix = "/" + owner.token + "/"
                    if not path.startswith(prefix):
                        self.send_error(404)
                        return
                    relative = path[len(prefix) :]
                    parts = PurePosixPath(relative).parts
                    if any(p in ("..", ".") or "\\" in p or ":" in p for p in parts):
                        self.send_error(404)
                        return
                    if relative in (
                        "index.html",
                        "viewer.js",
                        "viewer.css",
                        "compat.js",
                    ):
                        data = resource_path("assets/cesium/" + relative).read_bytes()
                        mime = (
                            mimetypes.guess_type(relative)[0]
                            or "application/octet-stream"
                        )
                    elif relative.startswith("cesium/"):
                        data = owner.store.engine_bytes(relative[7:])
                        mime = (
                            mimetypes.guess_type(relative)[0]
                            or "application/octet-stream"
                        )
                    elif len(parts) == 5 and parts[0] == "imagery":
                        level, col, row = map(int, parts[2:])
                        data = owner.store.imagery(parts[1], level, col, row)
                        mime = (
                            "image/jpeg"
                            if data.startswith(b"\xff\xd8")
                            else "image/png"
                        )
                    elif len(parts) == 4 and parts[0] == "terrain":
                        level, col, row = map(int, parts[1:])
                        if not (
                            0 <= level <= 20
                            and 0 <= col < 2**level
                            and 0 <= row < 2**level
                        ):
                            raise ValueError("地形瓦片编号无效")
                        data = owner.store.heights(level, col, row)
                        mime = "application/octet-stream"
                    else:
                        self.send_error(404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "private, max-age=3600")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as exc:
                    with owner._report_guard:
                        category = (
                            "高程加载失败"
                            if "/terrain/" in self.path
                            else "三维资源加载失败"
                        )
                        if (
                            category not in owner._reported
                            and not owner.store.closed.is_set()
                        ):
                            owner._reported.add(category)
                            owner.report(f"{category}：{str(exc)[:140]}")
                    try:
                        self.send_error(503)
                    except OSError:
                        pass

        class LoopbackServer(ThreadingHTTPServer):
            def server_bind(self):
                # 仅监听回环地址，无须为本机地址做可能阻塞的反向 DNS 查询。
                TCPServer.server_bind(self)
                self.server_name = "localhost"
                self.server_port = self.server_address[1]

        self.http = LoopbackServer(("127.0.0.1", 0), Handler)
        self.http.daemon_threads = True
        self.thread = threading.Thread(
            target=self.http.serve_forever, daemon=True, name="rdmap-3d-server"
        )
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.http.server_port}/{self.token}/index.html"

    def close(self):
        self.store.closed.set()
        self.http.shutdown()
        self.http.server_close()
