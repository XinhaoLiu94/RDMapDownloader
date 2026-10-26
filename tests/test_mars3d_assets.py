"""Mars3D 静态资源与本地服务路由测试（不启动 WebEngine）。"""

from __future__ import annotations

import shutil
import re
import unittest
import uuid
from pathlib import Path

import requests

from rdmap.terrain_server import ResourceStore, TerrainServer

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / ".test_tmp"


class Mars3DStaticTests(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(parents=True, exist_ok=True)
        self.root = TMP / ("mars3d-assets-" + uuid.uuid4().hex[:8])
        self.root.mkdir()
        self.store = ResourceStore(self.root)
        self.server = None

    def tearDown(self):
        if self.server is not None:
            self.server.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_bundled_library_has_no_mapbox_access_token(self):
        source = (ROOT / "assets/mars3d/mars3d.js").read_text(encoding="utf-8")
        contains_token = bool(re.search(
            r"\b(?:sk|pk)\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}", source
        ))
        self.assertFalse(contains_token, "第三方脚本不应内置 Mapbox 访问凭据")

    def test_mars3d_page_and_bundled_assets(self):
        self.server = TerrainServer(self.store, engine="mars3d")
        self.assertTrue(self.server.url.endswith("/index-mars3d.html"))
        base = self.server.url.rsplit("/", 1)[0]
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.get(self.server.url, timeout=5)
            self.assertEqual(response.status_code, 200)
            self.assertIn("Mars3D", response.text)
            self.assertIn("qwebchannel.js", response.text)
            self.assertIn("terrain-visual.js", response.text)

            for path, min_length in (
                ("/mars3d/mars3d.js", 1_000_000),
                ("/mars3d/mars3d.css", 10_000),
                ("/mars3d/img/cursor.png", 100),
                ("/turf/turf.min.js", 100_000),
                ("/terrain-visual.js", 1_000),
            ):
                response = session.get(base + path, timeout=5)
                self.assertEqual(response.status_code, 200, path)
                self.assertGreater(len(response.content), min_length, path)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
