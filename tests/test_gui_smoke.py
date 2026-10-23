"""主窗口离线冒烟测试；设置 RDMAP_NETWORK_TESTS=1 可执行真实下载/拼接。"""

from __future__ import annotations

import atexit
import os
import shutil
import unittest
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEventLoop, QLoggingCategory, QPoint, QSettings, Qt, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from rdmap.main_window import MainWindow

_ROOT = Path(__file__).resolve().parent.parent
_TEST_TMP = _ROOT / ".test_tmp"
_TEST_TMP.mkdir(parents=True, exist_ok=True)
_SETTINGS_DIR = _TEST_TMP / f"settings-{uuid.uuid4().hex[:10]}"
_CACHE_DIR = _TEST_TMP / f"cache-{uuid.uuid4().hex[:10]}"
_SETTINGS_DIR.mkdir()
_CACHE_DIR.mkdir()
os.environ["RDMAP_CACHE_DIR"] = str(_CACHE_DIR)
os.environ["RDMAP_TASK_DIR"] = str(_TEST_TMP / f"tasks-{uuid.uuid4().hex[:10]}")
atexit.register(lambda: shutil.rmtree(_TEST_TMP, ignore_errors=True))


def _new_temp_dir(prefix: str) -> Path:
    path = _TEST_TMP / f"{prefix}-{uuid.uuid4().hex[:10]}"
    path.mkdir()
    return path


def get_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setOrganizationName("RDMapDownloaderTest")
    app.setApplicationName("RDMapDownloaderTest")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(_SETTINGS_DIR))
    return app


class MainWindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = get_app()

    def setUp(self) -> None:
        QSettings().clear()
        self.window = MainWindow()
        self.window.resize(1280, 850)
        self.window.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        if self.window is not None:
            self.window.close()
            self.window = None
        self.app.processEvents()

    def test_default_range(self) -> None:
        self.assertIsNotNone(self.window._range)
        assert self.window._range is not None
        self.assertEqual(self.window._range.count, 6)
        self.assertEqual(self.window.level_spin.value(), 10)
        self.assertIn("6", self.window.tile_count_label.text())
        # 启动时只预填范围数值，不应在地图上自动出现框选矩形。
        self.assertIsNone(self.window.map.selection_bounds)
        # 默认输出目录按图源类型分目录。
        self.assertEqual(Path(self.window.download_dir_edit.text()).name, "amap_hybrid")

    def test_editing_extent_shows_selection(self) -> None:
        self.window.north_spin.setValue(23.70)
        self.app.processEvents()
        self.assertIsNotNone(self.window.map.selection_bounds)

    def test_qt_network_monitor_warning_suppressed(self) -> None:
        self.assertIn(
            "qt.network.monitor.warning=false",
            os.environ.get("QT_LOGGING_RULES", ""),
        )
        self.assertFalse(QLoggingCategory("qt.network.monitor").isWarningEnabled())

    def test_selection_updates_extent_and_range(self) -> None:
        map_widget = self.window.map
        map_widget.start_selection()
        map_widget._pick_point(QPoint(80, 80))
        map_widget._pick_point(QPoint(400, 300))
        self.app.processEvents()

        self.assertFalse(map_widget.selection_mode)
        self.assertIsNotNone(map_widget.selection_bounds)
        self.assertGreater(self.window.north_spin.value(), self.window.south_spin.value())
        self.assertGreater(self.window.east_spin.value(), self.window.west_spin.value())
        self.assertIsNotNone(self.window._range)
        self.assertGreater(self.window._range.count, 0)  # type: ignore[union-attr]

    def test_zoom_syncs_level_and_range(self) -> None:
        self.window.map.set_zoom(12)
        self.app.processEvents()
        self.assertEqual(self.window.level_spin.value(), 12)
        self.assertEqual(self.window.zoom_spin.value(), 12)
        self.assertIsNotNone(self.window._range)
        self.assertEqual(self.window._range.zoom, 12)  # type: ignore[union-attr]

    def test_zoom_at_keeps_cursor_anchor(self) -> None:
        map_widget = self.window.map
        position = QPoint(map_widget.width() // 2 + 120, map_widget.height() // 2 + 80)
        before = map_widget._screen_to_latlng(position)
        map_widget._zoom_at(position, 1)
        after = map_widget._screen_to_latlng(position)
        self.assertAlmostEqual(before[0], after[0], places=6)
        self.assertAlmostEqual(before[1], after[1], places=6)

    def test_log_toggle(self) -> None:
        self.window._set_log_visible(True)
        self.assertTrue(self.window.log_group.isVisible())
        self.assertTrue(self.window.btn_toggle_log.isChecked())
        self.assertEqual(self.window.btn_toggle_log.text(), "隐藏运行日志")
        self.window._set_log_visible(False)
        self.assertFalse(self.window.log_group.isVisible())
        self.assertFalse(self.window.btn_toggle_log.isChecked())

    def test_log_records_source_and_layer_changes_without_navigation_noise(self) -> None:
        self.assertEqual(self.window.log_view.toPlainText(), "")
        self.window.map.set_zoom(11)
        self.window.map.set_center(23.2, 113.2)
        self.window._begin_selection()
        self.window._clear_selection()
        self.assertEqual(self.window.log_view.toPlainText(), "")

        self.window._layer_buttons["satellite"].click()
        lines = self.window.log_view.toPlainText().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("切换图层：高德地图 · 影像", lines[0])
        self.window._on_provider_changed()
        self.window._apply_api_key()
        self.assertEqual(self.window.log_view.toPlainText().splitlines(), lines)

        self.window.source_combo.setCurrentIndex(self.window.source_combo.findData("google"))
        lines = self.window.log_view.toPlainText().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("切换图源：Google 地图 · 卫星", lines[1])

    def test_menu_keyboard_navigation_and_action_shortcuts(self) -> None:
        for action, key in zip(self.window.menuBar().actions(), (Qt.Key_F, Qt.Key_T, Qt.Key_H)):
            menu = action.menu()
            QTest.keyClick(self.window, key, Qt.AltModifier)
            self.app.processEvents()
            self.assertTrue(menu.isVisible(), f"Alt 快捷键没有打开{menu.title()}")
            QTest.keyClick(menu, Qt.Key_Escape)
            self.app.processEvents()
            self.assertFalse(menu.isVisible())
        QTest.keyClick(self.window, Qt.Key_L, Qt.ControlModifier)
        self.app.processEvents()
        self.assertTrue(self.window.log_group.isVisible())
        QTest.keyClick(self.window, Qt.Key_L, Qt.ControlModifier)
        self.app.processEvents()
        self.assertFalse(self.window.log_group.isVisible())

    def test_provider_and_grid(self) -> None:
        self.window.provider_combo.setCurrentIndex(0)
        self.app.processEvents()
        self.assertEqual(self.window.map.provider.key, "vector")
        self.window.grid_check.setChecked(True)
        self.assertTrue(self.window.map._grid_visible)
        self.window.grid_check.setChecked(False)
        self.assertFalse(self.window.map._grid_visible)

    def test_source_switching_to_google(self) -> None:
        google_index = self.window.source_combo.findData("google")
        self.assertGreaterEqual(google_index, 0)
        self.window.source_combo.setCurrentIndex(google_index)
        self.app.processEvents()

        self.assertEqual(self.window.map.provider.key, "google_satellite")
        self.assertIn("WGS-84", self.window._provider_label.text())
        self.assertIn("Google", self.window._provider_label.text())
        self.assertEqual(self.window.map.provider.attribution, "© Google")
        self.assertEqual(Path(self.window.download_dir_edit.text()).name, "google_satellite")

        hybrid_index = self.window.provider_combo.findData("google_hybrid")
        self.assertGreaterEqual(hybrid_index, 0)
        self.window.provider_combo.setCurrentIndex(hybrid_index)
        self.app.processEvents()
        self.assertEqual(self.window.map.provider.key, "google_hybrid")
        self.assertEqual(Path(self.window.download_dir_edit.text()).name, "google_hybrid")

        amap_index = self.window.source_combo.findData("amap")
        self.window.source_combo.setCurrentIndex(amap_index)
        self.app.processEvents()
        self.assertEqual(self.window.map.provider.key, "amap_hybrid")
        self.assertIn("GCJ-02", self.window._provider_label.text())

    def test_google_settings_roundtrip(self) -> None:
        self.window.source_combo.setCurrentIndex(
            self.window.source_combo.findData("google")
        )
        self.window.provider_combo.setCurrentIndex(
            self.window.provider_combo.findData("google_hybrid")
        )
        self.window._save_settings()

        second = MainWindow()
        try:
            self.assertEqual(second.source_combo.currentData(), "google")
            self.assertEqual(second.provider_combo.currentData(), "google_hybrid")
            self.assertIn("WGS-84", second._provider_label.text())
        finally:
            second.close()

    def test_tianditu_key_and_zoom_limit(self) -> None:
        self.window.source_combo.setCurrentIndex(self.window.source_combo.findData("tianditu"))
        self.assertEqual(self.window.map.provider.key, "tianditu_hybrid")
        self.assertTrue(self.window.key_panel.isVisible())
        self.assertIn("CGCS2000", self.window._provider_label.text())
        self.window.map.set_zoom(20)
        self.assertEqual(self.window.map.zoom, 18)
        self.assertEqual(self.window.level_spin.maximum(), 18)
        self.window.api_key_edit.clear()
        self.window._start_download()
        self.assertIsNone(self.window._download_worker)
        self.assertIn("Key", self.window.statusBar().currentMessage())
        self.window.source_combo.setCurrentIndex(self.window.source_combo.findData("esri"))
        self.assertFalse(self.window.key_panel.isVisible())
        self.assertEqual(self.window.map.provider.key, "esri_hybrid")
        self.assertIn("2028", self.window.layer_description.text())

    def test_task_tabs_and_advanced_range(self) -> None:
        self.assertFalse(self.window.tile_details.isVisible())
        self.window.btn_tile_details.click()
        self.assertTrue(self.window.tile_details.isVisible())
        self.assertTrue(self.window.btn_download_start.isVisible())
        self.window.task_tabs.setCurrentIndex(1)
        self.assertTrue(self.window.btn_stitch_start.isVisible())
        self.assertFalse(self.window.btn_download_start.isVisible())

    def test_settings_roundtrip(self) -> None:
        self.window.provider_combo.setCurrentIndex(2)
        self.window.level_spin.setValue(11)
        self.window._set_extent_values(23.4, 23.2, 113.4, 113.7, sync_map=True)
        self.window.download_dir_edit.setText(str(_TEST_TMP / "rdmap-tiles-edit"))
        self.window._save_settings()

        second = MainWindow()
        try:
            self.assertEqual(second.provider_combo.currentData(), "road")
            self.assertIn("路网", second._provider_label.text())
            self.assertEqual(second.level_spin.value(), 11)
            self.assertAlmostEqual(second.north_spin.value(), 23.4, places=6)
            self.assertAlmostEqual(second.south_spin.value(), 23.2, places=6)
            self.assertAlmostEqual(second.west_spin.value(), 113.4, places=6)
            self.assertAlmostEqual(second.east_spin.value(), 113.7, places=6)
        finally:
            second.close()

    def test_invalid_manual_range(self) -> None:
        self.window.row_min_spin.setValue(10)
        self.window.row_max_spin.setValue(5)
        self.app.processEvents()
        self.assertIsNone(self.window._range)
        self.assertIn("无效", self.window.tile_count_label.text())
        self.assertEqual(self.window.download_size_label.text(), "")

    def test_download_estimate_follows_range_levels_and_layer(self) -> None:
        from rdmap.download_estimate import estimate_download_bytes
        from rdmap.paths import format_bytes

        def check_estimate():
            ranges = self.window._selected_download_ranges()
            expected = format_bytes(estimate_download_bytes(self.window.map.provider, ranges))
            self.assertIn(expected, self.window.download_size_label.text())
            for item in ranges:
                self.assertIn(f"Z{item.zoom}：", self.window.download_size_label.toolTip())

        check_estimate()
        initial = self.window.download_size_label.text()
        self.window.level_spin.setValue(14)
        check_estimate()
        self.assertNotEqual(initial, self.window.download_size_label.text())
        self.window._set_extent_values(23.4, 23.2, 113.4, 113.7, sync_map=True)
        check_estimate()
        self.window.multi_level_check.setChecked(True)
        self.window.multi_levels_edit.setText("10-12,14")
        check_estimate()
        hybrid_estimate = self.window.download_size_label.text()
        self.window.provider_combo.setCurrentIndex(self.window.provider_combo.findData("road"))
        check_estimate()
        self.assertNotEqual(hybrid_estimate, self.window.download_size_label.text())
        self.window.multi_levels_edit.setText("invalid")
        self.assertEqual(self.window.download_size_label.text(), "")
        self.assertEqual(self.window.download_size_label.toolTip(), "")
        self.window.multi_level_check.setChecked(False)
        check_estimate()

    @unittest.skipUnless(
        os.environ.get("RDMAP_NETWORK_TESTS") == "1",
        "设置 RDMAP_NETWORK_TESTS=1 才执行真实网络测试",
    )
    def test_download_and_stitch_end_to_end(self) -> None:
        temp = _new_temp_dir("e2e")
        self.window.download_dir_edit.setText(str(temp / "tiles"))
        self.window.concurrency_spin.setValue(4)
        self.window._start_download()
        self.assertTrue(self._wait_for_task("download"))
        self.assertIsNone(self.window._download_worker)
        self.assertIn("完成", self.window.download_status.text())

        tiles = list((temp / "tiles" / "10").rglob("*.png"))
        self.assertEqual(len(tiles), 6)

        self.window.stitch_input_edit.setText(str(temp / "tiles"))
        self.window.stitch_output_edit.setText(str(temp / "stitched.png"))
        self.window._start_stitch()
        self.assertTrue(self._wait_for_task("stitch"))
        self.assertIsNone(self.window._stitch_worker)
        self.assertTrue((temp / "stitched.png").is_file())

        from PyQt5.QtGui import QImage

        image = QImage(str(temp / "stitched.png"))
        self.assertEqual((image.width(), image.height()), (512, 768))
        shutil.rmtree(temp, ignore_errors=True)
    def _wait_for_task(self, kind: str, timeout_ms: int = 90000) -> bool:
        loop = QEventLoop()
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        deadline.start(timeout_ms)

        poll = QTimer()
        poll.setInterval(200)

        def check() -> None:
            worker = (
                self.window._download_worker
                if kind == "download"
                else self.window._stitch_worker
            )
            if worker is None:
                loop.quit()

        poll.timeout.connect(check)
        poll.start()
        loop.exec_()
        poll.stop()
        return (
            self.window._download_worker is None
            if kind == "download"
            else self.window._stitch_worker is None
        )


if __name__ == "__main__":
    unittest.main()
