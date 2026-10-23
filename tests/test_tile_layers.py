"""检查标注合成、异步乱序返回，以及下载不能丢失注记。"""
from __future__ import annotations

import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QBuffer, QByteArray, QIODevice
from PyQt5.QtGui import QColor, QImage
from PyQt5.QtNetwork import QNetworkReply
from PyQt5.QtWidgets import QApplication

from rdmap.map_widget import TileMapWidget
from rdmap.providers import PROVIDER_MAP
from rdmap.tile_images import compose_tile
from rdmap.tile_math import TileRange
from rdmap.workers import DownloadOptions, DownloadWorker


def png(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(data)


class TileLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        base = QImage(256, 256, QImage.Format_ARGB32)
        base.fill(QColor("red"))
        overlay = QImage(256, 256, QImage.Format_ARGB32)
        overlay.fill(QColor(0, 0, 0, 0))
        overlay.setPixelColor(10, 10, QColor("blue"))
        overlay.setPixelColor(11, 11, QColor(0, 0, 255, 128))
        cls.base, cls.overlay = png(base), png(overlay)

    def setUp(self) -> None:
        self.temp = Path(__file__).resolve().parent.parent / ".test_tmp" / ("layers-" + uuid.uuid4().hex)
        self.temp.mkdir(parents=True)
        self.map = TileMapWidget(cache_dir=self.temp / "cache")

    def tearDown(self) -> None:
        self.map.close()
        self.map.deleteLater()
        self.app.processEvents()
        shutil.rmtree(self.temp)

    def assert_label(self, data: bytes) -> None:
        image = QImage.fromData(data)
        self.assertEqual(image.pixelColor(10, 10), QColor("blue"))
        self.assertEqual(image.pixelColor(20, 20), QColor("red"))
        blended = image.pixelColor(11, 11)
        self.assertTrue(126 <= blended.red() <= 128)
        self.assertTrue(127 <= blended.blue() <= 129)
        self.assertEqual(blended.alpha(), 255)

    def test_alpha_composite_preserves_imagery_and_labels(self) -> None:
        self.assert_label(compose_tile([self.base, self.overlay]))

    def test_reject_error_pages_and_wrong_size(self) -> None:
        small = QImage(128, 128, QImage.Format_RGB32)
        small.fill(QColor("red"))
        for parts in ([], [b"<html>denied</html>"], [self.base, b""], [png(small)]):
            with self.assertRaises(ValueError):
                compose_tile(parts)

    def reply(self, data: bytes, status: int = 200) -> Mock:
        reply = Mock()
        reply.error.return_value = QNetworkReply.NoError
        reply.attribute.return_value = status
        reply.readAll.return_value = QByteArray(data)
        return reply

    def test_preview_out_of_order_layers_only_cache_when_complete(self) -> None:
        key = ("amap_hybrid", 12, 3339, 1775)
        self.map._tile_parts[key] = [None, None]
        self.map._pending.add(key)
        overlay = self.reply(self.overlay)
        self.map._inflight[overlay] = (key, 1)
        self.map._on_tile_reply(overlay)
        self.assertIn(key, self.map._pending)
        self.assertFalse(self.map._tile_path(key).exists())
        base = self.reply(self.base)
        self.map._inflight[base] = (key, 0)
        self.map._on_tile_reply(base)
        self.assertNotIn(key, self.map._pending)
        self.assert_label(self.map._tile_path(key).read_bytes())

    def test_preview_failed_overlay_never_caches_partial_tile(self) -> None:
        key = ("amap_hybrid", 12, 3339, 1775)
        self.map._tile_parts[key] = [None, None]
        self.map._pending.add(key)
        failed, late = self.reply(b"denied", 403), self.reply(self.base)
        self.map._inflight[failed], self.map._inflight[late] = (key, 1), (key, 0)
        self.map._on_tile_reply(failed)
        self.map._on_tile_reply(late)
        self.assertNotIn(key, self.map._pending)
        self.assertIn(key, self.map._failed_until)
        self.assertFalse(self.map._tile_path(key).exists())

    def worker(self) -> DownloadWorker:
        return DownloadWorker(DownloadOptions(
            output_dir=str(self.temp / "tiles"), provider=PROVIDER_MAP["amap_hybrid"],
            tile_range=TileRange(12, 1775, 1775, 3339, 3339), retries=0,
        ))

    def test_download_writes_composite_and_stitch_preserves_labels(self) -> None:
        from rdmap.workers import StitchOptions, StitchWorker
        worker = self.worker()
        session = Mock()
        for_response = []
        for content in (self.base, self.overlay):
            response = Mock(status_code=200, content=content)
            for_response.append(response)
        session.get.side_effect = for_response
        worker._session = lambda: session
        task = next(worker._iter_tasks())
        self.assertEqual(worker._fetch_one(task), ("success", ""))
        self.assert_label(Path(task[2]).read_bytes())
        stitched = self.temp / "stitched.png"
        stitcher = StitchWorker(StitchOptions(
            tile_root=worker._options.output_dir, output_path=str(stitched),
            zoom=12, tile_range=worker._options.tile_range,
        ))
        stitcher.run()
        self.assert_label(stitched.read_bytes())

    def test_download_failed_overlay_is_not_a_success(self) -> None:
        worker = self.worker()
        session = Mock()
        session.get.side_effect = [Mock(status_code=200, content=self.base), Mock(status_code=403)]
        worker._session = lambda: session
        task = next(worker._iter_tasks())
        self.assertEqual(worker._fetch_one(task), ("failed", "HTTP 403"))
        self.assertFalse(Path(task[2]).exists())

    def test_missing_key_does_not_queue_network_requests(self) -> None:
        self.map.set_provider("tianditu_hybrid")
        self.map._enqueue_tile(("tianditu_hybrid", 12, 3339, 1775))
        self.assertFalse(self.map._inflight)
        self.assertFalse(self.map._pending)
