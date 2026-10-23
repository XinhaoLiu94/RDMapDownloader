"""瓦片下载/拼接辅助函数与离线拼接测试。"""

from __future__ import annotations

import atexit
import os
import shutil
import unittest
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QColor, QImage
from PyQt5.QtWidgets import QApplication

from rdmap.tile_math import TileRange
from rdmap.workers import (
    StitchOptions,
    StitchWorker,
    _looks_like_image,
    output_image_format,
)

_TEST_TMP = Path(__file__).resolve().parent.parent / ".test_tmp"
_TEST_TMP.mkdir(parents=True, exist_ok=True)
atexit.register(lambda: shutil.rmtree(_TEST_TMP, ignore_errors=True))


def _new_temp_dir(prefix: str) -> Path:
    """不使用 tempfile.mkdtemp：其 0700 模式在当前 Windows 沙箱下会生成不可访问目录。"""
    path = _TEST_TMP / f"{prefix}-{uuid.uuid4().hex[:10]}"
    path.mkdir()
    return path


def get_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class WorkerHelperTests(unittest.TestCase):
    def test_output_format(self) -> None:
        self.assertEqual(output_image_format("a.png"), ("PNG", -1))
        self.assertEqual(output_image_format("a.JPG")[0], "JPEG")
        self.assertEqual(output_image_format("a.webp")[0], "WEBP")
        self.assertIsNone(output_image_format("a.txt"))
        self.assertIsNone(output_image_format("noext"))

    def test_image_magic(self) -> None:
        self.assertTrue(_looks_like_image(b"\x89PNG\r\n\x1a\n" + b"0" * 20))
        self.assertTrue(_looks_like_image(b"\xff\xd8\xff\xe0" + b"0" * 20))
        self.assertTrue(_looks_like_image(b"RIFF1234WEBP" + b"0" * 20))
        self.assertFalse(_looks_like_image(b"<html>error</html>"))
        self.assertFalse(_looks_like_image(b""))


class StitchWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = get_app()

    def test_stitch_solid_tiles_and_missing(self) -> None:
        temp = _new_temp_dir("stitch")
        try:
            root = temp / "tiles"
            colors = {
                (200, 100): QColor("#ff0000"),
                (201, 100): QColor("#00ff00"),
                (202, 100): QColor("#0000ff"),
                (200, 101): QColor("#ffff00"),
                (201, 101): QColor("#00ffff"),
                # (202, 101) 故意缺失
            }
            for (col, row), color in colors.items():
                directory = root / "12" / str(row)
                directory.mkdir(parents=True, exist_ok=True)
                image = QImage(256, 256, QImage.Format_RGB32)
                image.fill(color)
                self.assertTrue(image.save(str(directory / f"{col}.png"), "PNG"))

            tile_range = TileRange(
                zoom=12,
                min_row=100,
                max_row=101,
                min_col=200,
                max_col=202,
            )
            output = temp / "out.png"
            worker = StitchWorker(
                StitchOptions(
                    tile_root=str(root),
                    output_path=str(output),
                    zoom=12,
                    tile_range=tile_range,
                )
            )
            result: dict[str, object] = {}
            worker.completed.connect(
                lambda ok, message, stats: result.update(ok=ok, message=message, stats=stats)
            )
            worker.run()

            self.assertTrue(result["ok"], result["message"])
            self.assertTrue(output.is_file())
            image = QImage(str(output))
            self.assertFalse(image.isNull())
            self.assertEqual((image.width(), image.height()), (768, 512))
            self.assertEqual(QColor(image.pixel(10, 10)).name(), "#ff0000")
            self.assertEqual(QColor(image.pixel(266, 10)).name(), "#00ff00")
            self.assertEqual(QColor(image.pixel(522, 10)).name(), "#0000ff")
            self.assertEqual(QColor(image.pixel(10, 266)).name(), "#ffff00")
            self.assertEqual(QColor(image.pixel(266, 266)).name(), "#00ffff")
            # 缺失的瓦片保持透明
            self.assertEqual(image.pixelColor(522, 266).alpha(), 0)
            self.assertEqual(int(result["stats"]["missing"]), 1)
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_unsupported_extension(self) -> None:
        tile_range = TileRange(zoom=10, min_row=0, max_row=0, min_col=0, max_col=0)
        output = _TEST_TMP / f"out-{uuid.uuid4().hex[:8]}.xyz"
        worker = StitchWorker(
            StitchOptions(
                tile_root=str(_TEST_TMP),
                output_path=str(output),
                zoom=10,
                tile_range=tile_range,
            )
        )
        result: dict[str, object] = {}
        worker.completed.connect(lambda ok, message, stats: result.update(ok=ok, message=message))
        worker.run()
        self.assertFalse(result["ok"])
        self.assertFalse(output.exists())

    def test_reject_oversized_output(self) -> None:
        tile_range = TileRange(zoom=10, min_row=0, max_row=999, min_col=0, max_col=999)
        output = _TEST_TMP / f"huge-{uuid.uuid4().hex[:8]}.png"
        worker = StitchWorker(
            StitchOptions(
                tile_root=str(_TEST_TMP),
                output_path=str(output),
                zoom=10,
                tile_range=tile_range,
            )
        )
        result: dict[str, object] = {}
        worker.completed.connect(lambda ok, message, stats: result.update(ok=ok, message=message))
        worker.run()
        self.assertFalse(result["ok"])
        self.assertIn("过大", str(result["message"]))
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
