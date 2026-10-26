"""双渲染器衔接：原生 2D、WGS84 Cesium、共享地理选区与独立视图。"""

from __future__ import annotations

import json
import math

from PyQt5.QtCore import QSignalBlocker, QTimer

from .geodesy import GeographicSelection, WGS84, provider_datum
from .providers import MAP_SOURCES, PROVIDER_MAP, DEFAULT_PROVIDER_KEY
from .terrain_server import supports_3d
from .tile_math import (
    MAX_LATITUDE,
    range_from_bounds,
    range_from_rows_cols,
    tile_to_latlng,
)


class MapModes:
    def _init_mode_state(self):
        self._view_mode = "2d"
        self._switching_mode = False
        self._mode_states = {}
        self._geographic_selection = None
        self._displayed_selection = None
        self._selection_visible = False
        self._cesium = None
        self._mars3d = None
        self._manual_range_dirty = False
        self._map_modes_closed = False

    def _renderer_for_mode(self, mode):
        if mode == "mars3d":
            return self._mars3d
        if mode == "3d":
            return self._cesium
        return None

    def _active_renderer(self):
        return self._renderer_for_mode(self._view_mode)

    def _is_3d_mode(self):
        return self._view_mode in ("3d", "mars3d")

    def _ensure_renderer(self, mode):
        renderer = self._renderer_for_mode(mode)
        if renderer is not None:
            return renderer
        from .cesium_widget import CesiumWidget

        renderer = CesiumWidget(self, engine="mars3d" if mode == "mars3d" else "cesium")
        if mode == "mars3d":
            self._mars3d = renderer
        else:
            self._cesium = renderer
        self.map_stack.addWidget(renderer)
        renderer.log.connect(self._append_log)
        renderer.ready.connect(lambda m=mode: self._on_renderer_ready(m))

        def current_page_only(slot):
            def forward(*values):
                # WebChannel 回报可能晚于页面切换或关闭，不能改写另一页的选区。
                if (
                    not self._map_modes_closed
                    and not self._closing
                    and not self._switching_mode
                    and self._view_mode == mode
                    and self._renderer_for_mode(mode) is renderer
                    and not renderer.closed
                ):
                    slot(*values)

            return forward

        renderer.selectionChanged.connect(current_page_only(self._on_3d_selection))
        renderer.selectionModeChanged.connect(
            current_page_only(self._on_selection_mode_changed)
        )
        renderer.positionChanged.connect(current_page_only(self._on_map_position))
        return renderer

    def _current_wgs84_view(self):
        renderer = self._active_renderer()
        if renderer is None:
            if self._is_3d_mode():
                return self._geographic_selection.bounds_for(WGS84)
            try:
                return GeographicSelection(
                    self.map.visible_bounds(), provider_datum(self.map.provider)
                ).bounds_for(WGS84)
            except ValueError:
                return self._geographic_selection.bounds_for(WGS84)

        camera = renderer.camera_state
        if isinstance(camera, dict):
            bounds = camera.get("bounds")
            if (
                isinstance(bounds, (tuple, list))
                and len(bounds) == 4
                and all(
                    isinstance(v, (int, float))
                    and not isinstance(v, bool)
                    and math.isfinite(v)
                    for v in bounds
                )
            ):
                north, south, west, east = bounds
                if (
                    -MAX_LATITUDE <= south < north <= MAX_LATITUDE
                    and -180 <= west < east <= 180
                ):
                    return tuple(bounds)

            centre, height = camera.get("centre"), camera.get("height")
            if (
                self._valid_centre(centre)
                and not any(isinstance(v, bool) for v in centre)
                and abs(centre[0]) <= MAX_LATITUDE
                and isinstance(height, (int, float))
                and not isinstance(height, bool)
                and math.isfinite(height)
                and height > 0
            ):
                # 朝向天空时可能没有地表包络，以相机高度近似初始视野。
                latitude, longitude = centre
                aspect = max(
                    0.5, min(3.0, renderer.width() / max(1, renderer.height()))
                )
                half_lat = max(0.001, min(85.0, height * 0.6 / 111_320))
                half_lon = min(
                    180.0,
                    half_lat * aspect / max(0.01, math.cos(math.radians(latitude))),
                )
                return (
                    min(MAX_LATITUDE, latitude + half_lat),
                    max(-MAX_LATITUDE, latitude - half_lat),
                    max(-180.0, longitude - half_lon),
                    min(180.0, longitude + half_lon),
                )

        # 三维页尚未回报相机时，选区比隐藏二维地图的旧位置更可靠。
        return self._geographic_selection.bounds_for(WGS84)

    def _restore_mode_settings(self):
        try:
            states = json.loads(str(self._settings.value("map/modeStates", "{}")))
            if isinstance(states, dict):
                self._mode_states = states
            selection = json.loads(
                str(self._settings.value("map/geographicSelection", "null"))
            )
            if selection:
                self._geographic_selection = GeographicSelection.from_dict(selection)
        except (ValueError, TypeError, KeyError):
            self._mode_states = {}
            self._geographic_selection = None
        datum = provider_datum(self.map.provider)
        if self._geographic_selection is not None:
            derived = self._geographic_selection.bounds_for(datum)
            if all(
                abs(a - b) < 0.000002 for a, b in zip(derived, self._extent_values())
            ):
                self._displayed_selection = (datum, self._extent_values())
        self._remember_geographic_selection()
        state = self._mode_states.get("2d", {})
        centre = state.get("centre")
        if self._valid_centre(centre):
            self.map.set_center(*centre)
        self._mode_states["2d"] = self._capture_mode_state()
        restored_mode = str(self._settings.value("map/viewMode", "2d"))
        if restored_mode in ("3d", "mars3d"):
            QTimer.singleShot(0, lambda m=restored_mode: self._set_map_mode(m))

    @staticmethod
    def _valid_centre(value):
        return (
            isinstance(value, (tuple, list))
            and len(value) == 2
            and all(isinstance(v, (int, float)) and math.isfinite(v) for v in value)
            and -85.052 <= value[0] <= 85.052
            and -180 <= value[1] <= 180
        )

    def _capture_mode_state(self):
        state = {
            "provider": str(self.provider_combo.currentData()),
            "zoom": self.map.zoom,
            "centre": [self.map.center_latitude, self.map.center_longitude],
            "level": self.level_spin.value(),
        }
        if self._geographic_selection:
            state["selection"] = self._geographic_selection.to_dict()
        renderer = self._active_renderer()
        if renderer is not None:
            state["camera"] = renderer.camera_state
        if self._range is not None:
            item = self._range
            state["range"] = [
                item.zoom,
                item.min_row,
                item.max_row,
                item.min_col,
                item.max_col,
            ]
        return state

    def _save_mode_settings(self):
        self._mode_states[self._view_mode] = self._capture_mode_state()
        self._settings.setValue("map/viewMode", self._view_mode)
        self._settings.setValue("map/modeStates", json.dumps(self._mode_states))
        if self._geographic_selection:
            self._settings.setValue(
                "map/geographicSelection",
                json.dumps(self._geographic_selection.to_dict()),
            )
        # 旧配置键始终保留 2D 的基准，防止下次把 WGS84 数值当作高德坐标。
        if self._view_mode in ("3d", "mars3d"):
            state = self._mode_states.get("2d", {})
            provider = PROVIDER_MAP.get(
                state.get("provider"), PROVIDER_MAP[DEFAULT_PROVIDER_KEY]
            )
            self._settings.setValue("map/source", provider.source)
            self._settings.setValue("map/provider", provider.key)
            self._settings.setValue("map/zoom", state.get("zoom", 10))
            if self._geographic_selection:
                for name, value in zip(
                    ("north", "south", "west", "east"),
                    self._geographic_selection.bounds_for(provider_datum(provider)),
                ):
                    self._settings.setValue("range/" + name, value)

    def _remember_geographic_selection(self, visible=None):
        if self._switching_mode or self._loading_settings:
            return
        datum = provider_datum(self.map.provider)
        native = self._extent_values()
        signature = (datum, native)
        if self._geographic_selection is None or signature != self._displayed_selection:
            try:
                self._geographic_selection = GeographicSelection(native, datum)
            except ValueError:
                return
            self._displayed_selection = signature
        if visible is not None:
            self._selection_visible = visible
        renderer = self._active_renderer()
        if renderer is not None:
            if visible and renderer.drawing:
                renderer.command("cancelSelection")
            renderer.command(
                "setSelection",
                (
                    list(self._geographic_selection.bounds_for(WGS84))
                    if self._selection_visible
                    else None
                ),
            )

    def _populate_mode_sources(self, mode, preferred):
        with QSignalBlocker(self.source_combo):
            self.source_combo.clear()
            for source in MAP_SOURCES:
                if mode == "2d" or any(
                    supports_3d(PROVIDER_MAP[key]) for key in source.provider_keys
                ):
                    self.source_combo.addItem(source.name, source.key)
            index = self.source_combo.findData(preferred)
            self.source_combo.setCurrentIndex(max(0, index))

    def _set_map_mode(self, mode):
        if self._map_modes_closed:
            return
        if mode not in ("2d", "3d", "mars3d") or mode == self._view_mode:
            return
        if self._selection_active():
            self.map.clear_selection()
            renderer = self._active_renderer()
            if renderer is not None:
                renderer.command("cancelSelection")
        # 手工行列范围也参与坐标转换，不能只转换之前的经纬度输入。
        if self._manual_range_dirty and self._range is not None:
            item = self._range
            if item != range_from_bounds(*self._extent_values(), item.zoom):
                north, west = tile_to_latlng(item.min_col, item.min_row, item.zoom)
                south, east = tile_to_latlng(
                    item.max_col + 1, item.max_row + 1, item.zoom
                )
                self._set_extent_values(north, south, west, east, sync_map=False)
                self._range = item
            self._manual_range_dirty = False
        self._remember_geographic_selection()
        if self._geographic_selection is None:
            self.statusBar().showMessage("请先输入有效的经纬度范围，再切换视图。", 5000)
            for key, button in self.mode_buttons.items():
                button.setChecked(key == self._view_mode)
            return
        wgs_view = self._current_wgs84_view()
        self._mode_states[self._view_mode] = self._capture_mode_state()
        state = self._mode_states.get(mode, {})
        preferred = state.get(
            "provider",
            "esri_hybrid" if mode in ("3d", "mars3d") else DEFAULT_PROVIDER_KEY,
        )
        provider = PROVIDER_MAP.get(
            preferred,
            (
                PROVIDER_MAP["esri_hybrid"]
                if mode in ("3d", "mars3d")
                else PROVIDER_MAP[DEFAULT_PROVIDER_KEY]
            ),
        )
        if mode in ("3d", "mars3d") and not supports_3d(provider):
            provider = PROVIDER_MAP["esri_hybrid"]
        self._switching_mode = True
        try:
            self._view_mode = mode
            self.map.set_active(mode == "2d")
            self._populate_mode_sources(mode, provider.source)
            self._populate_provider_combo(provider.source, provider.key)
            self._on_provider_changed()
            with QSignalBlocker(self.level_spin):
                self.level_spin.setValue(
                    int(state.get("level", self.level_spin.value()))
                )
            bounds = self._geographic_selection.bounds_for(provider_datum(provider))
            self._set_extent_values(*bounds, sync_map=False)
            self._displayed_selection = (
                provider_datum(provider),
                self._extent_values(),
            )
            if (
                state.get("selection") == self._geographic_selection.to_dict()
                and state.get("provider") == provider.key
                and state.get("range")
            ):
                try:
                    item = range_from_rows_cols(*state["range"])
                    if item.is_within_world and item.zoom == self.level_spin.value():
                        for spin, value in zip(
                            (
                                self.row_min_spin,
                                self.row_max_spin,
                                self.col_min_spin,
                                self.col_max_spin,
                            ),
                            state["range"][1:],
                        ):
                            with QSignalBlocker(spin):
                                spin.setValue(value)
                        self._set_current_range(item)
                except (ValueError, TypeError):
                    pass
            if mode == "2d":
                with QSignalBlocker(self.map):
                    if state.get("selection") == self._geographic_selection.to_dict():
                        self.map.set_zoom(int(state.get("zoom", 10)))
                        if self._valid_centre(state.get("centre")):
                            self.map.set_center(*state["centre"])
                    else:
                        self.map.show_bounds(*bounds, zoom=self.level_spin.value())
                with QSignalBlocker(self.zoom_spin):
                    self.zoom_spin.setValue(self.map.zoom)
                self._zoom_label.setText(f"级别 {self.map.zoom}")
                self.map_stack.setCurrentIndex(0)
                if self._selection_visible:
                    self.map.set_selection(*bounds)
                else:
                    self.map.clear_selection()
                for renderer in (self._cesium, self._mars3d):
                    if renderer is not None:
                        renderer.command("setActive", False)
            else:
                renderer = self._ensure_renderer(mode)
                self.map_stack.setCurrentWidget(renderer)
                for other_mode in ("3d", "mars3d"):
                    if other_mode != mode:
                        other = self._renderer_for_mode(other_mode)
                        if other is not None:
                            other.command("setActive", False)
                renderer.set_source(provider)
                renderer.set_theme()
                renderer.command("setEnabled", not self._range_locked)
                renderer.command("setActive", True)
                current = self._geographic_selection.to_dict()
                if state.get("camera") and state.get("selection") == current:
                    renderer.command("restoreCamera", state["camera"])
                else:
                    target = (
                        self._geographic_selection.bounds_for(WGS84)
                        if state.get("selection") != current and state
                        else wgs_view
                    )
                    renderer.command("flyTo", list(target))
                renderer.command(
                    "setSelection",
                    (
                        list(self._geographic_selection.bounds_for(WGS84))
                        if self._selection_visible
                        else None
                    ),
                )
            for key, button in self.mode_buttons.items():
                with QSignalBlocker(button):
                    button.setChecked(key == mode)
        finally:
            self._switching_mode = False
        self._append_log(
            "切换视图："
            + {
                "2d": "2D · Painter",
                "3d": "3D · Cesium · WGS84",
                "mars3d": "Mars3D · WGS84",
            }[mode]
        )
        self.statusBar().showMessage(
            (
                "Mars3D 三维实验模式 · WGS84"
                if mode == "mars3d"
                else (
                    "三维模式使用 WGS84 图层与高程"
                    if mode == "3d"
                    else "已恢复二维地图"
                )
            ),
            5000,
        )

    def _on_renderer_ready(self, mode):
        renderer = self._renderer_for_mode(mode)
        if renderer is None:
            return
        if self._view_mode == mode:
            renderer.set_source(self.map.provider)
        renderer.set_theme()
        renderer.command("setEnabled", not self._range_locked)
        renderer.command("setActive", self._view_mode == mode)

    def _on_3d_selection(self, bounds):
        if self._view_mode not in ("3d", "mars3d") or self._range_locked:
            return
        self._on_map_selection(*bounds)
        self._remember_geographic_selection(True)

    def _selection_active(self):
        renderer = self._active_renderer()
        if renderer is not None:
            return renderer.drawing
        return self.map.selection_mode

    def _mode_fly_to_range(self):
        renderer = self._active_renderer()
        if renderer is not None:
            renderer.command("flyTo", list(self._extent_values()))

    def _shutdown_map_modes(self):
        self._map_modes_closed = True
        self.map.set_active(False)
        for renderer in (self._cesium, self._mars3d):
            if renderer is not None:
                renderer.shutdown()
