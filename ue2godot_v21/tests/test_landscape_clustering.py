import unittest
from ue2godot.ue.steps.step3_landscape import _cluster_landscapes, _bounds_gap


def box(min_x, max_x, min_y, max_y):
    return (min_x, max_x, min_y, max_y, 0.0, 100.0)


class TestBoundsGap(unittest.TestCase):
    def test_overlapping_boxes_have_zero_gap(self):
        a = box(0, 100, 0, 100)
        b = box(50, 150, 50, 150)
        self.assertEqual(_bounds_gap(a, b), 0.0)

    def test_touching_boxes_have_zero_gap(self):
        a = box(0, 100, 0, 100)
        b = box(100, 200, 0, 100)
        self.assertEqual(_bounds_gap(a, b), 0.0)

    def test_separated_boxes_measure_straight_line_gap(self):
        a = box(0, 100, 0, 100)
        b = box(200, 300, 0, 100)
        self.assertAlmostEqual(_bounds_gap(a, b), 100.0)


class TestClustering(unittest.TestCase):
    def test_streaming_proxies_tile_into_one_cluster(self):
        # 4 adjacent tiles, edge-to-edge (0-gap), like Landscape streaming
        # proxies of a single terrain.
        bounds = [
            box(0, 100, 0, 100), box(100, 200, 0, 100),
            box(0, 100, 100, 200), box(100, 200, 100, 200),
        ]
        clusters = _cluster_landscapes(bounds, gap_threshold_cm=1.0)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(sorted(clusters[0]), [0, 1, 2, 3])

    def test_two_distant_islands_become_two_clusters(self):
        bounds = [
            box(0, 100, 0, 100),          # island A
            box(100000, 100100, 0, 100),  # island B, far away
        ]
        clusters = _cluster_landscapes(bounds, gap_threshold_cm=5000.0)
        self.assertEqual(len(clusters), 2)

    def test_transitive_chain_joins_into_one_cluster(self):
        # A-B close, B-C close, but A-C alone would exceed the threshold —
        # must still end up as ONE cluster via B.
        bounds = [
            box(0, 100, 0, 100),      # A
            box(140, 240, 0, 100),    # B: gap to A = 40
            box(280, 380, 0, 100),    # C: gap to B = 40, gap to A = 180
        ]
        clusters = _cluster_landscapes(bounds, gap_threshold_cm=50.0)
        self.assertEqual(len(clusters), 1)

    def test_single_landscape_is_its_own_cluster(self):
        clusters = _cluster_landscapes([box(0, 100, 0, 100)], gap_threshold_cm=5000.0)
        self.assertEqual(clusters, [[0]])

    def test_empty_input(self):
        self.assertEqual(_cluster_landscapes([], gap_threshold_cm=5000.0), [])


if __name__ == "__main__":
    unittest.main()
