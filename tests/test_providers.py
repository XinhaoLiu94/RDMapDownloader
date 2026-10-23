"""图源定义与 URL 生成测试。"""

from __future__ import annotations

import unittest

from rdmap.providers import (
    AMAP_REFERER,
    DEFAULT_PROVIDER_KEY,
    DEFAULT_SOURCE_KEY,
    GOOGLE_REFERER,
    MAP_PROVIDERS,
    MAP_SOURCES,
    PROVIDER_MAP,
    SOURCE_MAP,
    providers_for_source,
    tile_headers,
)


class ProviderTests(unittest.TestCase):
    def test_sources_cover_all_providers(self) -> None:
        source_keys = [source.key for source in MAP_SOURCES]
        self.assertEqual(source_keys, ["amap", "google", "tianditu", "esri"])
        self.assertEqual(DEFAULT_SOURCE_KEY, "amap")

        covered = {
            provider_key
            for source in MAP_SOURCES
            for provider_key in source.provider_keys
        }
        self.assertEqual(covered, {provider.key for provider in MAP_PROVIDERS})
        self.assertEqual(len(MAP_PROVIDERS), len(PROVIDER_MAP))

    def test_source_metadata(self) -> None:
        self.assertEqual(SOURCE_MAP["amap"].coordinate_system, "GCJ-02")
        self.assertEqual(SOURCE_MAP["google"].coordinate_system, "区域相关")
        self.assertEqual(PROVIDER_MAP[DEFAULT_PROVIDER_KEY].source, "amap")
        self.assertEqual(SOURCE_MAP["google"].default_provider_key, "google_satellite")
        self.assertEqual(len(providers_for_source("amap")), 4)
        self.assertEqual(len(providers_for_source("google")), 4)

    def test_amap_urls(self) -> None:
        vector = PROVIDER_MAP["vector"].url(10, 834, 443)
        satellite = PROVIDER_MAP["satellite"].url(10, 834, 443)
        road = PROVIDER_MAP["road"].url(10, 834, 443)
        self.assertIn("webrd0", vector)
        self.assertIn("style=7", vector)
        self.assertIn("webst0", satellite)
        self.assertIn("style=6", satellite)
        self.assertIn("style=8", road)
        self.assertIn("ltype=3", road)
        for url in (vector, satellite, road):
            self.assertIn("x=834", url)
            self.assertIn("y=443", url)
            self.assertIn("z=10", url)

    def test_google_urls(self) -> None:
        expected_lyrs = {
            "google_street": "lyrs=m",
            "google_satellite": "lyrs=s",
            "google_hybrid": "lyrs=y",
            "google_terrain": "lyrs=p",
        }
        for key, lyrs in expected_lyrs.items():
            provider = PROVIDER_MAP[key]
            url = provider.url(12, 3339, 1775)
            self.assertRegex(url, r"https://mt[0-3]\.google\.com/vt")
            self.assertIn(lyrs, url)
            self.assertIn("hl=zh-CN", url)
            self.assertIn("x=3339", url)
            self.assertIn("y=1775", url)
            self.assertIn("z=12", url)
            self.assertIn("WGS-84", provider.coordinate_system)
            self.assertEqual(provider.attribution, "© Google")

    def test_tile_headers_referer(self) -> None:
        amap_headers = tile_headers(1, 2, AMAP_REFERER)
        google_headers = tile_headers(1, 2, GOOGLE_REFERER)
        self.assertEqual(amap_headers["Referer"], AMAP_REFERER)
        self.assertEqual(google_headers["Referer"], GOOGLE_REFERER)
        self.assertIn("Chrome", amap_headers["User-Agent"])

    def test_hybrid_layers_and_tianditu_key(self) -> None:
        urls = PROVIDER_MAP["amap_hybrid"].urls(12, 3339, 1775)
        self.assertEqual(len(urls), 2)
        self.assertIn("style=6", urls[0])
        self.assertIn("style=8", urls[1])
        # ltype=3 会过滤掉地名，完整标注图层必须保留。
        self.assertNotIn("ltype=3", urls[1])
        provider = PROVIDER_MAP["tianditu_hybrid"]
        with self.assertRaises(ValueError):
            provider.urls(12, 3339, 1775)
        urls = provider.urls(12, 3339, 1775, "test&key")
        self.assertIn("/img_w/wmts?", urls[0])
        self.assertIn("/cia_w/wmts?", urls[1])
        for url in urls:
            self.assertIn("TILEMATRIX=12&TILEROW=1775&TILECOL=3339", url)
            self.assertIn("tk=test%26key", url)
        self.assertEqual(provider.max_zoom, 18)
        self.assertEqual(len(PROVIDER_MAP["esri_hybrid"].urls(12, 3339, 1775)), 3)

    def test_unknown_provider(self) -> None:
        with self.assertRaises(ValueError):
            providers_for_source("unknown")
        with self.assertRaises(ValueError):
            from rdmap.providers import provider_by_key

            provider_by_key("unknown")


if __name__ == "__main__":
    unittest.main()
