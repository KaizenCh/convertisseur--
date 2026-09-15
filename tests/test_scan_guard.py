import unittest
from ue2godot.ue import scan_guard
from ue2godot.ue.scan_guard import (
    ScanGuardError, package_path_from_filesystem, GuardResult,
)


class TestPackagePathDerivation(unittest.TestCase):
    def test_derives_game_path_from_umap(self):
        self.assertEqual(
            package_path_from_filesystem(
                r"D:\MyProject\Content\Maps\Necropolis\m1.umap"),
            "/Game/Maps/Necropolis/m1")

    def test_case_insensitive_content_marker(self):
        self.assertEqual(
            package_path_from_filesystem("d:/proj/content/Maps/m1.umap"),
            "/Game/Maps/m1")

    def test_refuses_to_guess_from_a_directory(self):
        # Un dossier n'est pas une map : fabriquer un chemin package
        # plausible ferait charger la MAUVAISE map. Mieux vaut rendre vide
        # et laisser le contrôle de map désactivé.
        self.assertEqual(package_path_from_filesystem("D:/proj/Content/Maps"), "")

    def test_refuses_path_without_content_segment(self):
        self.assertEqual(package_path_from_filesystem("D:/ailleurs/m1.umap"), "")

    def test_empty_input(self):
        self.assertEqual(package_path_from_filesystem(""), "")
        self.assertEqual(package_path_from_filesystem(None), "")


class TestReadinessValidation(unittest.TestCase):
    """La validation doit LEVER, pas avertir : un avertissement n'empêche
    pas l'écriture, et c'est l'écriture qu'il faut empêcher."""

    def setUp(self):
        self._orig_current = scan_guard.current_map_name
        self._orig_unreal = scan_guard.unreal

    def tearDown(self):
        scan_guard.current_map_name = self._orig_current
        scan_guard.unreal = self._orig_unreal

    def _fake_unreal(self, actor_count):
        class Subsystem:
            def get_all_level_actors(self):
                return list(range(actor_count))

        class FakeUnreal:
            EditorActorSubsystem = object
            @staticmethod
            def get_editor_subsystem(_cls):
                return Subsystem()
        return FakeUnreal()

    def test_raises_when_actor_count_below_threshold(self):
        scan_guard.current_map_name = lambda: "/Game/Maps/Default"
        scan_guard.unreal = self._fake_unreal(2)
        with self.assertRaises(ScanGuardError) as ctx:
            scan_guard.validate_scan_readiness(min_actors=20)
        message = str(ctx.exception)
        self.assertIn("2 acteur", message)
        self.assertIn("AUCUN manifeste", message)

    def test_passes_and_returns_count_when_plausible(self):
        scan_guard.current_map_name = lambda: "/Game/Maps/m1"
        scan_guard.unreal = self._fake_unreal(5929)
        self.assertEqual(scan_guard.validate_scan_readiness(min_actors=20), 5929)

    def test_raises_when_active_map_is_not_the_target(self):
        scan_guard.current_map_name = lambda: "/Game/Maps/AutreMap"
        scan_guard.unreal = self._fake_unreal(5000)
        with self.assertRaises(ScanGuardError) as ctx:
            scan_guard.validate_scan_readiness(
                min_actors=20, expected_map_substring="/Game/Maps/m1")
        self.assertIn("manifeste vide", str(ctx.exception))

    def test_map_check_is_skipped_when_no_target_given(self):
        # Sans chemin package connu, le contrôle de map est désactivé mais
        # le seuil d'acteurs reste actif : un niveau vide est suspect dans
        # tous les cas.
        scan_guard.current_map_name = lambda: "/Game/Maps/Quelconque"
        scan_guard.unreal = self._fake_unreal(100)
        self.assertEqual(
            scan_guard.validate_scan_readiness(min_actors=20, expected_map_substring=""),
            100)

    def test_notes_record_what_was_observed(self):
        scan_guard.current_map_name = lambda: "/Game/Maps/m1"
        scan_guard.unreal = self._fake_unreal(42)
        notes = []
        scan_guard.validate_scan_readiness(min_actors=10, notes=notes)
        self.assertTrue(any("42 acteur" in n for n in notes))


class TestGuardResult(unittest.TestCase):
    def test_result_carries_what_the_report_needs(self):
        result = GuardResult(actor_count=5929, current_map="/Game/Maps/m1",
                             target_map="/Game/Maps/m1", map_was_loaded=True)
        self.assertEqual(result.actor_count, 5929)
        self.assertTrue(result.map_was_loaded)
        self.assertEqual(result.notes, [])


if __name__ == "__main__":
    unittest.main()
