"""多级范围、串行队列、批次续传与旧任务库兼容测试。"""
from __future__ import annotations

import csv
import os
import shutil
import sqlite3
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QBuffer, QByteArray, QEventLoop, QIODevice, QSettings, QTimer
from PyQt5.QtGui import QColor, QImage
from PyQt5.QtWidgets import QApplication

from rdmap.download_levels import format_levels, parse_levels, ranges_for_levels
from rdmap.main_window import MainWindow
from rdmap.providers import PROVIDER_MAP
from rdmap.tasks import TaskStore
from rdmap.tile_math import TileRange, range_from_bounds
from rdmap.workers import DownloadOptions, DownloadWorker


class LevelMathTests(unittest.TestCase):
    def test_ranges_lists_sorting_and_duplicates(self):
        self.assertEqual(parse_levels("20, 3-5,4,3，7–8"), (3, 4, 5, 7, 8, 20))
        self.assertEqual(parse_levels("10 - 12"), (10, 11, 12))
        self.assertEqual(format_levels((3, 4, 5, 7, 8, 20)), "3-5, 7-8, 20")

    def test_invalid_and_provider_limits(self):
        for value in ("", ",", "12-10", "0,10", "10,21", "abc", "1-999999999999999999"):
            with self.assertRaises(ValueError):
                parse_levels(value)
        with self.assertRaises(ValueError):
            parse_levels("18-19", max_zoom=18)

    def test_same_geographic_bounds_at_every_level(self):
        bounds = (23.321, 23.320, 113.550, 113.551)
        reference = range_from_bounds(*bounds, 10)
        levels = (9, 10, 12, 15)
        self.assertEqual(ranges_for_levels(levels, bounds, reference),
                         tuple(range_from_bounds(*bounds, z) for z in levels))

    def test_manually_edited_xyz_range_scales_exactly(self):
        reference = TileRange(10, 100, 100, 200, 200)
        self.assertEqual(ranges_for_levels((9, 10, 11), (23.6, 23.1, 113.3, 113.9), reference), (
            TileRange(9, 50, 50, 100, 100), reference, TileRange(11, 200, 201, 400, 401),
        ))


class MultiLevelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        image = QImage(256, 256, QImage.Format_RGB32)
        image.fill(QColor("blue"))
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        assert image.save(buffer, "PNG")
        cls.png = bytes(data)

    def setUp(self):
        self.temp = Path(__file__).resolve().parent.parent / ".test_tmp" / ("multi-" + uuid.uuid4().hex)
        self.temp.mkdir(parents=True)
        self.env = patch.dict(os.environ, {"RDMAP_TASK_DIR": str(self.temp), "RDMAP_CACHE_DIR": str(self.temp / "cache")})
        self.env.start()
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(self.temp / "settings"))
        QSettings().clear()
        self.window = MainWindow()
        self.window.provider_combo.setCurrentIndex(self.window.provider_combo.findData("satellite"))
        self.window.level_spin.setValue(10)
        self.window._set_extent_values(23.321, 23.320, 113.550, 113.551, sync_map=False)
        self.window.download_dir_edit.setText(str(self.temp / "tiles"))
        self.window.concurrency_spin.setValue(1)
        self.window.multi_level_check.setChecked(True)
        self.window.multi_levels_edit.setText("12, 10-11")

    def tearDown(self):
        if self.window is not None:
            if self.window._download_worker is not None:
                self.window._stop_download()
                self.window._download_worker.wait(5000)
                self.app.processEvents()
            self.window.close()
        self.app.processEvents()
        self.env.stop()
        shutil.rmtree(self.temp)

    def wait_download(self, timeout_ms=5000):
        loop = QEventLoop()
        timer = QTimer()
        timer.timeout.connect(lambda: loop.quit() if self.window._download_worker is None else None)
        timer.start(10)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        deadline.start(timeout_ms)
        loop.exec_()
        timer.stop()
        deadline.stop()
        self.assertIsNone(self.window._download_worker, "多级下载没有结束")
        self.assertFalse(self.window._download_queue)

    def fetcher(self, seen, fail_zoom=None):
        def fetch(worker, task):
            zoom = worker._options.tile_range.zoom
            seen.append(zoom)
            if zoom == fail_zoom:
                return "failed", "HTTP 503"
            path = Path(task[2])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.png)
            return "success", ""
        return fetch

    def test_levels_run_in_order_and_are_persisted_as_one_group(self):
        seen = []
        with patch.object(DownloadWorker, "_fetch_one", new=self.fetcher(seen)):
            self.window._start_download()
            self.wait_download()
        self.assertEqual(seen, [10, 11, 12])
        store = self.window._task_store
        self.assertEqual(len(store.list_tasks()), 3)
        groups = store.list_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0]["status"], groups[0]["zoom_label"], groups[0]["done"]), ("completed", "10-12", 3))
        for item in groups[0]["level_tasks"]:
            path = self.temp / "tiles" / str(item["zoom"]) / str(item["min_row"]) / f"{item['min_col']}.png"
            self.assertTrue(path.is_file())
        self.assertEqual(self.window.download_progress.value(), 3)
        self.assertIn("多级下载完成", self.window.download_status.text())
        self.assertEqual(self.window.tile_count_label.text(), "3 张 · 3 级")
        self.window.multi_level_check.setChecked(False)
        self.assertEqual(self.window.tile_count_label.text(), "1 张")
        self.window.multi_level_check.setChecked(True)
        self.window._show_task_manager()
        self.assertEqual(self.window._task_manager.table.rowCount(), 1)
        self.assertEqual(self.window._task_manager.btn_resume.text(), "继续整组下载")
        self.assertFalse(self.window._task_manager.btn_resume.isEnabled())

    def test_failed_level_does_not_block_later_levels_and_resumes_after_restart(self):
        seen = []
        with patch.object(DownloadWorker, "_fetch_one", new=self.fetcher(seen, fail_zoom=11)):
            self.window._start_download()
            self.wait_download()
        self.assertEqual(seen, [10, 11, 12])
        store = self.window._task_store
        group = store.list_groups()[0]
        ids = {task["id"] for task in store.list_tasks()}
        self.assertEqual((group["done"], group["failed"], group["status"]), (2, 1, "partial"))
        self.assertEqual(store.failures(group["id"])[0]["zoom"], 11)
        self.window.close()
        self.window = MainWindow()
        self.assertTrue(self.window.multi_level_check.isChecked())
        self.assertEqual(parse_levels(self.window.multi_levels_edit.text()), (10, 11, 12))
        seen = []
        with patch.object(DownloadWorker, "_fetch_one", new=self.fetcher(seen)):
            self.window._resume_download_task(group["id"])
            self.wait_download()
        self.assertEqual(seen, [11])
        self.assertEqual({task["id"] for task in store.list_tasks()}, ids)
        self.assertEqual(store.list_groups()[0]["status"], "completed")

    def test_stop_cancels_remaining_queue_and_resume_restores_it(self):
        seen = []
        with patch.object(self.window, "_on_download_progress", side_effect=lambda *_args: self.window._stop_download()):
            with patch.object(DownloadWorker, "_fetch_one", new=self.fetcher(seen)):
                self.window._start_download()
                self.wait_download()
        self.assertNotIn(11, seen)
        self.assertNotIn(12, seen)
        group = self.window._task_store.list_groups()[0]
        self.assertNotEqual(group["status"], "completed")
        seen = []
        with patch.object(DownloadWorker, "_fetch_one", new=self.fetcher(seen)):
            self.window._resume_download_task(group["id"])
            self.wait_download()
        self.assertIn(11, seen)
        self.assertIn(12, seen)
        self.assertEqual(self.window._task_store.list_groups()[0]["status"], "completed")

    def test_total_limit_applies_to_all_levels_before_creating_any_tasks(self):
        with patch("rdmap.main_window.MAX_TILE_HARD_LIMIT", 2), patch("rdmap.main_window.QMessageBox.warning") as warning:
            self.window._start_download()
        self.assertTrue(warning.called)
        self.assertFalse(self.window._task_store.list_tasks())
        self.assertIsNone(self.window._download_worker)

    def test_group_keeps_overlapping_xyz_failures_separate_by_level_and_exports_them(self):
        store = self.window._task_store
        options = [DownloadOptions(str(self.temp / "tiles"), PROVIDER_MAP["satellite"], TileRange(z, 10, 10, 20, 20)) for z in (10, 11)]
        ids = store.create_batch(options, "two levels")
        for task_id in ids:
            store.record_tiles(task_id, [(20, 10, "failed", "HTTP 503")])
            store.finish(task_id, False, "failure")
        self.assertEqual([item["zoom"] for item in store.failures(ids[0])], [10, 11])
        self.window._show_task_manager()
        manager = self.window._task_manager
        manager.refresh(ids[1])
        self.assertEqual(manager.selected_id(), ids[0])
        output = self.temp / "failures.csv"
        with patch("rdmap.task_manager.QFileDialog.getSaveFileName", return_value=(str(output), "CSV")):
            manager.export_failures()
        with output.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual([row[0] for row in rows[1:]], ["10", "11"])
        store.delete(ids[0])
        self.assertFalse(store.list_tasks())

    def test_old_database_migrates_without_losing_tile_records(self):
        path = self.temp / "old.sqlite3"
        with sqlite3.connect(path) as db:
            db.executescript("""
                CREATE TABLE tasks (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, provider_key TEXT NOT NULL,
                    output_dir TEXT NOT NULL, zoom INTEGER NOT NULL,
                    min_row INTEGER NOT NULL, max_row INTEGER NOT NULL,
                    min_col INTEGER NOT NULL, max_col INTEGER NOT NULL,
                    concurrency INTEGER NOT NULL, skip_existing INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', message TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE task_tiles (
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    col INTEGER NOT NULL, row INTEGER NOT NULL,
                    status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', PRIMARY KEY(task_id,row,col)
                );
            """)
            db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                "old-task", "old", "satellite", str(self.temp / "tiles"), 10, 10, 10, 20, 21, 1, 1,
                "2026-10-23T10:00:00+08:00", "2026-10-23T10:00:00+08:00", "partial", "old failure",
            ))
            db.execute("INSERT INTO task_tiles VALUES ('old-task',20,10,'success','')")
            db.execute("INSERT INTO task_tiles VALUES ('old-task',21,10,'failed','HTTP 503')")
        db.close()
        store = TaskStore(path)
        old = store.get("old-task")
        self.assertEqual((old["done"], old["failed"], old["batch_id"]), (1, 1, ""))
        options = [DownloadOptions(str(self.temp / "tiles"), PROVIDER_MAP["satellite"], TileRange(z, 10, 10, 20, 20)) for z in (10, 11)]
        ids = store.create_batch(options, "new")
        self.assertEqual(len(store.group_tasks(ids[1])), 2)
        store.delete(ids[0])
        self.assertEqual([task["id"] for task in store.list_tasks()], ["old-task"])
        self.assertEqual(store.failures("old-task")[0]["error"], "HTTP 503")

    @unittest.skipUnless(os.environ.get("RDMAP_NETWORK_TESTS") == "1", "设置 RDMAP_NETWORK_TESTS=1 才执行真实网络测试")
    def test_real_multilevel_download(self):
        self.window._start_download()
        self.wait_download(60000)
        group = self.window._task_store.list_groups()[0]
        self.assertEqual((group["status"], group["done"]), ("completed", 3))
        for item in group["level_tasks"]:
            path = self.temp / "tiles" / str(item["zoom"]) / str(item["min_row"]) / f"{item['min_col']}.png"
            self.assertFalse(QImage(str(path)).isNull())


if __name__ == "__main__":
    unittest.main()
