import unittest
from ue2godot.ue.landscape_material_config import (
    _looks_like_landscape_material_folder,
    _umap_filesystem_path_to_game_folder,
    _normalize,
)


class TestFolderNameMatching(unittest.TestCase):
    def test_matches_various_separators_and_casing(self):
        for name in ("LandscapeMaterials", "Landscape_Material", "landscape__material",
                     "Landscape-Materials", "LANDSCAPE MATERIAL", "Landscape.Material"):
            self.assertTrue(_looks_like_landscape_material_folder(name), name)

    def test_rejects_unrelated_or_partial_names(self):
        for name in ("Materials", "Landscape", "Props", "LandscapeProps", "MaterialInstances"):
            self.assertFalse(_looks_like_landscape_material_folder(name), name)


class TestUmapPathToGameFolder(unittest.TestCase):
    def test_nested_map_under_content(self):
        self.assertEqual(
            _umap_filesystem_path_to_game_folder(
                "D:/MyProject/Content/Maps/Necropolis/Necropolis.umap"
            ),
            "/Game/Maps/Necropolis",
        )

    def test_map_directly_under_content_root(self):
        self.assertEqual(
            _umap_filesystem_path_to_game_folder("D:/MyProject/Content/Necropolis.umap"),
            "/Game",
        )

    def test_case_insensitive_content_marker(self):
        # Windows paths are case-insensitive; a lowercase "content" segment
        # (or any other casing) must still be recognised — this used to
        # fail silently (comparing a lowercased haystack against a
        # mixed-case needle) and return None for every real-world path.
        self.assertEqual(
            _umap_filesystem_path_to_game_folder("d:/proj/content/Maps/X.umap"),
            "/Game/Maps",
        )

    def test_no_content_marker_returns_none(self):
        self.assertIsNone(_umap_filesystem_path_to_game_folder("D:/random/X.umap"))

    def test_empty_input_returns_none(self):
        self.assertIsNone(_umap_filesystem_path_to_game_folder(""))
        self.assertIsNone(_umap_filesystem_path_to_game_folder(None))


if __name__ == "__main__":
    unittest.main()
