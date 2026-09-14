import math
import unittest
from ue2godot.core.glb import build_grid_mesh, write_glb
from ue2godot.core.axis import convert_position_ue_to_godot


class TestBuildGridMesh(unittest.TestCase):
    def _flat_heights(self, side, z=0.0):
        return [z] * (side * side)

    def test_flat_grid_produces_expected_vertex_and_triangle_count(self):
        side = 5  # resolution=4 -> 5x5 verts, 4x4 quads = 32 triangles
        heights = self._flat_heights(side)
        positions, normals, uvs, indices = build_grid_mesh(
            heights, side, 0.0, 400.0, 0.0, 400.0, convert_position_ue_to_godot
        )
        self.assertEqual(len(positions) // 3, side * side)
        self.assertEqual(len(uvs) // 2, side * side)
        self.assertEqual(len(indices) // 3, 4 * 4 * 2)

    def test_hole_is_not_filled(self):
        side = 3
        heights = [0.0] * (side * side)
        heights[1 * side + 1] = None  # centre point untraced -> a hole
        positions, normals, uvs, indices = build_grid_mesh(
            heights, side, 0.0, 200.0, 0.0, 200.0, convert_position_ue_to_godot
        )
        # 8 vertices instead of 9 - the hole never became a vertex.
        self.assertEqual(len(positions) // 3, 8)
        # No quad touching the missing centre point can be emitted: all 4
        # quads in a 2x2 grid touch the centre, so zero triangles survive.
        self.assertEqual(len(indices), 0)

    def test_flat_terrain_normals_point_up_in_godot_space(self):
        # A perfectly flat heightfield has zero gradient everywhere, so
        # every normal should be straight up in Godot (+Y), regardless of
        # the axis remapping applied to positions.
        side = 4
        heights = self._flat_heights(side, z=123.4)
        positions, normals, uvs, indices = build_grid_mesh(
            heights, side, 0.0, 300.0, 0.0, 300.0, convert_position_ue_to_godot
        )
        for i in range(0, len(normals), 3):
            nx, ny, nz = normals[i], normals[i + 1], normals[i + 2]
            self.assertAlmostEqual(nx, 0.0, places=5)
            self.assertAlmostEqual(ny, 1.0, places=5)
            self.assertAlmostEqual(nz, 0.0, places=5)

    def test_uv_matches_orthographic_capture_convention(self):
        # u = (y - y_min) / span_y ; v = (x_max - x) / span_x — tied to the
        # capture camera framing (pitch=-90/yaw=0: screen right = +Y,
        # screen up = +X). This is what a texture baked by
        # rendertarget.bake_top_down_base_color() actually looks like, so
        # a mismatch here means the terrain texture would not line up.
        side = 2
        heights = [0.0, 0.0, 0.0, 0.0]
        positions, normals, uvs, indices = build_grid_mesh(
            heights, side, 0.0, 100.0, 0.0, 100.0, convert_position_ue_to_godot
        )
        # ix=0,iy=0 -> x=0,y=0 -> u=(0-0)/100=0, v=(100-0)/100=1
        self.assertAlmostEqual(uvs[0], 0.0, places=5)
        self.assertAlmostEqual(uvs[1], 1.0, places=5)
        # ix=1,iy=1 (last vertex written) -> x=100,y=100 -> u=1, v=0
        self.assertAlmostEqual(uvs[-2], 1.0, places=5)
        self.assertAlmostEqual(uvs[-1], 0.0, places=5)

    def test_write_glb_roundtrip_header(self):
        side = 2
        heights = [0.0, 10.0, 5.0, 15.0]
        positions, normals, uvs, indices = build_grid_mesh(
            heights, side, 0.0, 100.0, 0.0, 100.0, convert_position_ue_to_godot
        )
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "test.glb")
            ok = write_glb(path, positions, normals, uvs, indices, png_bytes=None, name="Test")
            self.assertTrue(ok)
            with open(path, "rb") as f:
                header = f.read(4)
            self.assertEqual(header, b"glTF")


if __name__ == "__main__":
    unittest.main()
