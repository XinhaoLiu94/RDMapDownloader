"""瓦片数学单元测试。"""

from __future__ import annotations

import math
import unittest

from rdmap import tile_math as tm


class TileMathTests(unittest.TestCase):
    def test_known_tiles(self) -> None:
        # 广州塔附近；与 GMap.NET 的投影公式一致
        self.assertEqual(tm.latlng_to_tile(23.32, 113.55, 10), (834, 443))
        self.assertEqual(tm.latlng_to_tile(23.32, 113.55, 12), (3339, 1775))
        self.assertEqual(tm.latlng_to_tile(23.32, 113.55, 8), (208, 110))

    def test_range_from_default_bounds(self) -> None:
        tile_range = tm.range_from_bounds(23.60, 23.10, 113.30, 113.90, 10)
        self.assertEqual(tile_range.min_row, 442)
        self.assertEqual(tile_range.max_row, 444)
        self.assertEqual(tile_range.min_col, 834)
        self.assertEqual(tile_range.max_col, 835)
        self.assertEqual(tile_range.count, 6)
        self.assertTrue(tile_range.is_within_world)

    def test_pixel_roundtrip(self) -> None:
        for lat, lng in ((0.0, 0.0), (23.32, 113.55), (-45.5, -179.2), (85.0, 179.9)):
            x, y = tm.latlng_to_pixel(lat, lng, 14)
            back_lat, back_lng = tm.pixel_to_latlng(x, y, 14)
            self.assertAlmostEqual(lat, back_lat, places=9)
            self.assertAlmostEqual(lng, back_lng, places=9)

    def test_world_size_and_count(self) -> None:
        self.assertEqual(tm.tile_count(0), 1)
        self.assertEqual(tm.world_size(0), 256)
        self.assertEqual(tm.tile_count(10), 1024)
        self.assertEqual(tm.world_size(10), 262144)

    def test_tile_clamped_to_world(self) -> None:
        count = tm.tile_count(3)
        self.assertEqual(tm.latlng_to_tile(89.0, 180.0, 3), (count - 1, 0))
        self.assertEqual(tm.latlng_to_tile(-89.0, -180.0, 3), (0, count - 1))

    def test_range_at_world_edges(self) -> None:
        tile_range = tm.range_from_bounds(85.0, -85.0, -180.0, 180.0, 2)
        self.assertEqual((tile_range.min_col, tile_range.max_col), (0, 3))
        self.assertEqual((tile_range.min_row, tile_range.max_row), (0, 3))
        self.assertEqual(tile_range.count, 16)

    def test_range_swaps_inverted_values(self) -> None:
        normal = tm.range_from_bounds(23.6, 23.1, 113.3, 113.9, 10)
        inverted = tm.range_from_bounds(23.1, 23.6, 113.9, 113.3, 10)
        self.assertEqual(normal, inverted)

    def test_invalid_rows_raise(self) -> None:
        with self.assertRaises(ValueError):
            tm.range_from_rows_cols(10, 5, 4, 0, 1)
        with self.assertRaises(ValueError):
            tm.TileRange(zoom=10, min_row=0, max_row=1, min_col=0, max_col=-1)

    def test_manual_range_out_of_world(self) -> None:
        tile_range = tm.range_from_rows_cols(2, 0, 1, 0, 4)
        self.assertFalse(tile_range.is_within_world)
        self.assertEqual(tile_range.count, 10)

    def test_normalize_longitude(self) -> None:
        self.assertAlmostEqual(tm.normalize_longitude(180.0), -180.0)
        self.assertAlmostEqual(tm.normalize_longitude(190.0), -170.0)
        self.assertAlmostEqual(tm.normalize_longitude(-190.0), 170.0)

    def test_visible_bounds_contains_center(self) -> None:
        north, south, west, east = tm.visible_bounds(23.32, 113.55, 12, 1000, 800)
        self.assertGreater(north, 23.32)
        self.assertLess(south, 23.32)
        self.assertLess(west, 113.55)
        self.assertGreater(east, 113.55)

    def test_zoom_to_fit(self) -> None:
        zoom = tm.zoom_to_fit(23.60, 23.10, 113.30, 113.90, 1000, 800)
        self.assertGreaterEqual(zoom, 10)
        self.assertLessEqual(zoom, 20)

    def test_tile_center(self) -> None:
        lat, lng = tm.tile_center_latlng(834, 443, 10)
        self.assertTrue(23.0 < lat < 23.7)
        self.assertTrue(113.2 < lng < 114.0)

    def test_finite_validation(self) -> None:
        with self.assertRaises(ValueError):
            tm.range_from_bounds(math.nan, 0, 0, 1, 10)
        with self.assertRaises(ValueError):
            tm.range_from_bounds(0, 0, 0, math.inf, 10)


if __name__ == "__main__":
    unittest.main()
