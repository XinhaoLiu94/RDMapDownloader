"""下载任务持久化、失败续传、停止及界面恢复测试。"""
from __future__ import annotations

import csv
import os
import shutil
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QBuffer, QByteArray, QEventLoop, QIODevice, QSettings, QTimer
from PyQt5.QtGui import QColor, QImage
from PyQt5.QtWidgets import QApplication

from rdmap.main_window import MainWindow
from rdmap.providers import PROVIDER_MAP
from rdmap.task_manager import TaskManagerDialog
from rdmap.tasks import TaskStore
from rdmap.tile_math import TileRange
from rdmap.workers import DownloadOptions, DownloadWorker


class TaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        image = QImage(256, 256, QImage.Format_RGB32)
        image.fill(QColor("green"))
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        assert image.save(buffer, "PNG")
        cls.png = bytes(data)

    def setUp(self) -> None:
        self.temp = Path(__file__).resolve().parent.parent / ".test_tmp" / ("tasks-" + uuid.uuid4().hex)
        self.temp.mkdir(parents=True)
        self.store = TaskStore(self.temp / "tasks.sqlite3")
        self.options = DownloadOptions(
            output_dir=str(self.temp / "tiles"), provider=PROVIDER_MAP["satellite"],
            tile_range=TileRange(10, 10, 10, 20, 22), concurrency=1, retries=0,
            skip_existing=False, api_key="secret-that-must-not-be-stored",
        )
        self.task_id = self.store.create(self.options)
        self.options = replace(self.options, task_id=self.task_id, task_db=str(self.store.path))

    def tearDown(self) -> None:
        self.app.processEvents()
        shutil.rmtree(self.temp)

    def response(self, status: int = 200) -> Mock:
        return Mock(status_code=status, content=self.png)

    def run_worker(self, options, responses):
        worker = DownloadWorker(options)
        session = Mock()
        session.get.side_effect = responses
        worker._session = lambda: session
        result = {}
        worker.completed.connect(lambda ok, message, stats: result.update(ok=ok, message=message, stats=stats))
        worker.run()
        return result, session

    def test_resume_keeps_task_and_only_fetches_failed_tiles_even_when_overwriting(self) -> None:
        # 原任务不跳过旧文件；失败位置故意留有旧图，续传仍必须重试该位置。
        old_file = self.temp / "tiles" / "10" / "10" / "21.png"
        old_file.parent.mkdir(parents=True)
        old_file.write_bytes(self.png)
        result, session = self.run_worker(self.options, [self.response(), self.response(503), self.response()])
        self.assertFalse(result["ok"])
        self.assertEqual(session.get.call_count, 3)
        task = TaskStore(self.store.path).get(self.task_id)
        self.assertEqual((task["done"], task["failed"], task["status"]), (2, 1, "partial"))
        self.assertEqual(self.store.failures(self.task_id)[0]["error"], "HTTP 503")
        result, session = self.run_worker(self.options, [self.response()])
        self.assertTrue(result["ok"], result["message"])
        self.assertEqual(session.get.call_count, 1)
        self.assertIn("x=21", session.get.call_args.args[0])
        task = self.store.get(self.task_id)
        self.assertEqual((task["done"], task["failed"], task["status"]), (3, 0, "completed"))
        self.assertEqual(len(self.store.list_tasks()), 1)
        self.assertFalse(self.store.failures(self.task_id))

    def test_stop_records_result_of_inflight_request_and_resumes_unattempted_tiles(self) -> None:
        worker = DownloadWorker(self.options)
        def fetch(task):
            if task[0] == 20:
                Path(task[2]).parent.mkdir(parents=True, exist_ok=True)
                Path(task[2]).write_bytes(self.png)
                worker.cancel()
                return "success", ""
            return "cancelled", ""
        worker._fetch_one = fetch
        worker.run()
        task = self.store.get(self.task_id)
        self.assertEqual((task["done"], task["status"]), (1, "paused"))
        result, session = self.run_worker(self.options, [self.response(), self.response()])
        self.assertTrue(result["ok"])
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(self.store.get(self.task_id)["status"], "completed")

    def test_failed_records_are_complete_and_exportable(self) -> None:
        options = replace(self.options, tile_range=TileRange(10, 10, 10, 20, 169))
        task_id = self.store.create(options)
        self.store.record_tiles(task_id, [(col, 10, "failed", "HTTP 403") for col in range(20, 170)])
        self.store.finish(task_id, False, "有失败")
        self.assertEqual(self.store.get(task_id)["failed"], 150)
        self.assertEqual(len(self.store.failures(task_id)), 150)
        dialog = TaskManagerDialog(self.store)
        try:
            dialog.refresh(task_id)
            output = self.temp / "failures.csv"
            with patch("rdmap.task_manager.QFileDialog.getSaveFileName", return_value=(str(output), "CSV")):
                dialog.export_failures()
            with output.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(len(rows), 151)
            self.assertEqual(rows[-1], ["10", "10", "169", "HTTP 403"])
        finally:
            dialog.close()

    def test_crash_recovery_and_credentials_are_not_persisted(self) -> None:
        self.store.start(self.task_id)
        self.store.record_tiles(self.task_id, [(20, 10, "success", ""), (21, 10, "failed", "timeout")])
        reopened = TaskStore(self.store.path)
        reopened.recover_interrupted()
        task = reopened.get(self.task_id)
        self.assertEqual((task["status"], task["done"], task["failed"]), ("paused", 1, 1))
        with reopened.connection() as db:
            dump = "\n".join(db.iterdump())
        self.assertNotIn(self.options.api_key, dump)

    def test_corrupted_existing_file_is_downloaded_again(self) -> None:
        path = self.temp / "tiles" / "10" / "10" / "20.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"<html>server error</html>")
        worker = DownloadWorker(replace(self.options, skip_existing=True))
        session = Mock()
        session.get.return_value = self.response()
        worker._session = lambda: session
        self.assertEqual(worker._fetch_one(next(worker._iter_tasks())), ("success", ""))
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(path.read_bytes(), self.png)

    def test_delete_record_preserves_tile_files(self) -> None:
        path = self.temp / "tiles" / "keep.png"
        path.parent.mkdir()
        path.write_bytes(self.png)
        self.store.record_tiles(self.task_id, [(20, 10, "failed", "timeout")])
        self.store.delete(self.task_id)
        self.assertTrue(path.is_file())
        self.assertFalse(self.store.list_tasks())
        self.assertFalse(self.store.failures(self.task_id))

    def test_window_restores_exact_task_settings_without_creating_another_task(self) -> None:
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(self.temp / "settings"))
        with patch.dict(os.environ, {"RDMAP_TASK_DIR": str(self.temp), "RDMAP_CACHE_DIR": str(self.temp / "cache")}):
            window = MainWindow()
            try:
                window.source_combo.setCurrentIndex(window.source_combo.findData("google"))
                window.level_spin.setValue(15)
                with patch.object(window, "_launch_download") as launch:
                    window._resume_download_task(self.task_id)
                options, task_id = launch.call_args.args
                self.assertEqual(task_id, self.task_id)
                self.assertEqual(options.tile_range, self.options.tile_range)
                self.assertEqual(options.provider.key, "satellite")
                self.assertEqual(options.output_dir, self.options.output_dir)
                self.assertEqual(window._range, self.options.tile_range)
                self.assertEqual(window.row_min_spin.value(), 10)
                self.assertEqual(window.col_max_spin.value(), 22)
                self.assertEqual(len(window._task_store.list_tasks()), 1)
                window._show_task_manager()
                self.assertEqual(window._task_manager.table.rowCount(), 1)
            finally:
                window.close()

    def test_resume_button_finishes_saved_task_in_background_and_survives_restart(self) -> None:
        # 一张成功、一张失败、一张未尝试，模拟关闭软件后的任务库。
        path = self.temp / "tiles" / "10" / "10" / "20.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(self.png)
        self.store.record_tiles(self.task_id, [(20, 10, "success", ""), (21, 10, "failed", "HTTP 503")])
        self.store.finish(self.task_id, False, "有失败，可继续")
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(self.temp / "settings"))
        with patch.dict(os.environ, {"RDMAP_TASK_DIR": str(self.temp), "RDMAP_CACHE_DIR": str(self.temp / "cache")}):
            window = MainWindow()
            try:
                window._show_task_manager()
                manager = window._task_manager
                manager.refresh(self.task_id)
                self.assertTrue(manager.btn_resume.isEnabled())
                session = Mock()
                session.get.return_value = self.response()
                with patch.object(DownloadWorker, "_session", return_value=session):
                    manager.btn_resume.click()
                    loop = QEventLoop()
                    timer = QTimer()
                    timer.timeout.connect(lambda: loop.quit() if window._download_worker is None else None)
                    timer.start(20)
                    deadline = QTimer()
                    deadline.setSingleShot(True)
                    deadline.timeout.connect(loop.quit)
                    deadline.start(5000)
                    loop.exec_()
                    timer.stop()
                    deadline.stop()
                self.assertIsNone(window._download_worker)
                self.assertEqual(session.get.call_count, 2)
                self.assertEqual(len(self.store.list_tasks()), 1)
                self.assertEqual(self.store.get(self.task_id)["status"], "completed")
                self.assertFalse(manager.btn_resume.isEnabled())
            finally:
                window.close()
            reopened = MainWindow()
            try:
                self.assertEqual(reopened._task_store.get(self.task_id)["done"], 3)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
