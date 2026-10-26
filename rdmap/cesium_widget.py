"""按需创建的 Cesium/Qt WebEngine 视图，Python 保持下载与数据服务。"""

from __future__ import annotations

import json
import math
import threading

from PyQt5.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from .terrain_server import ResourceStore, TerrainServer
from .theme import COLORS


class CesiumBridge(QObject):
    readySignal = pyqtSignal()
    errorSignal = pyqtSignal(str)
    messageSignal = pyqtSignal(str)
    positionSignal = pyqtSignal(float, float)
    selectionSignal = pyqtSignal(object)
    cameraSignal = pyqtSignal(object)
    modeSignal = pyqtSignal(bool)
    terrainSignal = pyqtSignal()

    @pyqtSlot()
    def ready(self):
        self.readySignal.emit()

    @pyqtSlot(str)
    def error(self, text):
        self.errorSignal.emit(text[:500])

    @pyqtSlot(str)
    def message(self, text):
        self.messageSignal.emit(text[:300])

    @pyqtSlot(float, float)
    def position(self, lat, lon):
        if math.isfinite(lat) and math.isfinite(lon):
            self.positionSignal.emit(lat, lon)

    @pyqtSlot(str)
    def selection(self, text):
        try:
            bounds = json.loads(text)
            if len(bounds) == 4 and all(
                isinstance(v, (int, float)) and math.isfinite(v) for v in bounds
            ):
                n, s, w, e = bounds
                if -85.052 <= s < n <= 85.052 and -180 <= w < e <= 180:
                    self.selectionSignal.emit(bounds)
        except (ValueError, TypeError):
            pass

    @pyqtSlot(str)
    def camera(self, text):
        try:
            state = json.loads(text)
            if isinstance(state, dict) and len(text) < 4000:
                self.cameraSignal.emit(state)
        except (ValueError, TypeError):
            pass

    @pyqtSlot(bool)
    def selectionMode(self, active):
        self.modeSignal.emit(active)

    @pyqtSlot()
    def terrainLoaded(self):
        self.terrainSignal.emit()


class CesiumWidget(QWidget):
    ready = pyqtSignal()
    log = pyqtSignal(str)
    positionChanged = pyqtSignal(float, float)
    selectionChanged = pyqtSignal(object)
    selectionModeChanged = pyqtSignal(bool)
    cameraChanged = pyqtSignal(object)
    _prepared = pyqtSignal(str)
    _status = pyqtSignal(str)
    _failure = pyqtSignal(str)

    def __init__(self, parent=None, engine: str = "cesium"):
        super().__init__(parent)
        self.engine = engine if engine in ("cesium", "mars3d") else "cesium"
        self.is_ready = False
        self.closed = False
        self.drawing = False
        self.camera_state = {}
        self._pending = {}
        self._state_commands = {}
        self._server = None
        self._store = ResourceStore(notify=self._status.emit)
        self._loading = False
        self._page = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.stack = QStackedWidget()
        placeholder = QWidget()
        box = QVBoxLayout(placeholder)
        box.addStretch()
        title_text = "Mars3D 三维 · WGS84" if self.engine == "mars3d" else "三维地图 · WGS84"
        title = QLabel(title_text)
        title.setObjectName("panelTitle")
        title.setAlignment(Qt.AlignCenter)
        box.addWidget(title)
        self.detail = QLabel("正在准备三维组件…")
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignCenter)
        box.addWidget(self.detail)
        self.retry = QPushButton("重新加载")
        self.retry.setMaximumWidth(160)
        self.retry.clicked.connect(self.start)
        box.addWidget(self.retry, 0, Qt.AlignHCenter)
        box.addStretch()
        self.stack.addWidget(placeholder)
        layout.addWidget(self.stack, 1)
        self.status = QLabel("高程：靠近地表后加载 · WGS84 / EGM96 → 椭球高")
        self.status.setObjectName("hintLabel")
        self.status.setContentsMargins(12, 4, 12, 4)
        layout.addWidget(self.status)
        self.bridge = CesiumBridge(self)
        self.bridge.readySignal.connect(self._on_ready)
        self.bridge.errorSignal.connect(self._on_error)
        self.bridge.messageSignal.connect(self._set_status)
        self.bridge.positionSignal.connect(self.positionChanged)
        self.bridge.selectionSignal.connect(self.selectionChanged)
        self.bridge.modeSignal.connect(self._selection_mode)
        self.bridge.cameraSignal.connect(self._camera)
        self.bridge.terrainSignal.connect(
            lambda: self._set_status("高程已加载 · EGM96 海拔已转换为 WGS84 椭球高")
        )
        self._prepared.connect(self._load_page)
        self._status.connect(self._set_status)
        self._failure.connect(self._on_error)
        self._startup_timer = QTimer(self)
        self._startup_timer.setSingleShot(True)
        self._startup_timer.timeout.connect(
            lambda: self._on_error("三维页面加载超时，请检查网络后重新加载。")
        )
        QTimer.singleShot(0, self.start)

    def start(self):
        if self.closed or self._loading:
            return
        self._loading = True
        self.is_ready = False
        self.stack.setCurrentIndex(0)
        self.retry.setEnabled(False)
        self.detail.setText("正在准备三维组件，首次使用需要联网…")
        try:
            from PyQt5.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        except ImportError:
            self._on_error(
                "缺少三维组件。请安装 requirements-3d.txt 中的依赖后重启程序：\npython -m pip install -r requirements-3d.txt"
            )
            return

        def prepare():
            try:
                with self._store._guard:
                    self._store._failures.clear()
                self._store.prepare()
                if self.closed:
                    return
                if self._server is None:
                    self._server = TerrainServer(
                        self._store, self._failure.emit, engine=self.engine
                    )
                if self.closed:
                    self._server.close()
                    return
                self._prepared.emit(self._server.url)
            except Exception as exc:
                if not self.closed:
                    try:
                        self._failure.emit("三维组件准备失败：" + str(exc)[:180])
                    except RuntimeError:
                        pass

        threading.Thread(target=prepare, daemon=True, name="rdmap-3d-prepare").start()

    def _load_page(self, url):
        if self.closed:
            return
        if self._page is None:
            from PyQt5.QtWebChannel import QWebChannel
            from PyQt5.QtWebEngineWidgets import (
                QWebEngineView,
                QWebEnginePage,
                QWebEngineProfile,
            )

            self.web = QWebEngineView(self)
            profile = QWebEngineProfile(
                self.web
            )  # Off-the-record; no website state is persisted.
            self._page = QWebEnginePage(profile, self.web)
            self._profile = profile
            self.channel = QWebChannel(self._page)
            self.channel.registerObject("bridge", self.bridge)
            self._page.setWebChannel(self.channel)
            self.web.setPage(self._page)
            self._page.renderProcessTerminated.connect(self._renderer_stopped)
            self.stack.addWidget(self.web)
        self.stack.setCurrentWidget(self.web)
        self._startup_timer.start(60000)
        self.web.load(QUrl(url))

    def _renderer_stopped(self, status, code):
        self.is_ready = False
        self._on_error(f"三维渲染进程退出（{code}），可重新加载或返回 2D。")

    def _on_ready(self):
        self._loading = False
        self.is_ready = True
        self.retry.setEnabled(True)
        self._startup_timer.stop()
        for method, data in list(self._state_commands.items()):
            self.command(method, data)
        for method, data in list(self._pending.items()):
            self.command(method, data)
        self._pending.clear()
        self._set_status("WGS84 · 高程随视野靠近自动加载")
        self.ready.emit()
        if self.engine == "mars3d":
            self.log.emit("Mars3D 地图已就绪：Mars3D 3.7.17 · WGS84")
        else:
            self.log.emit("三维地图已就绪：Cesium · WGS84")

    def _on_error(self, message):
        if self.closed:
            return
        self._set_status(message)
        self.log.emit(message)
        if not self.is_ready:
            self._loading = False
            self._startup_timer.stop()
            self.detail.setText(message)
            self.retry.setEnabled(True)
            self.stack.setCurrentIndex(0)

    def _set_status(self, text):
        if not self.closed:
            self.status.setText(text)
            if not self.is_ready:
                self.detail.setText(text)

    def _selection_mode(self, active):
        self.drawing = active
        self.selectionModeChanged.emit(active)

    def _camera(self, state):
        self.camera_state = state
        self._state_commands.pop("flyTo", None)
        self._state_commands["restoreCamera"] = state
        self.cameraChanged.emit(state)

    def command(self, method: str, data=None):
        if self.closed:
            return
        if method in (
            "setSource",
            "setTheme",
            "setEnabled",
            "setActive",
            "setSelection",
            "flyTo",
            "restoreCamera",
        ):
            if method == "flyTo":
                self._state_commands.pop("restoreCamera", None)
            elif method == "restoreCamera":
                self._state_commands.pop("flyTo", None)
            self._state_commands[method] = data
        if method == "cancelSelection":
            self._selection_mode(False)
        if not self.is_ready:
            self._pending[method] = data
            return
        self._page.runJavaScript(
            f"window.rdmap && window.rdmap[{json.dumps(method)}]({json.dumps(data,allow_nan=False)})"
        )

    def set_source(self, provider):
        self.command(
            "setSource",
            {
                "key": provider.key,
                "maxZoom": provider.max_zoom,
                "credit": provider.attribution,
            },
        )

    def set_theme(self):
        self.command(
            "setTheme",
            {
                key: COLORS[key]
                for key in ("surface", "on_surface", "muted", "primary", "outline")
            },
        )

    def begin_selection(self):
        self.drawing = True
        self.command("beginSelection")

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self._startup_timer.stop()
        self._store.closed.set()
        if self._page is not None:
            self.web.stop()
            self._page.setWebChannel(None)
            self._page.deleteLater()
        if self._server is not None:
            threading.Thread(target=self._server.close, daemon=True).start()
