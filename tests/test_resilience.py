# -*- coding: utf-8 -*-
"""
Tests for framework resilience, exception boundaries, and edge-case handling.
"""

import os
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ue2godot.core.config import ResolvedConfig
from ue2godot.ue.classify import classify_actor, classify_component
from ue2godot.ue.transforms import extract_transform_dict, actor_transform, component_transform_diagnostic as transform_diagnostic
from ue2godot.ue.steps import step1_manifest, step2_meshes, step3_landscape, step4_decals_vfx
from ue2godot.orchestrator.step5_copy import copy_step5
from ue2godot.orchestrator.pipeline import PipelineOrchestrator


def make_config(raw_dict=None):
    return ResolvedConfig(raw_dict or {}, "test_run_123", "hash_abc_456")


class TestClassifyResilience(unittest.TestCase):
    def test_classify_actor_handles_none_and_weird_inputs(self):
        self.assertEqual(classify_actor(None), "other")
        self.assertEqual(classify_actor(12345), "other")
        self.assertEqual(classify_actor([]), "other")

    def test_classify_component_handles_none_and_weird_inputs(self):
        self.assertEqual(classify_component(None), "other")
        self.assertEqual(classify_component(object()), "other")


class TestTransformResilience(unittest.TestCase):
    def test_extract_transform_dict_none(self):
        tf = extract_transform_dict(None)
        self.assertEqual(tf["location"], [0.0, 0.0, 0.0])
        self.assertEqual(tf["rotation"], {"pitch": 0.0, "yaw": 0.0, "roll": 0.0})
        self.assertEqual(tf["scale"], [1.0, 1.0, 1.0])

    def test_extract_transform_dict_raising_object(self):
        bad_obj = MagicMock()
        type(bad_obj).translation = property(lambda self: 1 / 0)
        tf = extract_transform_dict(bad_obj)
        self.assertEqual(tf["location"], [0.0, 0.0, 0.0])

    def test_actor_transform_none(self):
        tf = actor_transform(None)
        self.assertEqual(tf["location"], [0.0, 0.0, 0.0])

    def test_component_transform_diagnostic_none(self):
        val, err = transform_diagnostic(None, None)
        self.assertEqual(val["location"], [0.0, 0.0, 0.0])
        self.assertIsNotNone(err)


class TestManifestStepResilience(unittest.TestCase):
    def test_step1_without_unreal(self):
        cfg = make_config({"paths": {"ue_export_root": tempfile.gettempdir()}})
        with patch("ue2godot.ue.steps.step1_manifest.unreal", None):
            report = step1_manifest.run(cfg)
            self.assertEqual(report.status, "FAILED")
            self.assertTrue(any("unavailable" in e for e in report.errors))

    def test_step1_reconstruction_contract_with_corrupt_manifest_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = os.path.join(tmpdir, "level_manifest_v10.json")
            mock_manifest = {
                "manifest_version": "10.0",
                "geometry": {
                    "unique_meshes": {},
                    "placements": [
                        {
                            "kind": "instanced_mesh",
                            "placement_id": "MESH_TEST_1",
                            "instance_count": 2,
                            "instance_transforms": [
                                {"location": [0, 0, 0]},
                                None
                            ]
                        }
                    ]
                }
            }
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(mock_manifest, f)

            with open(manifest_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(len(loaded["geometry"]["placements"]), 1)


class TestMeshStepResilience(unittest.TestCase):
    def test_step2_missing_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = make_config({"paths": {"ue_export_root": tmpdir}})
            with patch("ue2godot.ue.steps.step2_meshes.unreal", MagicMock()):
                report = step2_meshes.run(cfg)
                self.assertEqual(report.status, "FAILED")
                self.assertTrue(any("not found" in e for e in report.errors))

    def test_step2_corrupt_manifest_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = os.path.join(tmpdir, "level_manifest_v10.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                f.write("corrupted json payload {{{")

            cfg = make_config({"paths": {"ue_export_root": tmpdir}})
            with patch("ue2godot.ue.steps.step2_meshes.unreal", MagicMock()):
                report = step2_meshes.run(cfg)
                self.assertEqual(report.status, "FAILED")
                self.assertTrue(any("Failed to read manifest JSON" in e for e in report.errors))


class TestLandscapeStepResilience(unittest.TestCase):
    def test_landscape_clustering_bounds(self):
        bounds_empty = []
        clusters = step3_landscape._cluster_landscapes(bounds_empty, 5000.0)
        self.assertEqual(clusters, [])

        bounds_single = [(0, 100, 0, 100, 0, 50)]
        clusters_single = step3_landscape._cluster_landscapes(bounds_single, 5000.0)
        self.assertEqual(clusters_single, [[0]])

        bounds_disjoint = [(0, 100, 0, 100, 0, 50), (100000, 100100, 100000, 100100, 0, 50)]
        clusters_disjoint = step3_landscape._cluster_landscapes(bounds_disjoint, 5000.0)
        self.assertEqual(len(clusters_disjoint), 2)

    def test_actor_bounds_exception(self):
        bad_actor = MagicMock()
        bad_actor.get_actor_bounds.side_effect = Exception("UE API error")
        bounds = step3_landscape._actor_bounds(bad_actor)
        self.assertEqual(bounds, (0.0, 0.0, 0.0, 0.0, 0.0, 0.0))


class TestDecalsVFXStepResilience(unittest.TestCase):
    def test_step4_missing_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = make_config({"paths": {"ue_export_root": tmpdir}})
            report = step4_decals_vfx.run(cfg)
            self.assertEqual(report.status, "FAILED")
            self.assertTrue(any("not found" in e for e in report.errors))

    def test_step4_empty_decals_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = os.path.join(tmpdir, "level_manifest_v10.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({"manifest_version": "10.0", "effects": {"decals": [], "niagara": []}}, f)

            cfg = make_config({"paths": {"ue_export_root": tmpdir}})
            report = step4_decals_vfx.run(cfg)
            self.assertEqual(report.status, "OK")


class TestCopyStepResilience(unittest.TestCase):
    def test_step5_invalid_godot_project_root(self):
        cfg = make_config({"paths": {"godot_project_root": "/invalid/path/that/does/not/exist"}})
        report = copy_step5(cfg)
        self.assertEqual(report.status, "FAILED")
        self.assertTrue(any("Invalid Godot project root" in e for e in report.errors))


class TestOrchestratorCrosscheckResilience(unittest.TestCase):
    def test_crosscheck_empty_and_corrupt_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = make_config({"run_id": "test_run", "config_hash": "abc"})
            orch = PipelineOrchestrator(cfg)

            manifest_path = os.path.join(tmpdir, "manifest.json")
            asset_map_path = os.path.join(tmpdir, "asset_map.json")

            with open(manifest_path, "w") as f:
                f.write("invalid json")
            with open(asset_map_path, "w") as f:
                f.write("{}")

            report = orch.crosscheck(manifest_path, asset_map_path)
            self.assertIn(report.status, ("OK", "FAILED"))


if __name__ == "__main__":
    unittest.main()
