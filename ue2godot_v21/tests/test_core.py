import unittest
from ue2godot.core.result import Result
from ue2godot.core.ids import stable_id, unique_filename_for_path
from ue2godot.core.config import ResolvedConfig, deep_merge, compute_config_hash
from ue2godot.core.registry import AssetRegistry

class TestCore(unittest.TestCase):
    def test_result(self):
        r = Result.ok(10)
        self.assertTrue(r.is_ok)
        self.assertEqual(r.value, 10)

        err = Result.err("failed")
        self.assertTrue(err.is_err)
        self.assertEqual(err.error, "failed")

    def test_ids(self):
        sid = stable_id("MESH", "/Game/TestMesh")
        self.assertTrue(sid.startswith("MESH_"))

        fn = unique_filename_for_path("/Game/TestMesh.TestMesh")
        self.assertTrue(fn.endswith(".glb"))

    def test_config(self):
        base = {"a": 1, "b": {"c": 2}}
        override = {"b": {"c": 3, "d": 4}}
        merged = deep_merge(base, override)
        self.assertEqual(merged["b"]["c"], 3)
        self.assertEqual(merged["b"]["d"], 4)

        res = ResolvedConfig.resolve(base, override, {})
        self.assertIsNotNone(res.config_hash)

if __name__ == "__main__":
    unittest.main()


class TestClassify(unittest.TestCase):
    def test_default_rules_absent_falls_back(self):
        from ue2godot.ue.classify import classify_actor, classify_component
        # No `unreal` objects available outside the editor, so these
        # exercise only the pure-Python fallback path via a stub.
        class Stub:
            def __init__(self, name):
                self._name = name
            def get_class(self):
                class C:
                    def __init__(self, n):
                        self._n = n
                    def get_name(self):
                        return self._n
                return C(self._name)

        self.assertEqual(classify_actor(Stub("BP_LevelInstance_C")), "level_instance")
        self.assertEqual(classify_component(Stub("DecalComponent")), "decal")

    def test_external_rules_take_priority_and_respect_order(self):
        from ue2godot.ue.classify import classify_actor
        class Stub:
            def get_class(self):
                class C:
                    def get_name(self):
                        return "NS_candle_flame_Actor"
                return C()
        rules = [
            {"match": "candle", "category": "candle_fx"},
            {"match": "flame", "category": "generic_fx"},
        ]
        # "candle" must win even though "flame" also matches — rule order,
        # most specific first (§13.B.16).
        self.assertEqual(classify_actor(Stub(), rules), "candle_fx")


class TestPngCodecRoundtrip(unittest.TestCase):
    def test_compose_tinted_rgba_roundtrip(self):
        from ue2godot.core import png_codec
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            mask_path = os.path.join(d, "mask.png")
            out_path = os.path.join(d, "out.png")
            png_codec.write_rgba_png(mask_path, 1, 1, bytes([200, 200, 200, 128]))
            w, h, mean, lo, hi = png_codec.compose_tinted_rgba(mask_path, out_path, [1.0, 0.0, 0.0])
            self.assertEqual((w, h), (1, 1))
            r_w, r_h, channels, samples = png_codec.read_png(out_path)
            self.assertEqual((r_w, r_h, channels), (1, 1, 4))
            # Tint is pure red -> composed pixel should be (255, 0, 0, alpha)
            self.assertEqual(samples[0], 255)
            self.assertEqual(samples[1], 0)
            self.assertEqual(samples[2], 0)
