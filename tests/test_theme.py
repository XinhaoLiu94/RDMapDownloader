"""主题实时切换、系统跟随、设置恢复与新工作区交互。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, QSettings
from PyQt5.QtGui import QPalette
from PyQt5.QtTest import QSignalSpy
from PyQt5.QtWidgets import QApplication

from rdmap.main_window import MainWindow
from rdmap.theme import COLORS, DARK_COLORS, LIGHT_COLORS, theme_manager
from rdmap.tile_math import map_scale


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = Path(__file__).resolve().parent.parent / ".test_tmp" / ("theme-" + uuid.uuid4().hex)
        self.temp.mkdir(parents=True)
        self.env = patch.dict(os.environ, {"RDMAP_TASK_DIR": str(self.temp), "RDMAP_CACHE_DIR": str(self.temp / "cache")})
        self.env.start()
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(self.temp / "settings"))
        QSettings().clear()
        self.manager = theme_manager(self.app)
        self.original_reader = self.manager._read_system
        self.original_mode = self.manager.mode
        self.system = {"scheme": "light"}
        self.manager._read_system = lambda: self.system["scheme"]
        self.manager.set_mode("system")
        self.window = MainWindow()
        self.window.resize(1480, 940)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        self.manager._timer.setInterval(1000)
        self.manager._read_system = self.original_reader
        self.manager.set_mode(self.original_mode, persist=False)
        self.env.stop()
        shutil.rmtree(self.temp)

    def assert_scheme(self, scheme):
        colors = DARK_COLORS if scheme == "dark" else LIGHT_COLORS
        self.assertEqual(self.manager.scheme, scheme)
        self.assertEqual(self.app.palette().color(QPalette.Window).name(), colors["surface"].lower())
        self.assertEqual(COLORS["primary"], colors["primary"])

    def test_manual_toggle_preserves_download_plan_and_ignores_system(self):
        self.window.multi_level_check.setChecked(True)
        self.window.multi_levels_edit.setText("10-12,14")
        before = self.window._selected_download_ranges()
        colors_reference = COLORS
        for mode in ("dark", "light", "dark"):
            self.window.theme_combo.setCurrentIndex(self.window.theme_combo.findData(mode))
            self.app.processEvents()
            self.assert_scheme(mode)
            self.assertIs(COLORS, colors_reference)
            self.assertEqual(self.window._selected_download_ranges(), before)
            self.assertFalse(self.manager._timer.isActive())
            self.system["scheme"] = "light" if mode == "dark" else "dark"
            self.manager.refresh()
            self.assert_scheme(mode)

    def test_system_mode_refreshes_while_running_and_stays_system_in_settings(self):
        self.manager._timer.setInterval(15)
        for scheme in ("dark", "light"):
            changed = QSignalSpy(self.manager.changed)
            self.system["scheme"] = scheme
            self.assertTrue(changed.wait(1500), f"系统主题未刷新：mode={self.manager.mode}, timer={self.manager._timer.isActive()}")
            self.assert_scheme(scheme)
            self.assertEqual(self.window.theme_combo.currentData(), "system")
            self.assertEqual(QSettings().value("ui/theme"), "system")

    def test_manual_choice_restores_on_reopen(self):
        self.window.theme_combo.setCurrentIndex(self.window.theme_combo.findData("dark"))
        self.window.close()
        self.window = MainWindow()
        self.assertEqual(self.window.theme_combo.currentData(), "dark")
        self.assert_scheme("dark")
        QSettings().setValue("ui/theme", "unknown-old-value")
        self.manager.restore()
        self.assertEqual(self.manager.mode, "system")
        self.assert_scheme("light")

    def test_layer_cards_use_existing_providers_and_lock_during_download(self):
        self.window._layer_buttons["satellite"].click()
        self.assertEqual(self.window.map.provider.key, "satellite")
        self.window._set_download_running(True)
        self.window._layer_buttons["road"].click()
        self.assertEqual(self.window.map.provider.key, "satellite")
        self.assertFalse(self.window.level_slider.isEnabled())
        self.window._set_download_running(False)
        self.window.source_combo.setCurrentIndex(self.window.source_combo.findData("google"))
        self.assertIn("google_terrain", self.window._layer_buttons)
        self.window._layer_buttons["google_terrain"].click()
        self.assertEqual(self.window.provider_combo.currentData(), "google_terrain")

    def test_slider_stays_in_sync_with_map_and_provider_limits(self):
        self.window.level_slider.setValue(15)
        self.assertEqual(self.window.level_spin.value(), 15)
        self.assertEqual(self.window._range.zoom, 15)
        self.window.map.set_zoom(12)
        self.assertEqual(self.window.level_slider.value(), 12)
        self.window.source_combo.setCurrentIndex(self.window.source_combo.findData("tianditu"))
        self.assertEqual(self.window.level_slider.maximum(), 18)

    def test_download_and_stitch_buttons_stay_visible_when_settings_scroll(self):
        self.window.resize(1024, 680)
        self.app.processEvents()
        for tab, button in ((0, self.window.btn_download_start), (1, self.window.btn_stitch_start)):
            self.window.task_tabs.setCurrentIndex(tab)
            self.app.processEvents()
            before = button.mapTo(self.window, QPoint())
            self.window._side_scroll.verticalScrollBar().setValue(self.window._side_scroll.verticalScrollBar().maximum())
            self.app.processEvents()
            self.assertEqual(button.mapTo(self.window, QPoint()), before)
            self.assertTrue(button.isVisible())
            self.assertGreater(before.y(), 0)
            self.assertLessEqual(before.y() + button.height(), self.window.height())

    def test_large_tile_totals_fit_cards_and_keep_exact_counts_in_tooltips(self):
        # Windows 的 offscreen 插件缺少字体数据库，须用真实平台测量文本宽度。
        script = '''
import sys
from pathlib import Path
from PyQt5.QtCore import QSettings, Qt
from PyQt5.QtWidgets import QApplication
from rdmap.main_window import MainWindow
app = QApplication([])
app.setOrganizationName('RDMapCountLayoutTest')
app.setApplicationName('RDMapCountLayoutTest')
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(Path(sys.argv[1]) / 'layout-settings'))
window = MainWindow()
window.setAttribute(Qt.WA_DontShowOnScreen, True)
window.resize(1024, 680)
window.download_dock.parentWidget().setFixedWidth(400)
window.show()
for mode in ('light', 'dark'):
    window._theme_manager.set_mode(mode)
    for count in (6, 9999, 22490800, 99999900, 999999000000, 1466015503700):
        window._display_tile_count(count, levels=8)
        app.processEvents()
        for label in (window.tile_count_label, window.tile_count_detail):
            assert not label.wordWrap()
            assert label.fontMetrics().horizontalAdvance(label.text()) <= label.contentsRect().width(), (count, label.text(), label.size())
            assert label.fontMetrics().height() <= label.contentsRect().height()
        assert '8 级' in window.tile_count_detail.text()
        assert f'{count:,} 张 · 8 级' in window.tile_count_card.toolTip()
window._display_tile_count(error='范围无效', tooltip='南北界相反')
assert not window.tile_count_detail.text()
assert window.tile_count_label.toolTip() == '南北界相反'
window._update_level_summary()
assert window.tile_count_detail.text() == '张'
window.close()
app.processEvents()
print('LAYOUT_OK', flush=True)
'''
        env = os.environ.copy()
        if os.name == "nt":
            env.pop("QT_QPA_PLATFORM", None)
        result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script, str(self.temp)],
                                capture_output=True, timeout=20, text=True, env=env, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LAYOUT_OK", result.stdout)

    def test_app_closes_cleanly_after_switching_theme(self):
        script = '''
import sys
from pathlib import Path
from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QApplication
from rdmap.main_window import MainWindow
app = QApplication([])
app.setOrganizationName('RDMapThemeExitTest')
app.setApplicationName('RDMapThemeExitTest')
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(Path(sys.argv[1]) / 'child-settings'))
window = MainWindow()
window.show()
app.processEvents()
window._theme_manager.set_mode('dark')
window._theme_manager.set_mode('light')
window.grab()
window.close()
app.processEvents()
print('CLEAN_EXIT', flush=True)
'''
        result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script, str(self.temp)],
                                capture_output=True, timeout=20, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CLEAN_EXIT", result.stdout)


class ScaleTests(unittest.TestCase):
    def test_scale_matches_zoom_and_latitude(self):
        meters, pixels = map_scale(0, 10)
        self.assertGreater(pixels, 0)
        self.assertLessEqual(pixels, 100)
        _, higher_zoom_pixels = map_scale(0, 11)
        self.assertAlmostEqual(pixels, higher_zoom_pixels)
        high_lat_meters, _ = map_scale(60, 10)
        self.assertAlmostEqual(high_lat_meters, meters / 2)
        for zoom in (0, 10, 20, 30):
            distance, width = map_scale(85, zoom)
            self.assertGreater(distance, 0)
            self.assertTrue(0 < width <= 100)


if __name__ == "__main__":
    unittest.main()
