"""坐标输入、格式切换、预设框选及配置恢复。"""

from __future__ import annotations

import os
import random
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QSettings, Qt
from PyQt5.QtGui import QValidator
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication

from rdmap.coordinates import CoordinateSpinBox, format_dms, parse_coordinate
from rdmap.main_window import MainWindow
from rdmap.regions import REGION_MAP, REGION_PRESETS
from rdmap.tile_math import MAX_LATITUDE, range_from_bounds


class CoordinateMathTests(unittest.TestCase):
    def test_supported_inputs_and_negative_zero_degrees(self):
        for text in ("23°36′00.000″", "23°36'00\"", "23:36:00", "23 36 00", "23.600000°", "23.6"):
            with self.subTest(text=text):
                self.assertAlmostEqual(parse_coordinate(text, "dms"), 23.6)
        self.assertEqual(parse_coordinate("-0°30′00″", "dms"), -0.5)
        self.assertEqual(parse_coordinate("-113:18:30", "dms"), -(113 + 18 / 60 + 30 / 3600))
        self.assertEqual(parse_coordinate("−0.5"), -0.5)

    def test_invalid_minutes_seconds_and_malformed_input(self):
        for text in ("23°60′00″", "23°00′60″", "23:-1:00", "23:30:-1", "23:30", "nan", "inf", "1e2", "hello"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_coordinate(text, "dms")
        with self.assertRaises(ValueError):
            parse_coordinate("23:36:00", "decimal")

    def test_rounding_carries_and_precision_roundtrip(self):
        self.assertEqual(format_dms(12 + 59 / 60 + 59.9999 / 3600), "13°00′00.000″")
        self.assertEqual(format_dms(-0.5), "-0°30′00.000″")
        rng = random.Random(101)
        for value in (0, 180, -180, MAX_LATITUDE, -MAX_LATITUDE, 0.000001, -0.000001,
                      *(round(rng.uniform(-180, 180), 6) for _ in range(1000))):
            self.assertEqual(round(parse_coordinate(format_dms(value), "dms"), 6), round(value, 6))


class CoordinateInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.spin = CoordinateSpinBox()
        self.spin.setRange(-180, 180)

    def tearDown(self):
        self.spin.close()

    def test_toggle_preserves_value_and_does_not_emit_changes(self):
        for value in (-179.999999, -0.000001, 23.600001, 180):
            self.spin.setValue(value)
            changed = QSignalSpy(self.spin.valueChanged)
            for _ in range(10):
                self.spin.set_coordinate_format("dms")
                self.assertEqual(self.spin.text(), format_dms(value))
                self.spin.set_coordinate_format("decimal")
                self.assertEqual(self.spin.value(), value)
            self.assertEqual(len(changed), 0)

    def test_edit_commit_step_and_range_validation(self):
        self.spin.set_coordinate_format("dms")
        self.spin.lineEdit().selectAll()
        QTest.keyClicks(self.spin.lineEdit(), "-0:30:00")
        QTest.keyClick(self.spin, Qt.Key_Return)
        self.assertEqual(self.spin.value(), -0.5)
        self.assertEqual(self.spin.text(), "-0°30′00.000″")
        self.spin.stepUp()
        self.assertAlmostEqual(self.spin.value(), -0.5 + 1 / 3600, places=6)
        self.assertEqual(self.spin.validate("12°60′00″", 0)[0], QValidator.Invalid)
        self.assertEqual(self.spin.validate("12°30′", 0)[0], QValidator.Intermediate)
        self.spin.setRange(-MAX_LATITUDE, MAX_LATITUDE)
        self.assertEqual(self.spin.validate("86:00:00", 0)[0], QValidator.Invalid)
        self.assertEqual(self.spin.validate("-", 1)[0], QValidator.Intermediate)

    def test_pending_input_is_committed_before_format_switch(self):
        self.spin.lineEdit().selectAll()
        QTest.keyClicks(self.spin.lineEdit(), "-113.550123")
        self.spin.set_coordinate_format("dms")
        self.assertEqual(self.spin.value(), -113.550123)
        self.assertEqual(self.spin.text(), format_dms(-113.550123))


class RegionWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = Path(__file__).resolve().parent.parent / ".test_tmp" / ("coords-" + uuid.uuid4().hex)
        self.temp.mkdir(parents=True)
        self.env = patch.dict(os.environ, {"RDMAP_TASK_DIR": str(self.temp), "RDMAP_CACHE_DIR": str(self.temp / "cache")})
        self.env.start()
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(self.temp / "settings"))
        QSettings().clear()
        self.window = MainWindow()
        self.window.resize(1280, 850)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        self.env.stop()
        shutil.rmtree(self.temp)

    def select_region(self, key):
        self.window.region_combo.setCurrentIndex(self.window.region_combo.findData(key))
        self.app.processEvents()

    def test_every_preset_updates_coordinates_selection_and_fits_view(self):
        for preset in REGION_PRESETS:
            with self.subTest(region=preset.key):
                self.select_region(preset.key)
                self.assertEqual(self.window._extent_values(), preset.bounds)
                self.assertEqual(self.window.map.selection_bounds, preset.bounds)
                self.assertEqual(self.window._range, range_from_bounds(*preset.bounds, self.window.level_spin.value()))
                # 高纬度视野或新西兰附近会跨越 ±180°，直接检查投影后的边角。
                for latitude, longitude in ((preset.north, preset.west), (preset.south, preset.east)):
                    x, y = self.window.map._latlng_to_screen(latitude, longitude)
                    self.assertGreaterEqual(x, 0)
                    self.assertLessEqual(x, self.window.map.width())
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(y, self.window.map.height())

    def test_format_switch_preserves_preset_range_and_multi_level_plan(self):
        self.select_region("us_contiguous")
        self.window.multi_level_check.setChecked(True)
        self.window.multi_levels_edit.setText("3-5,7")
        before = self.window._selected_download_ranges()
        self.window.coord_format_combo.setCurrentIndex(self.window.coord_format_combo.findData("dms"))
        self.assertEqual(self.window._selected_download_ranges(), before)
        self.assertEqual(self.window.region_combo.currentData(), "us_contiguous")
        self.assertEqual(self.window.west_spin.text(), "-125°00′00.000″")
        self.assertFalse(self.window.multi_levels_hint.isHidden())
        self.assertFalse(self.window.download_plan_label.text())

    def test_manual_edit_or_map_selection_marks_region_custom(self):
        self.select_region("china_mainland")
        self.window.north_spin.setValue(53.0)
        self.assertEqual(self.window.region_combo.currentData(), "")
        self.select_region("us_contiguous")
        self.window._on_map_selection(25, 23, 112, 114)
        self.assertEqual(self.window.region_combo.currentData(), "")
        self.assertEqual(self.window._extent_values(), (25, 23, 112, 114))

    def test_startup_defaults_to_custom_preserving_format_and_bounds(self):
        self.select_region("australia")
        self.window.coord_format_combo.setCurrentIndex(self.window.coord_format_combo.findData("dms"))
        self.window.close()
        # 兼容旧配置：即使保存过国家预设，下次打开也应选择自定义。
        QSettings().setValue("range/preset", "australia")
        self.window = MainWindow()
        self.assertEqual(self.window.coord_format_combo.currentData(), "dms")
        self.assertEqual(self.window.region_combo.currentData(), "")
        self.assertEqual(self.window.region_combo.currentText(), "自定义区域")
        self.assertTrue(self.window.region_hint.isHidden())
        self.assertIsNone(self.window.map.selection_bounds)
        self.assertEqual(self.window._extent_values(), REGION_MAP["australia"].bounds)
        self.assertEqual(self.window.north_spin.text(), "-10°30′00.000″")

    def test_running_tasks_lock_region_and_format_controls(self):
        for setter in (self.window._set_download_running, self.window._set_stitch_running):
            setter(True)
            self.assertFalse(self.window.region_combo.isEnabled())
            self.assertFalse(self.window.coord_format_combo.isEnabled())
            setter(False)
            self.assertTrue(self.window.region_combo.isEnabled())
            self.assertTrue(self.window.coord_format_combo.isEnabled())


if __name__ == "__main__":
    unittest.main()
