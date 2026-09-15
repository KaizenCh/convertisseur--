import unittest
from ue2godot.orchestrator import operations as ops


class TestRegistry(unittest.TestCase):
    def test_every_operation_has_unique_key(self):
        keys = [o.key for o in ops.OPERATIONS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_workflow_references_existing_operations(self):
        for name, spec in ops.WORKFLOWS.items():
            for key in spec["operations"]:
                self.assertIn(key, ops.BY_KEY, f"{name} référence '{key}' inconnu")

    def test_every_required_artifact_is_produced_by_someone(self):
        produced = {a for o in ops.OPERATIONS for a in o.produces}
        for op in ops.OPERATIONS:
            for need in op.requires:
                self.assertIn(need, produced,
                              f"'{need}' requis par {op.key} mais produit par personne")

    def test_unreal_side_operations_declare_a_step_name(self):
        for op in ops.OPERATIONS:
            if op.side == ops.SIDE_UNREAL:
                self.assertIsNotNone(op.step_name, f"{op.key} sans step_name")

    def test_declared_steps_exist_in_entry_registry(self):
        from ue2godot.ue.entry import STEPS
        for op in ops.OPERATIONS:
            if op.step_name:
                self.assertIn(op.step_name, STEPS,
                              f"{op.key} pointe vers une étape inexistante")


class TestSelectionValidation(unittest.TestCase):
    def test_missing_dependency_is_reported(self):
        warnings = ops.validate_selection(["meshes"], [])
        self.assertTrue(any("Scanner la map" in w for w in warnings))

    def test_dependency_satisfied_by_existing_artifact(self):
        warnings = ops.validate_selection(["meshes"], [ops.ART_MANIFEST])
        self.assertEqual(warnings, [])

    def test_dependency_satisfied_by_earlier_selection(self):
        warnings = ops.validate_selection(["manifest", "meshes"], [])
        self.assertEqual(warnings, [])

    def test_rewrite_after_append_is_flagged(self):
        # landscape ajoute au manifeste ; relancer manifest ensuite l'efface.
        warnings = ops.validate_selection(
            ["landscape", "manifest"], [ops.ART_MANIFEST, ops.ART_ASSET_MAP]
        )
        self.assertTrue(any("effacera" in w for w in warnings))

    def test_correct_order_is_not_flagged_as_destructive(self):
        warnings = ops.validate_selection(["manifest", "meshes", "landscape"], [])
        self.assertFalse(any("effacera" in w for w in warnings))

    def test_full_workflow_is_self_consistent(self):
        warnings = ops.validate_selection(
            ops.WORKFLOWS["full_reconstruction"]["operations"], []
        )
        self.assertEqual(warnings, [], f"workflow complet incohérent : {warnings}")

    def test_all_workflows_are_self_consistent(self):
        for name, spec in ops.WORKFLOWS.items():
            # Les workflows partiels supposent des artefacts déjà produits ;
            # on leur fournit tout sauf ce qu'ils produisent eux-mêmes.
            produced_by_wf = {a for k in spec["operations"]
                              for a in ops.BY_KEY[k].produces}
            everything = [a for o in ops.OPERATIONS for a in o.produces
                          if a not in produced_by_wf]
            warnings = ops.validate_selection(spec["operations"], everything)
            destructive = [w for w in warnings if "effacera" in w]
            self.assertEqual(destructive, [],
                             f"workflow '{name}' a un ordre destructif : {destructive}")

    def test_unknown_key_is_ignored_not_crashed(self):
        self.assertEqual(ops.validate_selection(["does_not_exist"], []), [])


if __name__ == "__main__":
    unittest.main()
