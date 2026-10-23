"""主窗口：地图浏览、框选、瓦片下载与拼接。"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from PyQt5.QtCore import (
    QSettings,
    QSignalBlocker,
    Qt,
    QTime,
    QTimer,
    QUrl,
)
from PyQt5.QtGui import QDesktopServices, QIcon, QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QFileDialog,
    QGroupBox,
    QLabel,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPlainTextEdit,
    QShortcut,
    QSpinBox,
)

from . import __version__
from .config import (
    APP_TITLE,
    DEFAULT_CONCURRENCY,
    DEFAULT_EAST,
    DEFAULT_NORTH,
    DEFAULT_SOUTH,
    DEFAULT_WEST,
    DEFAULT_ZOOM,
    MAX_CONCURRENCY,
    MAX_MAP_ZOOM,
    MAX_TILE_ZOOM,
    MIN_MAP_ZOOM,
    MIN_TILE_ZOOM,
    default_download_dir,
    default_download_root,
)
from .coordinates import CoordinateSpinBox
from .download_estimate import estimate_download_bytes, estimated_tile_bytes
from .download_levels import format_levels, parse_levels, ranges_for_levels
from .regions import REGION_MAP
from .tasks import TaskStore
from .task_manager import TaskManagerDialog
from .theme import set_state, theme_manager
from .workspace_ui import WorkspaceUi
from .paths import directory_size, format_bytes, format_duration, resource_path
from .providers import (
    DEFAULT_SOURCE_KEY,
    PROVIDER_MAP,
    SOURCE_MAP,
    providers_for_source,
)
from .tile_math import (
    TileRange,
    format_bounds,
    range_from_bounds,
    range_from_rows_cols,
    tile_to_latlng,
    latlng_to_pixel,
    pixel_to_latlng,
    zoom_to_fit,
)
from .workers import (
    DownloadOptions,
    DownloadWorker,
    MAX_OUTPUT_DIMENSION,
    MAX_OUTPUT_PIXELS,
    StitchOptions,
    StitchWorker,
    SUPPORTED_OUTPUT_SUFFIXES,
    WARN_OUTPUT_PIXELS,
    output_image_format,
)

MAX_TILE_WARNING = 20_000
MAX_TILE_HARD_LIMIT = 1_000_000


class MainWindow(WorkspaceUi, QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._settings = QSettings()
        self._theme_manager = theme_manager()
        self._range: TileRange | None = None
        self._updating_range = False
        self._applying_region_preset = False
        self._download_worker: DownloadWorker | None = None
        self._stitch_worker: StitchWorker | None = None
        self._download_started_at = 0.0
        self._stitch_started_at = 0.0
        self._range_locked = False
        self._closing = False
        self._allow_close = False
        self._loading_settings = True
        self._last_provider_by_source: dict[str, str] = {}
        self._download_task_id: str | None = None
        self._download_queue: list[tuple[DownloadOptions, str]] = []
        self._batch_task_ids: list[str] = []
        self._batch_total = 0
        self._batch_processed = 0
        self._batch_failed = 0
        self._batch_cancelled = False
        self._batch_started_at = 0.0
        self._batch_position = 0
        self._task_manager: TaskManagerDialog | None = None
        self._task_store_error = ""
        try:
            self._task_store = TaskStore()
            self._task_store.recover_interrupted()
        except Exception as exc:
            self._task_store = None
            self._task_store_error = str(exc)

        self._close_timer = QTimer(self)
        self._close_timer.setInterval(200)
        self._close_timer.timeout.connect(self._poll_close_tasks)

        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(1024, 680)
        self.resize(1480, 940)

        icon_path = resource_path("assets/icon.png")
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_ui()
        self._build_menus()
        self._connect_signals()
        self._restore_settings()
        self._theme_manager.changed.connect(self._on_theme_changed)
        self._on_theme_changed()
        self._refresh_task_summary()
        self.statusBar().showMessage("就绪")

    # ------------------------------------------------------------------
    # 界面构建
    # ------------------------------------------------------------------
    def _build_log_group(self) -> QGroupBox:
        group, layout = self._make_card("运行日志")
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)
        self.log_view.setFixedHeight(104)
        self.log_view.setPlaceholderText("图源、图层切换及下载、拼接和错误信息会显示在这里")
        layout.addWidget(self.log_view)
        return group

    def _build_menus(self) -> None:
        menu_bar = QMenuBar(self)
        self._application_menu_bar = menu_bar
        menu_bar.setNativeMenuBar(False)
        menu_bar.setObjectName("appMenuBar")
        file_menu = menu_bar.addMenu("文件")
        self.act_open_output = QAction("打开下载目录", self)
        self.act_open_output.triggered.connect(self._open_download_dir)
        file_menu.addAction(self.act_open_output)

        self.act_open_cache = QAction("打开瓦片缓存目录", self)
        self.act_open_cache.triggered.connect(self._open_cache_dir)
        file_menu.addAction(self.act_open_cache)
        file_menu.addSeparator()

        self.act_quit = QAction("退出", self)
        self.act_quit.setShortcut("Ctrl+Q")
        self.act_quit.triggered.connect(self.close)
        file_menu.addAction(self.act_quit)

        tools_menu = menu_bar.addMenu("工具")
        self.act_tasks = QAction("下载任务管理", self)
        self.act_tasks.setShortcut("Ctrl+J")
        self.act_tasks.triggered.connect(self._show_task_manager)
        tools_menu.addAction(self.act_tasks)
        self.act_clear_cache = QAction("清理瓦片缓存", self)
        self.act_clear_cache.triggered.connect(self._clear_tile_cache)
        tools_menu.addAction(self.act_clear_cache)

        self.act_log = QAction("运行日志", self)
        self.act_log.setCheckable(True)
        self.act_log.setShortcut("Ctrl+L")
        self.act_log.toggled.connect(self._set_log_visible)
        tools_menu.addAction(self.act_log)

        help_menu = menu_bar.addMenu("帮助")
        self.act_help = QAction("使用说明", self)
        self.act_help.setShortcut("F1")
        self.act_help.triggered.connect(self._show_help)
        help_menu.addAction(self.act_help)

        self.act_about = QAction("关于", self)
        self.act_about.triggered.connect(self._show_about)
        help_menu.addAction(self.act_about)

        # 保留键盘菜单入口，避免为了简洁标签而丢失原有 Alt 快捷键。
        self._menu_shortcuts = []
        for menu, key in ((file_menu, "F"), (tools_menu, "T"), (help_menu, "H")):
            shortcut = QShortcut(QKeySequence(f"Alt+{key}"), self)
            shortcut.activated.connect(lambda selected_menu=menu: self._open_app_menu(selected_menu))
            self._menu_shortcuts.append(shortcut)
            menu.menuAction().setToolTip(f"{menu.title()}（Alt+{key}）")
        self._build_app_header(menu_bar)

    def _open_app_menu(self, menu: QMenu) -> None:
        menu_bar = self.menuBar()
        menu_bar.setActiveAction(menu.menuAction())
        if not menu.isVisible():
            menu.popup(menu_bar.mapToGlobal(menu_bar.actionGeometry(menu.menuAction()).bottomLeft()))

    def _apply_api_key(self) -> None:
        self.map.set_api_key(self.api_key_edit.text())
        self._on_provider_changed()

    def _make_coord_spin(self, value: float, minimum: float, maximum: float) -> CoordinateSpinBox:
        spin = CoordinateSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def _make_tile_spin(self) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, (1 << MAX_TILE_ZOOM) - 1)
        spin.setAlignment(Qt.AlignRight)
        spin.setGroupSeparatorShown(True)
        spin.setKeyboardTracking(False)
        spin.setMinimumWidth(96)
        return spin

    # ------------------------------------------------------------------
    # 信号连接
    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        self.theme_combo.currentIndexChanged.connect(
            lambda _index: self._theme_manager.set_mode(str(self.theme_combo.currentData()))
        )
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.grid_check.toggled.connect(self._on_grid_toggled)
        self.zoom_spin.valueChanged.connect(self._on_zoom_spin_changed)

        self.map.positionChanged.connect(self._on_map_position)
        self.map.zoomChanged.connect(self._on_map_zoom_changed)
        self.map.selectionChanged.connect(self._on_map_selection)
        self.map.selectionModeChanged.connect(self._on_selection_mode_changed)
        self.map.selectionCleared.connect(self._on_selection_cleared)

        self.act_reset.triggered.connect(self.map.reset_view)
        self.act_use_view.triggered.connect(self._use_current_view)

        self.btn_draw.clicked.connect(self._begin_selection)
        self.btn_clear_draw.clicked.connect(self._clear_selection)
        self.btn_use_view.clicked.connect(self._use_current_view)
        self.btn_locate.clicked.connect(self._locate_tile_range)
        self.region_combo.currentIndexChanged.connect(self._on_region_changed)
        self.coord_format_combo.currentIndexChanged.connect(self._on_coordinate_format_changed)

        for spin in (self.north_spin, self.south_spin, self.west_spin, self.east_spin):
            spin.valueChanged.connect(self._on_extent_spin_changed)
        self.level_spin.valueChanged.connect(self._on_level_changed)
        self.multi_level_check.toggled.connect(self._on_multi_levels_changed)
        self.multi_levels_edit.textChanged.connect(self._update_level_summary)
        for spin in (self.row_min_spin, self.row_max_spin, self.col_min_spin, self.col_max_spin):
            spin.valueChanged.connect(self._on_tile_range_spin_changed)

        self.btn_download_browse.clicked.connect(self._choose_download_dir)
        self.btn_download_start.clicked.connect(self._start_download)
        self.btn_download_stop.clicked.connect(self._stop_download)

        self.btn_stitch_input_browse.clicked.connect(self._choose_stitch_input)
        self.btn_stitch_output_browse.clicked.connect(self._choose_stitch_output)
        self.btn_stitch_start.clicked.connect(self._start_stitch)
        self.btn_stitch_stop.clicked.connect(self._stop_stitch)
        self.btn_toggle_log.toggled.connect(self._set_log_visible)

    # ------------------------------------------------------------------
    # 设置持久化
    # ------------------------------------------------------------------
    def _restore_settings(self) -> None:
        settings = self._settings
        self._theme_manager.restore()
        geometry = settings.value("window/geometry")
        restored = False
        if geometry is not None:
            restored = self.restoreGeometry(geometry)
        if not restored:
            self._apply_default_geometry()

        splitter_state = settings.value("window/splitter")
        if splitter_state is not None:
            self._splitter.restoreState(splitter_state)
        self._splitter.setHandleWidth(1)

        source_key = str(settings.value("map/source", DEFAULT_SOURCE_KEY) or DEFAULT_SOURCE_KEY)
        if source_key not in SOURCE_MAP:
            source_key = DEFAULT_SOURCE_KEY
        source_index = self.source_combo.findData(source_key)
        with QSignalBlocker(self.source_combo):
            self.source_combo.setCurrentIndex(max(0, source_index))
        source_key = str(self.source_combo.currentData())
        source = SOURCE_MAP[source_key]

        saved_provider = str(settings.value("map/provider", source.default_provider_key) or "")
        self._populate_provider_combo(source_key, preferred=saved_provider)
        provider_key = str(self.provider_combo.currentData() or source.default_provider_key)
        self.map.set_provider(provider_key)
        self._on_provider_changed()

        grid = self._get_bool("map/grid", False)
        self.grid_check.setChecked(grid)

        zoom = max(MIN_MAP_ZOOM, min(MAX_MAP_ZOOM, self._get_int("map/zoom", DEFAULT_ZOOM)))
        self.map.set_zoom(zoom)
        with QSignalBlocker(self.zoom_spin):
            self.zoom_spin.setValue(self.map.zoom)
        self._zoom_label.setText(f"级别 {self.map.zoom}")

        level = max(MIN_TILE_ZOOM, min(MAX_TILE_ZOOM, self._get_int("range/level", DEFAULT_ZOOM)))
        self.level_spin.setValue(level)

        north = self._get_float("range/north", DEFAULT_NORTH)
        south = self._get_float("range/south", DEFAULT_SOUTH)
        west = self._get_float("range/west", DEFAULT_WEST)
        east = self._get_float("range/east", DEFAULT_EAST)
        # 每次启动默认选自定义区域，只恢复范围数值，不恢复国家预设或自动画框。
        self._set_extent_values(north, south, west, east, sync_map=False)
        coordinate_format = str(settings.value("ui/coordinate_format", "decimal"))
        self.coord_format_combo.setCurrentIndex(max(0, self.coord_format_combo.findData(coordinate_format)))

        download_dir = str(settings.value("download/dir", "") or "").strip()
        managed_root = default_download_root()
        try:
            download_path = Path(download_dir).expanduser() if download_dir else None
        except (OSError, ValueError):
            download_path = None
        if (
            download_path is None
            or download_path == managed_root
            or (download_path.parent == managed_root and download_path.name in PROVIDER_MAP)
        ):
            download_dir = str(default_download_dir(provider_key))
        self._set_download_dir_text(download_dir)
        self.concurrency_spin.setValue(
            max(1, min(MAX_CONCURRENCY, self._get_int("download/concurrency", DEFAULT_CONCURRENCY)))
        )
        self.skip_check.setChecked(self._get_bool("download/skip_existing", True))
        self.multi_levels_edit.setText(str(settings.value("download/levels", "") or ""))
        self.multi_level_check.setChecked(self._get_bool("download/multi_level", False))

        self.stitch_input_edit.setText(str(settings.value("stitch/input", "") or ""))
        self.stitch_output_edit.setText(str(settings.value("stitch/output", "") or ""))
        if self.stitch_input_edit.text().strip() and not self.stitch_output_edit.text().strip():
            self.stitch_output_edit.setText(
                str(Path(self.stitch_input_edit.text().strip()) / f"{self.level_spin.value()}.png")
            )

        self._set_log_visible(self._get_bool("ui/log_visible", False))
        self._on_map_position(self.map.center_latitude, self.map.center_longitude)
        self._loading_settings = False

    def _save_settings(self) -> None:
        settings = self._settings
        settings.setValue("window/geometry", self.saveGeometry())
        settings.setValue("window/splitter", self._splitter.saveState())
        settings.setValue("map/source", self.source_combo.currentData())
        settings.setValue("map/provider", self.provider_combo.currentData())
        settings.setValue("map/zoom", self.map.zoom)
        settings.setValue("map/grid", self.grid_check.isChecked())
        settings.setValue("range/north", self.north_spin.value())
        settings.setValue("range/south", self.south_spin.value())
        settings.setValue("range/west", self.west_spin.value())
        settings.setValue("range/east", self.east_spin.value())
        settings.setValue("range/level", self.level_spin.value())
        settings.setValue("ui/coordinate_format", self.coord_format_combo.currentData())
        settings.setValue("download/dir", self.download_dir_edit.text().strip())
        settings.setValue("download/concurrency", self.concurrency_spin.value())
        settings.setValue("download/skip_existing", self.skip_check.isChecked())
        settings.setValue("download/multi_level", self.multi_level_check.isChecked())
        settings.setValue("download/levels", self.multi_levels_edit.text().strip())
        settings.setValue("stitch/input", self.stitch_input_edit.text().strip())
        settings.setValue("stitch/output", self.stitch_output_edit.text().strip())
        settings.setValue("ui/log_visible", self.btn_toggle_log.isChecked())
        settings.setValue("ui/theme", self._theme_manager.mode)
        settings.sync()

    def _get_bool(self, key: str, default: bool) -> bool:
        value = self._settings.value(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _get_int(self, key: str, default: int) -> int:
        value = self._settings.value(key, default)
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _get_float(self, key: str, default: float) -> float:
        value = self._settings.value(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _apply_default_geometry(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1480, 940)
            return
        available = screen.availableGeometry()
        width = min(1480, max(1024, available.width() - 80))
        height = min(940, max(680, available.height() - 80))
        self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    # ------------------------------------------------------------------
    # 地图交互
    # ------------------------------------------------------------------
    def _populate_provider_combo(self, source_key: str, preferred: str | None = None) -> None:
        source = SOURCE_MAP.get(source_key)
        if source is None:
            return
        providers = providers_for_source(source_key)
        provider_keys = {provider.key for provider in providers}
        target_key = preferred if preferred in provider_keys else source.default_provider_key

        with QSignalBlocker(self.provider_combo):
            self.provider_combo.clear()
            for provider in providers:
                self.provider_combo.addItem(provider.name, provider.key)
                self.provider_combo.setItemData(
                    self.provider_combo.count() - 1,
                    provider.description,
                    Qt.ToolTipRole,
                )
            index = self.provider_combo.findData(target_key)
            self.provider_combo.setCurrentIndex(max(0, index))
        self.provider_combo.setToolTip(f"{source.name}图层类型（{source.coordinate_system}）")

    def _on_source_changed(self, _index: int | None = None) -> None:
        if self._loading_settings:
            return
        source_key = str(self.source_combo.currentData() or DEFAULT_SOURCE_KEY)
        source = SOURCE_MAP.get(source_key)
        if source is None:
            return
        self._populate_provider_combo(
            source_key,
            preferred=self._last_provider_by_source.get(source_key),
        )
        self._on_provider_changed()
        self.statusBar().showMessage(
            f"已切换到 {source.name}（{source.coordinate_system}）；"
            "当前经纬度不会自动转换，请按图源坐标基准输入",
            10000,
        )

    def _set_download_dir_text(self, text: str) -> None:
        self.download_dir_edit.setText(text)
        # 路径较长时优先显示末尾（图源子目录），完整路径在工具提示里。
        self.download_dir_edit.setCursorPosition(len(text))
        self.download_dir_edit.setToolTip(
            f"当前输出目录：\n{text}\n\n"
            "下载目录结构：输出目录 / 级别 / 行 / 列.png\n"
            "默认按图源类型分目录，避免不同图源的瓦片互相覆盖"
        )

    def _maybe_update_default_output(self, provider) -> None:
        """使用默认输出目录时，按图源类型自动切换子目录，避免瓦片混用。"""
        if self._loading_settings:
            return
        root = default_download_root()
        target = default_download_dir(provider.key)
        current = self.download_dir_edit.text().strip()
        if not current:
            self._set_download_dir_text(str(target))
            return
        try:
            path = Path(current).expanduser()
        except (OSError, ValueError):
            return
        if path == root or (path.parent == root and path.name in PROVIDER_MAP):
            self._set_download_dir_text(str(target))

    def _on_provider_changed(self, _index: int | None = None) -> None:
        key = self.provider_combo.currentData()
        if not key:
            return
        previous_provider = self.map.provider
        try:
            self.map.set_provider(str(key))
        except ValueError:
            return
        provider = PROVIDER_MAP.get(str(key))
        if provider is None:
            return

        self.zoom_spin.setMaximum(provider.max_zoom)
        self.level_spin.setMaximum(provider.max_zoom)
        self.level_slider.setMaximum(provider.max_zoom)
        self._level_max_label.setText(f"{provider.max_zoom} · 近景")
        self.key_panel.setVisible(provider.requires_key)
        description = provider.description
        if provider.requires_key and not self.api_key_edit.text().strip():
            description = "中文卫星注记 · 先填写下方 Key，即可加载与下载。"
        self.layer_description.setText(description)
        self.layer_choices.setToolTip(description)
        self._refresh_layer_buttons()

        source = SOURCE_MAP.get(provider.source)
        source_name = source.name if source is not None else provider.source
        self._last_provider_by_source[provider.source] = provider.key
        self._provider_label.setText(
            f"{provider.coordinate_system} · {source_name} {provider.name}"
        )
        tooltip = f"{provider.description}\n坐标系：{provider.coordinate_system}"
        if provider.source == "google":
            tooltip += "\n需要能够访问 Google 的网络环境"
        self._provider_label.setToolTip(tooltip)
        self._update_level_summary()

        if not self._loading_settings:
            self._maybe_update_default_output(provider)
            if previous_provider.key != provider.key:
                action = "切换图源" if previous_provider.source != provider.source else "切换图层"
                self._append_log(f"{action}：{source_name} · {provider.name}（{provider.coordinate_system}）")
            self.statusBar().showMessage(f"地图图源：{source_name} · {provider.name}", 4000)

    def _on_grid_toggled(self, checked: bool) -> None:
        self.map.set_grid_visible(bool(checked))

    def _on_zoom_spin_changed(self, value: int) -> None:
        if self.map.zoom != value:
            self.map.set_zoom(value)

    def _on_map_position(self, latitude: float, longitude: float) -> None:
        self._coord_label.setText(f"经度 {longitude:+.6f}   纬度 {latitude:+.6f}")
        self._position_map_overlays()

    def _on_map_zoom_changed(self, zoom: int) -> None:
        with QSignalBlocker(self.zoom_spin):
            self.zoom_spin.setValue(zoom)
        self._zoom_label.setText(f"级别 {zoom}")
        if self._range_locked:
            return
        with QSignalBlocker(self.level_spin):
            self.level_spin.setValue(zoom)
        self._update_tile_range()

    def _on_map_selection(self, north: float, south: float, west: float, east: float) -> None:
        self._set_extent_values(north, south, west, east, sync_map=False)
        self._append_log("框选范围：" + format_bounds(north, south, west, east))
        self.statusBar().showMessage("框选完成，已更新瓦片范围", 5000)

    def _on_selection_mode_changed(self, active: bool) -> None:
        if active:
            self.btn_draw.setEnabled(False)
            self.btn_draw.setText("框选：请点两个点")
            self.btn_clear_draw.setText("取消框选")
            self.statusBar().showMessage("框选模式：请在地图上点击两个对角点（Esc 取消）")
        else:
            self.btn_draw.setEnabled(True)
            self.btn_draw.setText("开始框选")
            self.btn_clear_draw.setText("清空框选")

    def _on_selection_cleared(self) -> None:
        self.statusBar().showMessage("已清除地图框选", 4000)

    def _begin_selection(self) -> None:
        if self._closing:
            return
        self.map.start_selection()

    def _clear_selection(self) -> None:
        self.map.clear_selection()

    def _use_current_view(self) -> None:
        if self._closing:
            return
        north, south, west, east = self.map.visible_bounds()
        self._set_extent_values(north, south, west, east, sync_map=True)
        self.statusBar().showMessage("已用当前地图视野更新框选范围", 5000)

    def _locate_tile_range(self) -> None:
        north = self.north_spin.value()
        south = self.south_spin.value()
        west = self.west_spin.value()
        east = self.east_spin.value()
        self.map.show_bounds(north, south, west, east, zoom=self.level_spin.value())
        self.statusBar().showMessage(f"已定位到 Z{self.level_spin.value()} 的框选范围", 4000)

    # ------------------------------------------------------------------
    # 范围计算
    # ------------------------------------------------------------------
    def _extent_values(self) -> tuple[float, float, float, float]:
        return tuple(spin.value() for spin in (self.north_spin, self.south_spin, self.west_spin, self.east_spin))

    def _on_coordinate_format_changed(self, _index: int) -> None:
        coordinate_format = str(self.coord_format_combo.currentData())
        for spin in (self.north_spin, self.south_spin, self.west_spin, self.east_spin):
            spin.set_coordinate_format(coordinate_format)

    def _on_region_changed(self, _index: int) -> None:
        preset = REGION_MAP.get(str(self.region_combo.currentData()))
        if preset is None or self._range_locked:
            return
        self._applying_region_preset = True
        try:
            self.map.clear_selection()
            self._set_extent_values(*preset.bounds, sync_map=True)
            zoom = min(self.map.provider.max_zoom, zoom_to_fit(*preset.bounds, self.map.width(), self.map.height()))
            self.map.show_bounds(*preset.bounds, zoom=zoom)
            # 纬度在墨卡托投影中并不等距，按投影中心定位，确保高纬度预设也完整可见。
            x1, y1 = latlng_to_pixel(preset.north, preset.west, zoom)
            x2, y2 = latlng_to_pixel(preset.south, preset.east, zoom)
            latitude, longitude = pixel_to_latlng((x1 + x2) / 2, (y1 + y2) / 2, zoom)
            self.map.set_center(latitude, longitude)
        finally:
            self._applying_region_preset = False
        self.statusBar().showMessage(f"已选择{preset.name}，可调整四至和下载级别", 5000)

    def _mark_region_custom(self) -> None:
        if self._applying_region_preset:
            return
        with QSignalBlocker(self.region_combo):
            self.region_combo.setCurrentIndex(0)

    def _on_extent_spin_changed(self, _value: float) -> None:
        if self._updating_range:
            return
        self._mark_region_custom()
        self._update_tile_range()
        self.map.set_selection(
            self.north_spin.value(),
            self.south_spin.value(),
            self.west_spin.value(),
            self.east_spin.value(),
        )

    def _on_level_changed(self, value: int) -> None:
        if self._updating_range:
            return
        self._update_tile_range()
        self.statusBar().showMessage(f"下载级别：Z{value}", 3000)

    def _on_tile_range_spin_changed(self, _value: int) -> None:
        if self._updating_range:
            return
        self._mark_region_custom()
        self._refresh_tile_range_from_spins()

    def _set_extent_values(
        self,
        north: float,
        south: float,
        west: float,
        east: float,
        sync_map: bool,
    ) -> None:
        self._mark_region_custom()
        self._updating_range = True
        try:
            for spin, value in (
                (self.north_spin, north),
                (self.south_spin, south),
                (self.west_spin, west),
                (self.east_spin, east),
            ):
                with QSignalBlocker(spin):
                    spin.setValue(value)
        finally:
            self._updating_range = False
        self._update_tile_range()
        if sync_map:
            self.map.set_selection(
                self.north_spin.value(),
                self.south_spin.value(),
                self.west_spin.value(),
                self.east_spin.value(),
            )

    def _update_tile_range(self) -> None:
        if self._updating_range:
            return
        try:
            tile_range = range_from_bounds(
                self.north_spin.value(),
                self.south_spin.value(),
                self.west_spin.value(),
                self.east_spin.value(),
                self.level_spin.value(),
            )
        except (TypeError, ValueError) as exc:
            self._set_invalid_range(str(exc))
            return

        self._updating_range = True
        try:
            for spin, value in (
                (self.row_min_spin, tile_range.min_row),
                (self.row_max_spin, tile_range.max_row),
                (self.col_min_spin, tile_range.min_col),
                (self.col_max_spin, tile_range.max_col),
            ):
                with QSignalBlocker(spin):
                    spin.setValue(value)
        finally:
            self._updating_range = False
        self._set_current_range(tile_range)

    def _refresh_tile_range_from_spins(self) -> None:
        try:
            tile_range = range_from_rows_cols(
                self.level_spin.value(),
                self.row_min_spin.value(),
                self.row_max_spin.value(),
                self.col_min_spin.value(),
                self.col_max_spin.value(),
            )
        except (TypeError, ValueError) as exc:
            self._set_invalid_range(str(exc))
            return
        self._set_current_range(tile_range)

    def _display_tile_count(self, count: int | None = None, *, levels: int | None = None,
                            error: str = "", tooltip: str = "") -> None:
        if count is None:
            self.tile_count_label.setText(error or "—")
            self.tile_count_detail.clear()
        else:
            value, unit = f"{count:,}", "张"
            for divisor, suffix in ((10**12, "万亿张"), (10**8, "亿张"), (10**4, "万张")):
                if count >= divisor:
                    value = f"{count / divisor:,.2f}".rstrip("0").rstrip(".")
                    unit = suffix
                    break
            self.tile_count_label.setText(value)
            detail = f"{unit} · {levels} 级" if levels is not None else unit
            self.tile_count_detail.setText(f"{detail} · {error}" if error else detail)
            exact = f"{count:,} 张" + (f" · {levels} 级" if levels is not None else "")
            tooltip = f"{exact}\n{tooltip}".rstrip()
        set_state(self.tile_count_label, "state", "error" if error else "")
        self.tile_count_card.setToolTip(tooltip)
        self.tile_count_label.setToolTip(tooltip)
        self.tile_count_detail.setToolTip(tooltip)

    def _set_invalid_range(self, message: str) -> None:
        self._range = None
        self.image_size_label.clear()
        self._display_tile_count(error="范围无效", tooltip=message)
        self._update_level_summary()

    def _set_current_range(self, tile_range: TileRange) -> None:
        if not tile_range.is_within_world:
            self._range = None
            self.image_size_label.clear()
            limit = (1 << tile_range.zoom) - 1
            self._display_tile_count(tile_range.count, error="超出范围",
                                     tooltip=f"Z{tile_range.zoom} 的有效行/列范围是 0 ~ {limit}")
            self._update_level_summary()
            return

        self._range = tile_range
        self._update_level_summary()

    def _on_multi_levels_changed(self, checked: bool) -> None:
        self.multi_levels_panel.setVisible(checked)
        self.multi_levels_edit.setVisible(checked)
        self.multi_levels_hint.setVisible(checked)
        if checked and not self.multi_levels_edit.text().strip():
            self.multi_levels_edit.setText(str(self.level_spin.value()))
        self._update_level_summary()

    def _selected_download_ranges(self) -> tuple[TileRange, ...]:
        if self._range is None:
            raise ValueError("请先设置有效的下载范围")
        if not self.multi_level_check.isChecked():
            return (self._range,)
        levels = parse_levels(self.multi_levels_edit.text(), self.map.provider.max_zoom)
        bounds = (self.north_spin.value(), self.south_spin.value(), self.west_spin.value(), self.east_spin.value())
        return ranges_for_levels(levels, bounds, self._range)

    def _update_level_summary(self, *_args) -> None:
        with QSignalBlocker(self.level_slider):
            self.level_slider.setValue(self.level_spin.value())
        self.download_size_label.clear()
        self.download_size_label.setToolTip("")
        if not self.multi_level_check.isChecked():
            self.download_plan_label.clear()
            self.multi_levels_panel.hide()
            if self._range is not None:
                self.image_size_label.setText(
                    f"{self._range.pixel_width:,} × {self._range.pixel_height:,} px"
                )
                self._display_tile_count(self._range.count, tooltip=(
                    f"行 {self._range.min_row} ~ {self._range.max_row}，列 {self._range.min_col} ~ {self._range.max_col}\n"
                    f"拼接 {self._range.pixel_width:,} × {self._range.pixel_height:,} px"
                ))
                self._update_download_estimate((self._range,))
            return
        try:
            ranges = self._selected_download_ranges()
            total = sum(item.count for item in ranges)
            self.image_size_label.setText(f"Z{format_levels(item.zoom for item in ranges)}\n逐级拼接")
            self._display_tile_count(total, levels=len(ranges), tooltip="\n".join(
                f"Z{item.zoom}：{item.count:,} 张；拼接 {item.pixel_width:,} × {item.pixel_height:,} px"
                for item in ranges
            ))
            self._update_download_estimate(ranges)
            if total > MAX_TILE_HARD_LIMIT:
                self.download_plan_label.setText("超过单次瓦片总数上限，请减少级别或缩小范围。")
                self.multi_levels_panel.show()
            else:
                self.download_plan_label.clear()
                self.multi_levels_panel.hide()
        except ValueError as exc:
            self.download_plan_label.setText(str(exc))
            self.multi_levels_panel.show()

    def _update_download_estimate(self, ranges: tuple[TileRange, ...]) -> None:
        provider = self.map.provider
        estimated = estimate_download_bytes(provider, ranges)
        self.download_size_label.setText(f"≈ {format_bytes(estimated)}")
        details = [
            f"Z{item.zoom}：{item.count:,} 张 × 约 {format_bytes(estimated_tile_bytes(provider, item.zoom))}"
            f" ≈ {format_bytes(estimate_download_bytes(provider, (item,)))}"
            for item in ranges
        ]
        self.download_size_label.setToolTip(
            "按图层类型、级别和常见压缩瓦片大小粗估网络流量，实际大小随地区变化。\n"
            + "\n".join(details)
            + "\n按完整范围估算，未扣除已有瓦片，不含重试。"
            + "\n卫星与标注分别请求的流量已计入；合成 PNG 后的磁盘占用可能更大。"
        )

    # ------------------------------------------------------------------
    # 下载
    # ------------------------------------------------------------------
    def _start_download(self) -> None:
        if self._closing or self._download_worker is not None:
            return
        if self.map.provider.requires_key and not self.api_key_edit.text().strip():
            self.api_key_edit.setFocus()
            self.statusBar().showMessage("请先填写天地图 Key，然后开始下载。", 8000)
            return
        if self._stitch_worker is not None:
            QMessageBox.information(self, "请稍候", "拼接任务正在进行，请等待完成后再开始下载。")
            return
        if self.map.selection_mode:
            QMessageBox.information(self, "框选未完成", "请先完成框选，或按 Esc 取消框选。")
            return

        self._refresh_tile_range_from_spins()
        tile_range = self._range
        if tile_range is None:
            QMessageBox.warning(self, "瓦片范围无效", "请检查经纬度/瓦片范围后再试。")
            return

        try:
            ranges = self._selected_download_ranges()
        except ValueError as exc:
            QMessageBox.warning(self, "下载级别无效", str(exc))
            self.multi_levels_edit.setFocus()
            return

        output_text = self.download_dir_edit.text().strip()
        if not output_text:
            QMessageBox.warning(self, "缺少输出目录", "请选择瓦片输出目录。")
            return
        output_dir = Path(output_text).expanduser()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "无法创建输出目录", f"{output_dir}\n\n{exc}")
            return
        if not output_dir.is_dir():
            QMessageBox.critical(self, "输出目录无效", f"{output_dir} 不是目录。")
            return

        total = sum(item.count for item in ranges)
        if total > MAX_TILE_HARD_LIMIT:
            QMessageBox.warning(
                self,
                "下载范围过大",
                f"当前范围包含 {total:,} 张瓦片，超过单次 {MAX_TILE_HARD_LIMIT:,} 张的安全上限。\n"
                "请减少级别、缩小框选范围，或分开下载。",
            )
            return

        provider_key = str(self.provider_combo.currentData() or "")
        provider = PROVIDER_MAP.get(provider_key)
        if provider is None:
            QMessageBox.warning(self, "地图类型无效", "请重新选择地图类型。")
            return
        source = SOURCE_MAP.get(provider.source)
        source_name = source.name if source is not None else provider.source
        display_name = f"{source_name} {provider.name}"
        levels_text = format_levels(item.zoom for item in ranges)

        if total > MAX_TILE_WARNING:
            estimated = format_bytes(estimate_download_bytes(provider, ranges))
            if not self._confirm(
                "确认下载",
                f"即将下载 {total:,} 张瓦片（{display_name} · Z{levels_text}），"
                f"预计下载流量约 {estimated}（粗估，未扣除已有瓦片）。\n\n是否继续？",
                "开始下载",
            ):
                return

        options = [DownloadOptions(
            output_dir=str(output_dir),
            provider=provider,
            tile_range=item,
            concurrency=self.concurrency_spin.value(),
            skip_existing=self.skip_check.isChecked(),
            api_key=self.api_key_edit.text().strip(),
        ) for item in ranges]
        if self._task_store is None:
            QMessageBox.warning(self, "无法保存下载任务", self._task_store_error)
            return
        try:
            task_ids = self._task_store.create_batch(options, display_name)
        except Exception as exc:
            QMessageBox.warning(self, "无法保存下载任务", str(exc))
            return
        self._launch_download_sequence(list(zip(options, task_ids)))

    def _launch_download_sequence(self, queue: list[tuple[DownloadOptions, str]], multi: bool = False) -> None:
        if len(queue) == 1 and not multi:
            self._batch_task_ids = []
            self._launch_download(*queue[0])
            return
        self._download_queue = list(queue)
        self._batch_task_ids = [task_id for _options, task_id in queue]
        self._batch_total = sum(options.tile_range.count for options, _task_id in queue)
        self._batch_processed = self._batch_failed = 0
        self._batch_cancelled = False
        self._batch_started_at = time.monotonic()
        self._batch_position = 0
        self._launch_next_download()

    def _launch_next_download(self) -> None:
        options, task_id = self._download_queue.pop(0)
        self._batch_position += 1
        self._launch_download(options, task_id)
        if self._download_worker is None:
            # 任务库等启动错误发生时，不留下还会自动执行的隐藏队列。
            self._download_queue.clear()
            self._batch_task_ids = []
            self._set_download_running(False)

    def _launch_download(self, options: DownloadOptions, task_id: str) -> None:
        assert self._task_store is not None
        try:
            self._task_store.start(task_id)
        except Exception as exc:
            QMessageBox.warning(self, "无法更新下载任务", str(exc))
            return
        options = replace(options, task_id=task_id, task_db=str(self._task_store.path))
        total = options.tile_range.count
        source = SOURCE_MAP[options.provider.source]
        display_name = f"{source.name} {options.provider.name}"
        self._download_task_id = task_id
        worker = DownloadWorker(options, self)
        worker.progress.connect(self._on_download_progress)
        worker.log.connect(self._append_log)
        worker.completed.connect(self._on_download_completed)
        worker.finished.connect(worker.deleteLater)
        self._download_worker = worker
        self._download_started_at = time.monotonic()
        self.download_progress.setRange(0, max(1, self._batch_total if self._batch_task_ids else total))
        self.download_progress.setValue(self._batch_processed if self._batch_task_ids else 0)
        self._set_task_status(
            self.download_status,
            f"准备下载 {total:,} 张瓦片（{display_name} · Z{options.tile_range.zoom}）…",
            "busy",
        )
        self._append_log(
            f"下载任务：{display_name} · Z{options.tile_range.zoom} · {total:,} 张 → {options.output_dir}"
        )
        self._set_download_running(True)
        self._refresh_task_summary()
        worker.start()

    def _refresh_task_summary(self) -> None:
        if self._task_store is None:
            self.btn_task_manager.setText("任务记录不可用")
            self.btn_task_manager.setToolTip(self._task_store_error)
            return
        try:
            tasks = self._task_store.list_groups()
            unfinished = sum(task["status"] != "completed" for task in tasks)
            self.btn_task_manager.setText(f"任务管理 · {len(tasks)}")
            self.btn_task_manager.setToolTip(f"{len(tasks)} 个任务，{unfinished} 个未完成；Ctrl+J 打开")
        except Exception as exc:
            self.btn_task_manager.setToolTip(str(exc))

    def _show_task_manager(self) -> None:
        if self._task_store is None:
            QMessageBox.warning(self, "无法读取下载任务", self._task_store_error)
            return
        if self._task_manager is None:
            self._task_manager = TaskManagerDialog(self._task_store, self)
            self._task_manager.resumeRequested.connect(self._resume_download_task)
            self._task_manager.tasksChanged.connect(self._refresh_task_summary)
        self._task_manager.refresh(self._download_task_id)
        self._task_manager.show()
        self._task_manager.raise_()
        self._task_manager.activateWindow()

    def _resume_download_task(self, task_id: str) -> None:
        if self._closing or self._download_worker is not None or self._stitch_worker is not None:
            self.statusBar().showMessage("请等待当前任务结束后再继续其他下载。", 6000)
            return
        if self._task_store is None:
            return
        try:
            group = self._task_store.group_tasks(task_id)
            pending = [item for item in group if item["status"] != "completed"]
            if not pending:
                self.statusBar().showMessage("这组任务已经全部完成。", 6000)
                return
            task = pending[0]
            provider = PROVIDER_MAP.get(task["provider_key"])
            if provider is None:
                raise ValueError("原任务图源已不可用，无法继续下载。")
            tile_range = range_from_rows_cols(task["zoom"], task["min_row"], task["max_row"],
                                             task["min_col"], task["max_col"])
            if not tile_range.is_within_world or tile_range.count > MAX_TILE_HARD_LIMIT:
                raise ValueError("任务的瓦片范围无效或超出安全上限。")
            if tile_range.zoom > provider.max_zoom:
                raise ValueError("任务级别超过该图源当前支持的最大级别。")
            output_dir = Path(task["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            with QSignalBlocker(self.source_combo):
                self.source_combo.setCurrentIndex(self.source_combo.findData(provider.source))
            self._populate_provider_combo(provider.source, preferred=provider.key)
            self._on_provider_changed()
            self.map.clear_selection()
            self.map.set_zoom(tile_range.zoom)
            self.level_spin.setValue(tile_range.zoom)
            north, west = tile_to_latlng(tile_range.min_col, tile_range.min_row, tile_range.zoom)
            south, east = tile_to_latlng(tile_range.max_col + 1, tile_range.max_row + 1, tile_range.zoom)
            self._set_extent_values(north, south, west, east, sync_map=True)
            self.map.show_bounds(north, south, west, east, zoom=tile_range.zoom)
            for spin, value in ((self.row_min_spin, tile_range.min_row), (self.row_max_spin, tile_range.max_row),
                                (self.col_min_spin, tile_range.min_col), (self.col_max_spin, tile_range.max_col)):
                with QSignalBlocker(spin):
                    spin.setValue(value)
            self._set_current_range(tile_range)
            self._set_download_dir_text(task["output_dir"])
            self.concurrency_spin.setValue(task["concurrency"])
            self.skip_check.setChecked(bool(task["skip_existing"]))
            self.task_tabs.setCurrentIndex(0)
            self.multi_levels_edit.setText(format_levels(item["zoom"] for item in group))
            self.multi_level_check.setChecked(len(group) > 1)
            if provider.requires_key and not self.api_key_edit.text().strip():
                self.statusBar().showMessage("原任务使用天地图，请填写 Key 后再次点击“继续下载”。", 10000)
                if self._task_manager is not None:
                    self._task_manager.hide()
                self.api_key_edit.setFocus()
                return
            queue = []
            for item in pending:
                saved_range = range_from_rows_cols(item["zoom"], item["min_row"], item["max_row"],
                                                   item["min_col"], item["max_col"])
                if not saved_range.is_within_world or saved_range.zoom > provider.max_zoom:
                    raise ValueError("任务级别或范围超过图源支持范围")
                queue.append((DownloadOptions(
                    output_dir=item["output_dir"], provider=provider, tile_range=saved_range,
                    concurrency=item["concurrency"], skip_existing=bool(item["skip_existing"]),
                    api_key=self.api_key_edit.text().strip(),
                ), item["id"]))
            if sum(options.tile_range.count for options, _task_id in queue) > MAX_TILE_HARD_LIMIT:
                raise ValueError("多级任务的瓦片总数超过安全上限")
            self._launch_download_sequence(queue, multi=len(group) > 1)
        except Exception as exc:
            QMessageBox.warning(self, "无法继续下载", str(exc))

    def _stop_download(self) -> None:
        worker = self._download_worker
        if worker is None:
            return
        self._batch_cancelled = True
        self._download_queue.clear()
        worker.cancel()
        self.btn_download_stop.setEnabled(False)
        self._set_task_status(self.download_status, "正在停止下载，请稍候…", "busy")
        self._append_log("已请求停止下载")

    def _on_download_progress(self, done: int, total: int, failed: int) -> None:
        prefix = "下载中"
        started_at = self._download_started_at
        if self._batch_task_ids:
            position = self._batch_position
            zoom = self._download_worker._options.tile_range.zoom if self._download_worker is not None else 0
            prefix = f"Z{zoom} · 第 {position}/{len(self._batch_task_ids)} 级"
            done += self._batch_processed
            failed += self._batch_failed
            total = self._batch_total
            started_at = self._batch_started_at
        total = max(1, total)
        self.download_progress.setMaximum(total)
        self.download_progress.setValue(min(done, total))
        elapsed = max(0.1, time.monotonic() - started_at)
        rate = done / elapsed if done else 0.0
        remaining = (total - done) / rate if rate > 0 else None
        percent = int(done * 100 / total)
        text = (
            f"{prefix} {percent}% · {done:,}/{total:,} 张 · 失败 {failed:,} · "
            f"{rate:.1f} 张/秒 · 剩余 {format_duration(remaining)}"
        )
        self._set_task_status(self.download_status, text, "busy")

    def _on_download_completed(self, ok: bool, message: str, stats: dict) -> None:
        self._download_worker = None
        if self._batch_task_ids:
            self._on_batch_level_completed(ok, message, stats)
            return
        self._set_download_running(False)
        self._refresh_task_summary()
        if self._task_manager is not None:
            self._task_manager.refresh(self._download_task_id)
        processed = int(stats.get("processed", 0) or 0)
        total = int(stats.get("total", 0) or 0)
        success = int(stats.get("success", 0) or 0)
        skipped = int(stats.get("skipped", 0) or 0)
        failed = int(stats.get("failed", 0) or 0)
        zoom = int(stats.get("zoom", self.level_spin.value()) or self.level_spin.value())
        output_dir = str(stats.get("output_dir", "") or "")

        self.download_progress.setMaximum(max(1, total))
        self.download_progress.setValue(processed)
        kind = "ok" if ok else ("normal" if message.startswith("已停止") else "error")
        self._set_task_status(self.download_status, message, kind)
        self._append_log(message)
        if success or skipped:
            self.statusBar().showMessage(f"下载完成：{success:,} 张瓦片", 8000)
            if output_dir:
                if not self.stitch_input_edit.text().strip():
                    self.stitch_input_edit.setText(output_dir)
                if not self.stitch_output_edit.text().strip():
                    self.stitch_output_edit.setText(str(Path(output_dir) / f"{zoom}.png"))

        if not ok and success == 0 and not message.startswith("已停止") and not self._closing:
            QMessageBox.warning(
                self,
                "下载失败",
                f"{message}\n\n请检查网络连接或输出目录权限。"
                + ("\n失败记录已保存，可在“任务管理”中选中原任务继续下载。" if failed else ""),
            )
        elif failed and success:
            self.statusBar().showMessage(f"{failed:,} 张失败，已保存记录，可在“任务管理”中继续下载", 10000)
        if failed and not self._closing:
            self._set_log_visible(True)

    def _on_batch_level_completed(self, ok: bool, message: str, stats: dict) -> None:
        self._batch_processed += int(stats.get("processed", 0))
        self._batch_failed += int(stats.get("failed", 0))
        self._append_log(f"Z{stats.get('zoom')}：{message}")
        self._refresh_task_summary()
        if self._task_manager is not None:
            self._task_manager.refresh(self._download_task_id)
        interrupted = self._batch_cancelled or self._closing or message.startswith("已停止")
        fatal_error = not ok and stats.get("task_status") not in {"partial", "completed", "paused"}
        if self._download_queue and not interrupted and not fatal_error:
            self._launch_next_download()
            return
        self._download_queue.clear()
        self._batch_task_ids = []
        self._set_download_running(False)
        self.download_progress.setMaximum(max(1, self._batch_total))
        self.download_progress.setValue(self._batch_processed)
        if interrupted:
            text, kind = "多级下载已暂停，未完成的级别可在任务管理中继续。", "normal"
        elif fatal_error:
            text, kind = f"多级下载中断：{message}，可在任务管理中继续。", "error"
        elif self._batch_failed:
            text, kind = f"多级下载结束：{self._batch_failed:,} 张失败，已保存记录，可继续补齐。", "error"
        else:
            text, kind = f"多级下载完成：共处理 {self._batch_processed:,} 张瓦片。", "ok"
        self._set_task_status(self.download_status, text, kind)
        self.statusBar().showMessage(text, 10000)
        self._append_log(text)
        output_dir = str(stats.get("output_dir", ""))
        if output_dir:
            if not self.stitch_input_edit.text().strip():
                self.stitch_input_edit.setText(output_dir)
            if not self.stitch_output_edit.text().strip():
                self.stitch_output_edit.setText(str(Path(output_dir) / f"{self.level_spin.value()}.png"))

    def _set_download_running(self, running: bool) -> None:
        common_locked = [
            self.layer_choices,
            self.level_slider,
            self.region_combo,
            self.coord_format_combo,
            self.multi_level_check,
            self.multi_levels_edit,
            self.north_spin,
            self.south_spin,
            self.west_spin,
            self.east_spin,
            self.level_spin,
            self.row_min_spin,
            self.row_max_spin,
            self.col_min_spin,
            self.col_max_spin,
            self.btn_draw,
            self.btn_clear_draw,
            self.btn_use_view,
            self.btn_locate,
        ]
        download_locked = [
            self.api_key_edit,
            self.source_combo,
            self.provider_combo,
            self.download_dir_edit,
            self.btn_download_browse,
            self.concurrency_spin,
            self.skip_check,
        ]
        for widget in common_locked + download_locked:
            widget.setEnabled(not running)
        self.btn_download_start.setEnabled(not running)
        self.btn_download_stop.setEnabled(running)
        self.act_use_view.setEnabled(not running)
        self._range_locked = running or self._stitch_worker is not None
        if running:
            self.btn_stitch_start.setEnabled(False)
        else:
            self.btn_stitch_start.setEnabled(self._stitch_worker is None)

    # ------------------------------------------------------------------
    # 拼接
    # ------------------------------------------------------------------
    def _start_stitch(self) -> None:
        if self._closing or self._stitch_worker is not None:
            return
        if self._download_worker is not None:
            QMessageBox.information(self, "请稍候", "下载任务正在进行，请等待完成后再开始拼接。")
            return
        if self.map.selection_mode:
            QMessageBox.information(self, "框选未完成", "请先完成框选，或按 Esc 取消框选。")
            return

        self._refresh_tile_range_from_spins()
        tile_range = self._range
        if tile_range is None:
            QMessageBox.warning(self, "瓦片范围无效", "请检查经纬度/瓦片范围后再试。")
            return

        tile_root_text = self.stitch_input_edit.text().strip()
        if not tile_root_text:
            QMessageBox.warning(self, "缺少瓦片目录", "请选择包含瓦片的目录。")
            return
        tile_root = Path(tile_root_text).expanduser()
        if not tile_root.is_dir():
            QMessageBox.warning(self, "瓦片目录不存在", f"{tile_root} 不是有效目录。")
            return
        zoom_dir = tile_root / str(tile_range.zoom)
        if not zoom_dir.is_dir():
            QMessageBox.warning(
                self,
                "缺少级别目录",
                f"在瓦片目录下未找到 Z{tile_range.zoom} 子目录：\n{zoom_dir}\n\n"
                "请确认级别和瓦片目录是否匹配。",
            )
            return

        output_text = self.stitch_output_edit.text().strip()
        if not output_text:
            QMessageBox.warning(self, "缺少输出文件", "请选择拼接输出图片路径。")
            return
        output_path = Path(output_text).expanduser()
        if not output_path.suffix:
            output_path = output_path.with_suffix(".png")
            self.stitch_output_edit.setText(str(output_path))
        if output_path.suffix.lower() not in SUPPORTED_OUTPUT_SUFFIXES:
            QMessageBox.warning(
                self,
                "不支持的格式",
                "输出格式仅支持：" + "、".join(SUPPORTED_OUTPUT_SUFFIXES),
            )
            return
        if output_image_format(output_path) is None:
            QMessageBox.warning(self, "不支持的格式", "当前输出格式无法由 Qt 保存。")
            return
        if output_path.is_dir():
            QMessageBox.warning(self, "输出路径无效", "输出路径是一个目录，请指定图片文件名。")
            return

        pixels = tile_range.pixel_width * tile_range.pixel_height
        if (
            tile_range.pixel_width > MAX_OUTPUT_DIMENSION
            or tile_range.pixel_height > MAX_OUTPUT_DIMENSION
            or pixels > MAX_OUTPUT_PIXELS
        ):
            QMessageBox.warning(
                self,
                "拼接范围过大",
                f"输出图片尺寸为 {tile_range.pixel_width:,} × {tile_range.pixel_height:,} 像素，"
                f"超过安全上限（{MAX_OUTPUT_DIMENSION:,} 像素/边，约 "
                f"{MAX_OUTPUT_PIXELS // 1_000_000} 百万像素）。\n\n"
                "请降低级别或缩小瓦片范围。",
            )
            return
        if pixels > WARN_OUTPUT_PIXELS and not self._confirm(
            "确认拼接",
            f"输出图片尺寸为 {tile_range.pixel_width:,} × {tile_range.pixel_height:,} 像素，"
            f"约 {pixels / 1_000_000:.1f} 百万像素，可能占用较多内存。\n\n是否继续？",
            "开始拼接",
        ):
            return
        if output_path.exists() and not self._confirm(
            "文件已存在",
            f"输出文件已存在：\n{output_path}\n\n是否覆盖？",
            "覆盖",
        ):
            return

        options = StitchOptions(
            tile_root=str(tile_root),
            output_path=str(output_path),
            zoom=tile_range.zoom,
            tile_range=tile_range,
        )
        worker = StitchWorker(options, self)
        worker.progress.connect(self._on_stitch_progress)
        worker.log.connect(self._append_log)
        worker.completed.connect(self._on_stitch_completed)
        worker.finished.connect(worker.deleteLater)
        self._stitch_worker = worker
        self._stitch_started_at = time.monotonic()
        self.stitch_progress.setRange(0, max(1, tile_range.count))
        self.stitch_progress.setValue(0)
        self._set_task_status(
            self.stitch_status,
            f"准备拼接 {tile_range.count:,} 张瓦片…",
            "busy",
        )
        self._append_log(
            f"拼接任务：{tile_range.cols} × {tile_range.rows} 张 · Z{tile_range.zoom} → {output_path}"
        )
        self._set_stitch_running(True)
        worker.start()

    def _stop_stitch(self) -> None:
        worker = self._stitch_worker
        if worker is None:
            return
        worker.cancel()
        self.btn_stitch_stop.setEnabled(False)
        self._set_task_status(self.stitch_status, "正在停止拼接，请稍候…", "busy")
        self._append_log("已请求停止拼接")

    def _on_stitch_progress(self, done: int, total: int) -> None:
        total = max(1, total)
        self.stitch_progress.setMaximum(total)
        self.stitch_progress.setValue(min(done, total))
        elapsed = max(0.1, time.monotonic() - self._stitch_started_at)
        rate = done / elapsed if done else 0.0
        remaining = (total - done) / rate if rate > 0 else None
        percent = int(done * 100 / total)
        text = (
            f"拼接中 {percent}% · {done:,}/{total:,} 张 · "
            f"{rate:.0f} 张/秒 · 剩余 {format_duration(remaining)}"
        )
        self._set_task_status(self.stitch_status, text, "busy")

    def _on_stitch_completed(self, ok: bool, message: str, stats: dict) -> None:
        self._stitch_worker = None
        self._set_stitch_running(False)
        processed = int(stats.get("processed", 0) or 0)
        total = int(stats.get("total", 0) or 0)
        self.stitch_progress.setMaximum(max(1, total))
        self.stitch_progress.setValue(total if ok else processed)
        kind = "ok" if ok else ("normal" if message.startswith("已停止") else "error")
        self._set_task_status(self.stitch_status, message, kind)
        self._append_log(message)
        if ok:
            self.statusBar().showMessage(message, 10000)
        elif not message.startswith("已停止") and not self._closing:
            self._set_log_visible(True)
            QMessageBox.warning(self, "拼接失败", message)

    def _set_stitch_running(self, running: bool) -> None:
        common_locked = [
            self.layer_choices,
            self.level_slider,
            self.region_combo,
            self.coord_format_combo,
            self.multi_level_check,
            self.multi_levels_edit,
            self.north_spin,
            self.south_spin,
            self.west_spin,
            self.east_spin,
            self.level_spin,
            self.row_min_spin,
            self.row_max_spin,
            self.col_min_spin,
            self.col_max_spin,
            self.btn_draw,
            self.btn_clear_draw,
            self.btn_use_view,
            self.btn_locate,
        ]
        stitch_locked = [
            self.api_key_edit,
            self.source_combo,
            self.provider_combo,
            self.stitch_input_edit,
            self.stitch_output_edit,
            self.btn_stitch_input_browse,
            self.btn_stitch_output_browse,
        ]
        for widget in common_locked + stitch_locked:
            widget.setEnabled(not running)
        self.btn_stitch_start.setEnabled(not running)
        self.btn_stitch_stop.setEnabled(running)
        self.act_use_view.setEnabled(not running)
        self._range_locked = running or self._download_worker is not None
        if running:
            self.btn_download_start.setEnabled(False)
        else:
            self.btn_download_start.setEnabled(self._download_worker is None)

    # ------------------------------------------------------------------
    # 文件选择与菜单
    # ------------------------------------------------------------------
    def _choose_download_dir(self) -> None:
        start_dir = self.download_dir_edit.text().strip() or str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "选择瓦片输出目录", start_dir)
        if not directory:
            return
        self._set_download_dir_text(directory)
        self.stitch_input_edit.setText(directory)
        self.stitch_output_edit.setText(str(Path(directory) / f"{self.level_spin.value()}.png"))

    def _choose_stitch_input(self) -> None:
        start_dir = self.stitch_input_edit.text().strip() or self.download_dir_edit.text().strip()
        directory = QFileDialog.getExistingDirectory(self, "选择瓦片目录", start_dir or str(Path.home()))
        if not directory:
            return
        self.stitch_input_edit.setText(directory)
        if not self.stitch_output_edit.text().strip():
            self.stitch_output_edit.setText(str(Path(directory) / f"{self.level_spin.value()}.png"))

    def _choose_stitch_output(self) -> None:
        current = self.stitch_output_edit.text().strip()
        if current:
            start = current
        else:
            base = self.stitch_input_edit.text().strip() or self.download_dir_edit.text().strip()
            if base:
                start = str(Path(base) / f"{self.level_spin.value()}.png")
            else:
                start = str(Path.home() / f"{self.level_spin.value()}.png")
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "选择拼接输出图片",
            start,
            "PNG 图片 (*.png);;JPEG 图片 (*.jpg *.jpeg);;WebP 图片 (*.webp);;TIFF 图片 (*.tif *.tiff);;所有文件 (*.*)",
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".png"
        self.stitch_output_edit.setText(path)

    def _open_download_dir(self) -> None:
        path = Path(self.download_dir_edit.text().strip()).expanduser()
        if not path.is_dir():
            QMessageBox.information(self, "目录不存在", f"{path}\n\n请先选择或创建下载目录。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_cache_dir(self) -> None:
        path = self.map.cache_dir
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "无法打开缓存目录", str(exc))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _clear_tile_cache(self) -> None:
        size = directory_size(self.map.cache_dir)
        if size <= 0:
            QMessageBox.information(self, "缓存为空", "当前没有可清理的瓦片缓存。")
            return
        if not self._confirm(
            "清理瓦片缓存",
            f"当前缓存占用 {format_bytes(size)}。\n\n清理后再次浏览地图会重新下载瓦片，是否继续？",
            "清理",
        ):
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.map.clear_cache()
        finally:
            QApplication.restoreOverrideCursor()
        self._append_log(f"已清理瓦片缓存（{format_bytes(size)}）")
        self.statusBar().showMessage("瓦片缓存已清理", 5000)

    def _show_help(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("使用说明")
        box.setIcon(QMessageBox.Information)
        box.setTextFormat(Qt.RichText)
        box.setText(
            "<h3>基本流程</h3>"
            "<ol>"
            "<li>在右侧选择图源与图层；需要地名、道路请选择 <b>卫星 + 标注</b>。</li>"
            "<li>点击 <b>开始框选</b>，在地图上点击两个对角点；"
            "也可直接输入经纬度，或点击 <b>当前视野</b>。</li>"
            "<li>选择“缩放级别 Z”，确认瓦片范围；多级下载时数量为全部级别的合计。</li>"
            "<li>需要多个级别时，勾选 <b>多级连续下载</b>，输入如 <b>10-14, 16</b>；程序会依次下载。</li>"
            "<li>选择输出目录，点击 <b>开始下载</b>。</li>"
            "<li>在“拼接”中选择瓦片目录和输出图片，点击 <b>开始拼接</b>。</li>"
            "</ol>"
            "<p><b>快捷键：</b>滚轮缩放 · 拖拽平移 · 双击放大 · Esc 取消框选 · "
            "方向键平移 · Home 复位视图。</p>"
            "<p><b>坐标系：</b>高德为 GCJ-02，天地图为 CGCS2000，Esri 为 WGS-84。"
            "切换图源不会转换经纬度；Google 大陆道路/注记与卫星影像可能错位。</p>"
            "<p><b>天地图：</b>需填写自己的 Key，或设置 TIANDITU_KEY 环境变量。</p>"
            "<p><b>继续下载：</b>Ctrl+J 打开任务管理，多级任务可继续整组，已完成级别会跳过。</p>"
            "<p><b>目录结构：</b>输出目录/级别/行/列.png。拼接时会自动查找 "
            ".png、.jpg、.jpeg、.webp、.bmp 文件。</p>"
        )
        box.exec_()

    def _show_about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("关于")
        box.setIcon(QMessageBox.Information)
        box.setTextFormat(Qt.RichText)
        box.setText(
            f"<h3>RDMapDownloader {__version__}</h3>"
            "<p>基于 PyQt5 重写的多图源地图瓦片下载与拼接工具。</p>"
            "<p>原项目：GMapDownloader（C# / WinForms / GMap.NET）。</p>"
            "<p>支持高德、Google、天地图与 Esri；卫星标注在预览、下载和拼接中保持一致。</p>"
            "<p>瓦片数据版权归各图源及其数据提供者所有。</p>"
            "<p>请遵守地图服务商的使用条款，合理控制下载频率。</p>"
        )
        box.exec_()

    # ------------------------------------------------------------------
    # 日志和任务状态
    # ------------------------------------------------------------------
    def _set_log_visible(self, visible: bool) -> None:
        visible = bool(visible)
        for control in (self.btn_toggle_log, self.act_log):
            if control.isChecked() != visible:
                with QSignalBlocker(control):
                    control.setChecked(visible)
        self.log_group.setVisible(visible)
        self.btn_toggle_log.setText("隐藏运行日志" if visible else "显示运行日志")
        if visible:
            QTimer.singleShot(0, self._scroll_log_into_view)

    def _scroll_log_into_view(self) -> None:
        bar = self._side_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _append_log(self, text: str) -> None:
        timestamp = QTime.currentTime().toString("HH:mm:ss")
        lines = str(text).splitlines() or [""]
        for line in lines:
            self.log_view.appendPlainText(f"[{timestamp}] {line}")
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _set_task_status(self, label: QLabel, text: str, kind: str) -> None:
        set_state(label, "statusKind", kind)
        label.setText(text)
        QTimer.singleShot(0, self._adjust_task_height)

    # ------------------------------------------------------------------
    # 关闭流程
    # ------------------------------------------------------------------
    def _confirm(self, title: str, text: str, ok_text: str = "继续", cancel_text: str = "取消") -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Question)
        box.setText(text)
        ok_button = box.addButton(ok_text, QMessageBox.AcceptRole)
        cancel_button = box.addButton(cancel_text, QMessageBox.RejectRole)
        box.setDefaultButton(cancel_button)
        box.exec_()
        return box.clickedButton() is ok_button

    def _poll_close_tasks(self) -> None:
        if self._download_worker is None and self._stitch_worker is None:
            self._close_timer.stop()
            self._save_settings()
            self._allow_close = True
            self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._allow_close:
            self._save_settings()
            event.accept()
            return

        active = [
            worker
            for worker in (self._download_worker, self._stitch_worker)
            if worker is not None
        ]
        if not active:
            self._save_settings()
            event.accept()
            return

        if self._closing:
            event.ignore()
            return

        if not self._confirm(
            "任务进行中",
            "下载或拼接任务仍在进行。\n\n是否停止任务并退出？",
            "停止并退出",
            "取消",
        ):
            event.ignore()
            return

        self._closing = True
        self._batch_cancelled = True
        self._download_queue.clear()
        for worker in active:
            worker.cancel()
        self._close_timer.start()
        self.statusBar().showMessage("正在停止任务，请稍候…")
        self._append_log("正在停止任务并退出…")
        event.ignore()
