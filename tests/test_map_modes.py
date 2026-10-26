"""双模式坐标、真实高程采样、服务边界和原生界面状态回归。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from array import array
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

import requests
from PyQt5.QtCore import QSettings, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication, QWidget

from rdmap.geodesy import GCJ02, WGS84, GeographicSelection, from_wgs84, to_wgs84
from rdmap.elevation import GeoidGrid, HgtTile, hgt_name, terrain_heightmap
from rdmap.main_window import MainWindow
from rdmap.terrain_server import ResourceStore, TerrainServer
from rdmap.tile_math import range_from_rows_cols


class GeodesyTests(unittest.TestCase):
    def test_beijing_reference_and_inverse(self):
        lat, lon = from_wgs84(39.908823, 116.397470, GCJ02)
        self.assertAlmostEqual(lat, 39.910226, places=5)
        self.assertAlmostEqual(lon, 116.403714, places=5)
        original = to_wgs84(lat, lon, GCJ02)
        for a, b in zip(original, (39.908823, 116.397470)):
            self.assertAlmostEqual(a, b, places=9)

    def test_overseas_identity(self):
        self.assertEqual(from_wgs84(40, -74, GCJ02), (40, -74))

    def test_original_selection_does_not_grow(self):
        original = (23.6, 23.1, 113.3, 113.9)
        selection = GeographicSelection(original, GCJ02)
        for _ in range(32):
            self.assertNotEqual(selection.bounds_for(WGS84), original)
            self.assertEqual(selection.bounds_for(GCJ02), original)
            selection = GeographicSelection.from_dict(selection.to_dict())


class ElevationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_geoid_bilinear_and_longitude_wrap(self):
        path = self.root / "grid.gtx"
        path.write_bytes(
            struct.pack(
                ">4d2i8f", -90, -180, 180, 90, 2, 4, 0, 10, 20, 30, 40, 50, 60, 70
            )
        )
        grid = GeoidGrid(path)
        self.assertAlmostEqual(grid.undulation(0, -135), 25)
        self.assertEqual(grid.undulation(0, 225), grid.undulation(0, -135))

    def test_hgt_endianness_bilinear_and_nodata(self):
        path = self.root / "tile.hgt"
        path.write_bytes(struct.pack(">4h", 100, 200, -100, -32768))
        tile = HgtTile(path, 0, 0)
        try:
            self.assertAlmostEqual(tile.sample(0.5, 0.5), 200 / 3)
            self.assertEqual(tile.sample(0, 0), -100)
            with self.assertRaises(ValueError):
                tile.sample(0, 1)
        finally:
            tile.close()
        self.assertEqual(hgt_name(-12, -77), "S12W077")

    def test_ellipsoid_height_and_shared_edges(self):
        class Geoid:
            def undulation(self, lat, lon):
                return 30

        path = self.root / "flat.hgt"
        path.write_bytes(struct.pack(">4h", 100, 100, 100, 100))
        left = array("f")
        left.frombytes(terrain_heightmap(10, 834, 443, lambda *_: path, Geoid()))
        right = array("f")
        right.frombytes(terrain_heightmap(10, 835, 443, lambda *_: path, Geoid()))
        self.assertEqual(set(left), {130})
        self.assertEqual(
            [left[y * 65 + 64] for y in range(65)], [right[y * 65] for y in range(65)]
        )

    def test_distant_ellipsoid_needs_no_download(self):
        self.assertEqual(
            terrain_heightmap(
                1, 0, 0, lambda *_: self.fail("unexpected download"), None
            ),
            bytes(65 * 65 * 4),
        )
        with self.assertRaises(ValueError):
            terrain_heightmap(10, 1024, 0, None, None)


class ServerTests(unittest.TestCase):
    def test_token_path_validation_and_binary_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResourceStore(Path(directory))
            with patch.object(store, "heights", return_value=bytes(65 * 65 * 4)):
                server = TerrainServer(store)
                base = server.url.rsplit("/", 1)[0]
                session = requests.Session()
                session.trust_env = False
                try:
                    self.assertEqual(
                        session.get(server.url, timeout=3).status_code, 200
                    )
                    self.assertEqual(
                        session.get(base + "/terrain/1/0/0", timeout=3).content,
                        bytes(65 * 65 * 4),
                    )
                    self.assertEqual(
                        session.get(base + "/terrain/1/3/0", timeout=3).status_code, 503
                    )
                    self.assertEqual(
                        session.get(
                            base + "/imagery/amap_hybrid/1/0/0", timeout=3
                        ).status_code,
                        503,
                    )
                    self.assertEqual(
                        session.get(base + "/%2e%2e/main.py", timeout=3).status_code,
                        404,
                    )
                    self.assertEqual(
                        session.get(
                            base.replace(server.token, "wrong") + "/index.html",
                            timeout=3,
                        ).status_code,
                        404,
                    )
                finally:
                    session.close()
                    server.close()


class FakeCesium(QWidget):
    log = pyqtSignal(str)
    ready = pyqtSignal()
    positionChanged = pyqtSignal(float, float)
    selectionChanged = pyqtSignal(object)
    selectionModeChanged = pyqtSignal(bool)

    def __init__(self, parent=None, engine="cesium"):
        super().__init__(parent)
        self.engine = engine
        self.camera_state = {}
        self.drawing = False
        self.commands = []
        self.closed = False

    def command(self, method, data=None):
        self.commands.append((method, data))
        if method == "cancelSelection":
            self.drawing = False
            self.selectionModeChanged.emit(False)

    def set_source(self, provider):
        self.command("setSource", provider.key)

    def set_theme(self):
        self.command("setTheme")

    def begin_selection(self):
        self.drawing = True
        self.selectionModeChanged.emit(True)

    def shutdown(self):
        self.closed = True


class ModeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = (
            Path(__file__).resolve().parent.parent
            / ".test_tmp"
            / ("3d-" + next(tempfile._get_candidate_names()))
        )
        self.temp.mkdir(parents=True)
        self.env = patch.dict(
            os.environ,
            {
                "RDMAP_CACHE_DIR": str(self.temp / "cache"),
                "RDMAP_TASK_DIR": str(self.temp),
            },
        )
        self.env.start()
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(
            QSettings.IniFormat, QSettings.UserScope, str(self.temp / "settings")
        )
        QSettings().clear()
        self.fake = patch("rdmap.cesium_widget.CesiumWidget", FakeCesium)
        self.fake.start()
        self.window = MainWindow()
        self.window.resize(1280, 850)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        self.fake.stop()
        self.env.stop()
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_repeated_switch_preserves_selection_source_and_view(self):
        w = self.window
        w.map.set_zoom(12)
        w.map.set_center(23.4, 113.7)
        w._set_extent_values(23.6, 23.1, 113.3, 113.9, sync_map=True)
        original = w._extent_values()
        original_range = w._range
        for _ in range(12):
            w._set_map_mode("3d")
            self.assertEqual(w.map.provider.key, "esri_hybrid")
            self.assertNotEqual(w._extent_values(), original)
            self.assertEqual(
                {w.source_combo.itemData(i) for i in range(w.source_combo.count())},
                {"esri", "google"},
            )
            w._set_map_mode("2d")
            self.assertEqual(w._extent_values(), original)
            self.assertEqual(w._range, original_range)
            self.assertEqual(w.map.provider.key, "amap_hybrid")
            self.assertEqual(w.map.zoom, 12)
            self.assertAlmostEqual(w.map.center_latitude, 23.4, places=10)
            self.assertAlmostEqual(w.map.center_longitude, 113.7, places=10)

    def test_google_3d_excludes_shifted_labels(self):
        w = self.window
        w._set_map_mode("3d")
        w.source_combo.setCurrentIndex(w.source_combo.findData("google"))
        self.assertEqual(
            [w.provider_combo.itemData(i) for i in range(w.provider_combo.count())],
            ["google_satellite"],
        )

    def test_mars3d_mode_has_own_renderer(self):
        w = self.window
        self.assertIn("mars3d", w.mode_buttons)
        w._set_map_mode("mars3d")
        self.assertEqual(w._view_mode, "mars3d")
        self.assertIsNotNone(w._mars3d)
        self.assertEqual(w._mars3d.engine, "mars3d")
        self.assertTrue(w.mode_buttons["mars3d"].isChecked())
        self.assertEqual(w.map.provider.key, "esri_hybrid")

        w._set_map_mode("3d")
        self.assertEqual(w._view_mode, "3d")
        self.assertIsNotNone(w._cesium)
        self.assertTrue(w.mode_buttons["3d"].isChecked())

        w._set_map_mode("2d")
        self.assertEqual(w._view_mode, "2d")
        self.assertFalse(w.mode_buttons["mars3d"].isChecked())

    def _assert_first_3d_handoff_uses_current_view(self, source, target):
        w = self.window
        w._set_map_mode(source)
        renderer = w._active_renderer()
        camera = {
            "position": [1344183.4875, -4687724.9096, 4174068.6118],
            "heading": 0,
            "pitch": -1.5707963267948966,
            "roll": 0,
            "bounds": [40.9, 40.6, -74.2, -73.8],
            "centre": [40.75, -74.0],
            "height": 50_000,
            "terrainVisual": {"active": True, "exaggeration": 2.5},
        }
        renderer.camera_state = camera
        original_selection = w._geographic_selection
        self.assertNotEqual(tuple(camera["bounds"]), w.map.visible_bounds())

        w._set_map_mode(target)
        self.assertIn(("flyTo", camera["bounds"]), w._active_renderer().commands)
        self.assertEqual(w._geographic_selection, original_selection)
        self.assertEqual(w._mode_states[source]["camera"], camera)

        # 已打开过的页面继续恢复自己的相机，而非另一页的新视野。
        w._active_renderer().camera_state = {**camera, "bounds": [30, 29, 110, 111]}
        w._set_map_mode(source)
        self.assertEqual(renderer.commands[-2], ("restoreCamera", camera))

    def test_first_cesium_to_mars3d_handoff_uses_current_view(self):
        self._assert_first_3d_handoff_uses_current_view("3d", "mars3d")

    def test_first_mars3d_to_cesium_handoff_uses_current_view(self):
        self._assert_first_3d_handoff_uses_current_view("mars3d", "3d")

    def test_invalid_3d_view_uses_camera_centre_or_selection(self):
        w = self.window
        w._set_map_mode("mars3d")
        renderer = w._active_renderer()
        renderer.camera_state = {
            "bounds": [41, 40, -73, -74],
            "centre": [40.75, -74],
            "height": 50_000,
        }
        north, south, west, east = w._current_wgs84_view()
        self.assertGreater(north, 40.75)
        self.assertLess(south, 40.75)
        self.assertLess(west, -74)
        self.assertGreater(east, -74)
        self.assertLess(north - south, 2)
        fallback = w._geographic_selection.bounds_for(WGS84)
        for camera in (
            {},
            [],
            {"bounds": [float("nan"), 40, -75, -73]},
            {"bounds": [91, 40, -75, -73]},
            {"bounds": [True, 0, -75, -73]},
            {"bounds": [41, 40, -75]},
            {"centre": [40.75, -74], "height": float("inf")},
            {"centre": [40.75, -74], "height": 0},
            {"centre": [True, -74], "height": 50_000},
        ):
            with self.subTest(camera=camera):
                renderer.camera_state = camera
                self.assertEqual(w._current_wgs84_view(), fallback)

    def test_delayed_hidden_renderer_signals_do_not_change_current_state(self):
        w = self.window
        for source, target in (("3d", "mars3d"), ("mars3d", "3d")):
            with self.subTest(source=source, target=target):
                w._set_map_mode(source)
                old_renderer = w._active_renderer()

                def delayed_reply(renderer=old_renderer):
                    renderer.selectionChanged.emit([40.8, 40.7, -74.1, -73.9])
                    renderer.selectionModeChanged.emit(False)
                    renderer.positionChanged.emit(40.75, -74)

                QTimer.singleShot(0, delayed_reply)
                w._set_map_mode(target)
                current_renderer = w._active_renderer()
                w._begin_selection()
                before = (w._extent_values(), w._coord_label.text(), w.btn_draw.text())
                self.app.processEvents()
                self.assertEqual(
                    (w._extent_values(), w._coord_label.text(), w.btn_draw.text()),
                    before,
                )
                self.assertTrue(current_renderer.drawing)
                self.assertFalse(w.btn_draw.isEnabled())

                # 当前页面的回报仍生效，取消框选的同步回报也应更新按钮。
                current_renderer.positionChanged.emit(29.35, 110.45)
                self.assertIn("110.450000", w._coord_label.text())
                current_renderer.selectionChanged.emit([29.4, 29.3, 110.4, 110.5])
                self.assertEqual(w._extent_values(), (29.4, 29.3, 110.4, 110.5))
                self.assertFalse(current_renderer.drawing)
                self.assertTrue(w.btn_draw.isEnabled())

    def test_renderer_signals_during_switch_or_after_close_are_ignored(self):
        w = self.window
        w._set_map_mode("mars3d")
        w._set_map_mode("3d")
        current = w._active_renderer()
        before = (w._extent_values(), w._coord_label.text(), w.btn_draw.text())
        w._switching_mode = True
        try:
            current.selectionChanged.emit([40.8, 40.7, -74.1, -73.9])
            current.selectionModeChanged.emit(True)
            current.positionChanged.emit(40.75, -74)
        finally:
            w._switching_mode = False
        self.assertEqual(
            (w._extent_values(), w._coord_label.text(), w.btn_draw.text()), before
        )

        def delayed_replies():
            for renderer in (w._cesium, w._mars3d):
                renderer.selectionChanged.emit([40.8, 40.7, -74.1, -73.9])
                renderer.selectionModeChanged.emit(True)
                renderer.positionChanged.emit(40.75, -74)

        QTimer.singleShot(0, delayed_replies)
        w.close()
        self.app.processEvents()
        self.assertEqual(
            (w._extent_values(), w._coord_label.text(), w.btn_draw.text()), before
        )

    def test_close_cancels_preview_and_pending_mode_restore(self):
        w = self.window
        w.close()
        self.assertFalse(w.map._active)
        self.assertFalse(w.map._inflight)
        w._set_map_mode("3d")
        self.assertIsNone(w._cesium)

    def test_selection_dispatch_and_task_options_immutable(self):
        from rdmap.workers import DownloadOptions

        w = self.window
        options = DownloadOptions("example", w.map.provider, w._range, 1, True)
        saved = (options.provider, options.tile_range)
        w._set_map_mode("3d")
        w._begin_selection()
        self.assertTrue(w._selection_active())
        w._cesium.selectionChanged.emit([29.4, 29.3, 110.4, 110.5])
        self.assertEqual(w._extent_values(), (29.4, 29.3, 110.4, 110.5))
        w._clear_selection()
        self.assertFalse(w._selection_active())
        self.assertIn(("setSelection", None), w._cesium.commands)
        self.assertEqual((options.provider, options.tile_range), saved)
        w._begin_selection()
        w.north_spin.setValue(29.45)
        self.assertFalse(w._selection_active())
        w._set_map_mode("2d")
        self.assertAlmostEqual(w.map.center_latitude, 29.35, delta=0.05)
        w._set_map_mode("3d")
        w._set_download_running(True)
        self.assertIn(("setEnabled", False), w._cesium.commands)
        w._set_download_running(False)

    def test_restore_3d_keeps_original_canonical_selection(self):
        w = self.window
        w._set_extent_values(23.6, 23.1, 113.3, 113.9, sync_map=True)
        canonical = w._geographic_selection
        w._set_map_mode("3d")
        old_camera = {
            "position": [1, 2, 3],
            "heading": 0,
            "pitch": -1,
            "roll": 0,
        }
        w._cesium.camera_state = old_camera
        w.close()
        self.window = MainWindow()
        self.app.processEvents()
        self.assertEqual(self.window._view_mode, "3d")
        self.assertEqual(self.window._geographic_selection, canonical)
        # 旧配置没有 terrainVisual 字段，仍应完整恢复相机。
        self.assertNotIn("terrainVisual", old_camera)
        self.assertIn(("restoreCamera", old_camera), self.window._cesium.commands)
        self.window._set_map_mode("2d")
        self.assertEqual(self.window._extent_values(), canonical.native_bounds)

    def test_each_3d_mode_restores_its_own_terrain_visual_after_restart(self):
        w = self.window
        cesium_camera = {
            "position": [1344183.4875, -4687724.9096, 4174068.6118],
            "heading": 0,
            "pitch": -1.5707963267948966,
            "roll": 0,
            "bounds": [40.9, 40.6, -74.2, -73.8],
            "centre": [40.75, -74],
            "height": 50_000,
            "terrainVisual": {"active": True, "exaggeration": 2.4},
        }
        mars_camera = {
            "position": [6428137, 0, 0],
            "heading": 0.25,
            "pitch": -1.2,
            "roll": 0,
            "bounds": [0.2, -0.2, -0.3, 0.3],
            "centre": [0, 0],
            "height": 50_000,
            "terrainVisual": {"active": False, "exaggeration": 1.6},
        }
        w._set_map_mode("3d")
        w._cesium.camera_state = cesium_camera
        w._set_map_mode("mars3d")
        w._mars3d.camera_state = mars_camera
        canonical = w._geographic_selection
        w.close()

        self.window = MainWindow()
        self.app.processEvents()
        reopened = self.window
        self.assertEqual(reopened._view_mode, "mars3d")
        self.assertEqual(reopened._geographic_selection, canonical)
        self.assertIn(("restoreCamera", mars_camera), reopened._mars3d.commands)
        self.assertEqual(reopened._mode_states["mars3d"]["camera"], mars_camera)

        reopened._set_map_mode("3d")
        self.assertIn(("restoreCamera", cesium_camera), reopened._cesium.commands)
        self.assertEqual(reopened._geographic_selection, canonical)

    def test_manual_tile_range_survives_roundtrip(self):
        w = self.window
        w.row_min_spin.setValue(430)
        w.row_max_spin.setValue(431)
        w.col_min_spin.setValue(832)
        w.col_max_spin.setValue(833)
        original = w._range
        self.assertEqual(original, range_from_rows_cols(10, 430, 431, 832, 833))
        w._set_map_mode("3d")
        w._set_map_mode("2d")
        self.assertEqual(w._range, original)
