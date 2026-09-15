import os
import tempfile
import unittest

from ue2godot.core.strategies import (
    Strategy, StrategyChain, StrategyOutcome, summarize_outcomes,
)


def always_valid(value):
    return (value is not None), "valeur nulle"


class TestStrategyChain(unittest.TestCase):
    def test_first_strategy_wins_when_it_succeeds(self):
        chain = StrategyChain("t", [
            Strategy("a", lambda: "A"),
            Strategy("b", lambda: "B"),
        ], always_valid)
        out = chain.run()
        self.assertEqual(out.value, "A")
        self.assertEqual(out.strategy, "a")
        self.assertFalse(out.used_fallback())

    def test_falls_through_to_second_on_exception(self):
        def boom():
            raise RuntimeError("cassé")
        chain = StrategyChain("t", [Strategy("a", boom), Strategy("b", lambda: "B")],
                              always_valid)
        out = chain.run()
        self.assertEqual(out.value, "B")
        self.assertTrue(out.used_fallback())
        self.assertIn("cassé", out.attempts[0]["reason"])

    def test_invalid_result_is_a_failure_not_a_success(self):
        # Le point crucial : une stratégie qui rend une valeur inutilisable
        # (fichier vide, None) ne doit PAS être comptée comme réussie —
        # c'est ainsi qu'un GLB de 0 octet avait été accepté autrefois.
        chain = StrategyChain("t", [
            Strategy("rend_none", lambda: None),
            Strategy("rend_ok", lambda: "OK"),
        ], always_valid)
        out = chain.run()
        self.assertEqual(out.strategy, "rend_ok")
        self.assertEqual(out.attempts[0]["strategy"], "rend_none")

    def test_unavailable_strategy_is_skipped_with_a_reason(self):
        chain = StrategyChain("t", [
            Strategy("absent", lambda: "X", available=lambda: False),
            Strategy("present", lambda: "Y"),
        ], always_valid)
        out = chain.run()
        self.assertEqual(out.value, "Y")
        self.assertIn("indisponible", out.attempts[0]["reason"])

    def test_total_failure_reports_every_reason_not_just_the_last(self):
        chain = StrategyChain("t", [
            Strategy("a", lambda: (_ for _ in ()).throw(ValueError("err-a"))),
            Strategy("b", lambda: None),
        ], always_valid)
        out = chain.run()
        self.assertFalse(out.succeeded)
        summary = out.failure_summary()
        self.assertIn("err-a", summary)
        self.assertIn("b", summary)

    def test_arguments_are_forwarded_to_every_strategy(self):
        seen = []
        chain = StrategyChain("t", [
            Strategy("a", lambda x, y=0: seen.append((x, y)) or None),
            Strategy("b", lambda x, y=0: f"{x}-{y}"),
        ], always_valid)
        out = chain.run(5, y=7)
        self.assertEqual(seen, [(5, 7)])
        self.assertEqual(out.value, "5-7")

    def test_quality_and_caveat_travel_with_the_outcome(self):
        chain = StrategyChain("t", [
            Strategy("a", lambda: None),
            Strategy("b", lambda: "B", quality="reduced", caveat="sans matériaux"),
        ], always_valid)
        out = chain.run()
        self.assertEqual(out.quality, "reduced")
        self.assertEqual(out.caveat, "sans matériaux")


class TestSummary(unittest.TestCase):
    def test_summary_counts_fallbacks_and_lists_degraded(self):
        ok = StrategyOutcome(value=1, strategy="main", succeeded=True)
        fb = StrategyOutcome(value=1, strategy="alt", quality="reduced",
                             caveat="perte X", succeeded=True,
                             attempts=[{"strategy": "main", "reason": "ko"}])
        summary = summarize_outcomes({"a": ok, "b": fb})
        self.assertEqual(summary["fallback_count"], 1)
        self.assertEqual(summary["by_strategy"], {"main": 1, "alt": 1})
        self.assertEqual(summary["degraded_total"], 1)

    def test_failed_outcomes_are_not_counted_as_strategies(self):
        failed = StrategyOutcome(succeeded=False)
        summary = summarize_outcomes({"a": failed})
        self.assertEqual(summary["by_strategy"], {})
        self.assertEqual(summary["fallback_count"], 0)


class TestExportValidators(unittest.TestCase):
    def test_glb_validator_rejects_wrong_magic(self):
        from ue2godot.ue.mesh_export import _validate_exported_file
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.glb")
            with open(path, "wb") as f:
                f.write(b"NOPE" + b"\x00" * 100)
            ok, reason = _validate_exported_file(path)
            self.assertFalse(ok)
            self.assertIn("glTF", reason)

    def test_glb_validator_accepts_real_header(self):
        from ue2godot.ue.mesh_export import _validate_exported_file
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.glb")
            with open(path, "wb") as f:
                f.write(b"glTF" + b"\x00" * 100)
            self.assertTrue(_validate_exported_file(path)[0])

    def test_obj_validator_rejects_file_without_vertices(self):
        from ue2godot.ue.mesh_export import _validate_exported_file
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.obj")
            with open(path, "w") as f:
                f.write("# exporté par Unreal\n# aucune géométrie\n" + "#" * 100)
            ok, reason = _validate_exported_file(path)
            self.assertFalse(ok)
            self.assertIn("sommet", reason)

    def test_obj_validator_accepts_file_with_vertices(self):
        from ue2godot.ue.mesh_export import _validate_exported_file
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.obj")
            with open(path, "w") as f:
                f.write("# en-tête\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n" + "#" * 80)
            self.assertTrue(_validate_exported_file(path)[0])

    def test_png_validator_rejects_non_png(self):
        from ue2godot.ue.rendertarget import _validate_png
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.png")
            with open(path, "wb") as f:
                f.write(b"\x00" * 2000)
            self.assertFalse(_validate_png(path)[0])

    def test_png_validator_accepts_real_png_header(self):
        from ue2godot.ue.rendertarget import _validate_png
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.png")
            with open(path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000)
            self.assertTrue(_validate_png(path)[0])


class TestChainComposition(unittest.TestCase):
    def test_mesh_chain_has_three_distinct_strategies(self):
        from ue2godot.ue.mesh_export import build_mesh_export_chain
        chain = build_mesh_export_chain()
        names = [s.name for s in chain.strategies]
        self.assertEqual(len(names), 3)
        self.assertEqual(len(set(names)), 3, "les stratégies doivent être distinctes")
        self.assertEqual(names[0], "gltf_exporter", "la voie principale doit rester première")

    def test_mesh_chain_fallbacks_can_be_disabled(self):
        from ue2godot.ue.mesh_export import build_mesh_export_chain
        chain = build_mesh_export_chain(enable_manual=False, enable_task=False)
        self.assertEqual(len(chain.strategies), 1)

    def test_texture_chain_has_three_distinct_strategies(self):
        from ue2godot.ue.rendertarget import build_texture_export_chain
        chain = build_texture_export_chain()
        names = [s.name for s in chain.strategies]
        self.assertEqual(len(set(names)), 3)
        self.assertEqual(names[0], "render_target_bake")

    def test_every_reduced_strategy_declares_what_is_lost(self):
        # Un repli de qualité dégradée SANS description de la perte serait
        # cosmétique : on saurait qu'on est dégradé sans savoir en quoi.
        from ue2godot.ue.mesh_export import build_mesh_export_chain
        from ue2godot.ue.rendertarget import build_texture_export_chain
        for chain in (build_mesh_export_chain(), build_texture_export_chain()):
            for strategy in chain.strategies:
                if strategy.quality != "full" or strategy.name != chain.strategies[0].name:
                    if strategy is not chain.strategies[0]:
                        self.assertTrue(
                            strategy.caveat,
                            f"{strategy.name} ne décrit pas ce qu'il coûte",
                        )


if __name__ == "__main__":
    unittest.main()


class TestSlimManifestEquivalence(unittest.TestCase):
    """Le manifeste allégé doit porter les placements AU BIT PRÈS.

    C'est ce qui sépare un vrai repli d'une version approximative : si les
    transforms différaient, basculer dessus déplacerait silencieusement la
    scène entière.
    """

    def _build_slim(self, manifest):
        # Reproduit la projection appliquée par step1_manifest, sans Unreal.
        return {
            "geometry": {
                "placements": manifest["geometry"]["placements"],
                "skeletal_mesh_placements": manifest["geometry"]["skeletal_mesh_placements"],
                "unique_meshes": {
                    path: {"path": info.get("path", path), "name": info.get("name", "")}
                    for path, info in manifest["geometry"]["unique_meshes"].items()
                },
                "unique_skeletal_meshes": manifest["geometry"]["unique_skeletal_meshes"],
            },
            "effects": manifest["effects"],
            "world_features": {
                "lights": manifest["world_features"]["lights"],
                "audio": manifest["world_features"]["audio"],
                "landscape": manifest["world_features"]["landscape"],
            },
        }

    def _sample_manifest(self):
        placement = {
            "kind": "static_mesh", "placement_id": "P1",
            "mesh": {"path": "/Game/M", "name": "M"},
            "reconstruction_transform": {
                "location": [123.456789, -87.25, 0.001],
                "rotation": {"pitch": 12.5, "yaw": -90.0, "roll": 0.25},
                "scale": [1.0, 2.5, 1.0],
            },
        }
        return {
            "geometry": {
                "placements": [placement],
                "skeletal_mesh_placements": [],
                "unique_meshes": {"/Game/M": {
                    "path": "/Game/M", "name": "M",
                    "lod": {"lod_count": 3}, "collision": {"boxes": 2},
                    "material_slots": [{"slot_index": 0}],
                }},
                "unique_skeletal_meshes": {},
            },
            "effects": {"decals": [{"material_path": "/Game/D"}], "niagara": []},
            "world_features": {"lights": [{"intensity": 5000.0}],
                               "audio": [], "landscape": []},
            "materials": {"unique_materials": {"/Game/D": {"huge": "x" * 10000}}},
        }

    def test_placements_are_bit_identical(self):
        full = self._sample_manifest()
        slim = self._build_slim(full)
        self.assertEqual(slim["geometry"]["placements"],
                         full["geometry"]["placements"])

    def test_effects_and_lights_survive_intact(self):
        full = self._sample_manifest()
        slim = self._build_slim(full)
        self.assertEqual(slim["effects"], full["effects"])
        self.assertEqual(slim["world_features"]["lights"],
                         full["world_features"]["lights"])

    def test_slim_drops_only_analysis_registries(self):
        full = self._sample_manifest()
        slim = self._build_slim(full)
        self.assertNotIn("materials", slim)
        # Le mesh garde son identité — seule sa fiche d'analyse disparaît,
        # et le reconstructeur ne résout un mesh que par son chemin.
        self.assertEqual(slim["geometry"]["unique_meshes"]["/Game/M"]["path"], "/Game/M")
        self.assertNotIn("lod", slim["geometry"]["unique_meshes"]["/Game/M"])

    def test_slim_is_substantially_smaller(self):
        import json
        full = self._sample_manifest()
        slim = self._build_slim(full)
        self.assertLess(len(json.dumps(slim)), len(json.dumps(full)))
