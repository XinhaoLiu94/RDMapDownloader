"""下载量预估：完整范围、图层请求量和多级合计。"""

import unittest

from rdmap.download_estimate import estimate_download_bytes
from rdmap.providers import MAP_PROVIDERS, PROVIDER_MAP
from rdmap.tile_math import TileRange


class DownloadEstimateTests(unittest.TestCase):
    def test_total_is_additive_for_levels_and_tile_counts(self):
        provider = PROVIDER_MAP["amap_hybrid"]
        first, second = TileRange(10, 10, 10, 20, 21), TileRange(14, 10, 11, 20, 23)
        total = estimate_download_bytes(provider, (first, second))
        self.assertEqual(total, estimate_download_bytes(provider, (first,)) + estimate_download_bytes(provider, (second,)))
        doubled = TileRange(10, 10, 11, 20, 21)
        self.assertEqual(estimate_download_bytes(provider, (doubled,)), 2 * estimate_download_bytes(provider, (first,)))

    def test_overlays_add_to_download_and_roads_are_smaller(self):
        ranges = (TileRange(10, 10, 10, 20, 20),)
        for satellite, hybrid in (("satellite", "amap_hybrid"), ("google_satellite", "google_hybrid"),
                                  ("tianditu_satellite", "tianditu_hybrid"), ("esri_satellite", "esri_hybrid")):
            self.assertGreater(estimate_download_bytes(PROVIDER_MAP[hybrid], ranges),
                               estimate_download_bytes(PROVIDER_MAP[satellite], ranges))
        self.assertLess(estimate_download_bytes(PROVIDER_MAP["road"], ranges),
                        estimate_download_bytes(PROVIDER_MAP["satellite"], ranges))

    def test_all_providers_work_without_keys_and_empty_plan_is_zero(self):
        for provider in MAP_PROVIDERS:
            self.assertEqual(estimate_download_bytes(provider, ()), 0)
            for zoom in (1, 10, provider.max_zoom):
                estimate = estimate_download_bytes(provider, (TileRange(zoom, 0, 0, 0, 0),))
                self.assertGreater(estimate, 0)
                self.assertLess(estimate, 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
