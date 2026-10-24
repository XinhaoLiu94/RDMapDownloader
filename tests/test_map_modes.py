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
from PyQt5.QtCore import QSettings, pyqtSignal
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

    def __init__(self, parent=None):
        super().__init__(parent)
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
        w._cesium.camera_state = {
            "position": [1, 2, 3],
            "heading": 0,
            "pitch": -1,
            "roll": 0,
        }
        w.close()
        self.window = MainWindow()
        self.app.processEvents()
        self.assertEqual(self.window._view_mode, "3d")
        self.assertEqual(self.window._geographic_selection, canonical)
        self.window._set_map_mode("2d")
        self.assertEqual(self.window._extent_values(), canonical.native_bounds)

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
