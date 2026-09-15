import unittest
from ue2godot.ue import components, materials, assets


class TestRecordBuilders(unittest.TestCase):
    def test_every_declared_kind_has_a_real_builder(self):
        for kind, fname in components.RECORD_BUILDERS.items():
            self.assertTrue(hasattr(components, fname),
                            f"{kind} déclare {fname}, qui n'existe pas")
            self.assertTrue(callable(getattr(components, fname)))

    def test_classify_kinds_all_have_a_builder_or_are_deliberate(self):
        # Tout kind produit par classify_component doit soit avoir une fiche,
        # soit être explicitement hors périmètre. Sans ce test, ajouter un
        # kind au classifieur le ferait capturer par le scanner sans jamais
        # être décrit — exactement le trou qu'avaient les SkeletalMesh.
        from ue2godot.ue.classify import classify_component
        deliberate = {"collision", "other"}
        produced = {"static_mesh", "skeletal_mesh", "niagara", "decal",
                    "light", "audio", "particle", "collision", "other"}
        for kind in produced - deliberate:
            self.assertIn(kind, components.RECORD_BUILDERS,
                          f"kind '{kind}' capturé mais sans fiche")

    def test_base_record_always_carries_transform_diagnostic_fields(self):
        calls = []

        def fake_transform(component, actor):
            calls.append((component, actor))
            return {"location": [0, 0, 0]}, None

        record = components._base_record(None, None, "test", fake_transform)
        self.assertEqual(len(calls), 1, "le diagnostic de transform doit être appelé")
        for key in ("kind", "actor", "component", "level_instance_chain",
                    "transform", "transform_extraction_error"):
            self.assertIn(key, record)

    def test_light_type_inference_covers_all_classes(self):
        # Vérifie l'inférence de type sans Unreal, en simulant class_name.
        import ue2godot.ue.components as c
        original = c.class_name
        try:
            for cls, expected in (("DirectionalLightComponent", "directional"),
                                  ("SpotLightComponent", "spot"),
                                  ("RectLightComponent", "rect"),
                                  ("SkyLightComponent", "sky"),
                                  ("PointLightComponent", "point")):
                c.class_name = lambda obj, _c=cls: _c
                rec = c.light_component_info(None, None, lambda comp, act: (None, None))
                self.assertEqual(rec["light_type"], expected, cls)
        finally:
            c.class_name = original


class TestMaterialResolution(unittest.TestCase):
    def test_parameter_value_serialisation_keeps_type(self):
        self.assertEqual(materials.parameter_value_to_dict(None)["type"], "none")
        self.assertEqual(materials.parameter_value_to_dict(True)["type"], "bool")
        self.assertEqual(materials.parameter_value_to_dict(3)["type"], "int")
        self.assertEqual(materials.parameter_value_to_dict(2.5)["type"], "float")
        self.assertEqual(materials.parameter_value_to_dict("x")["type"], "string")

    def test_resolve_parameter_prefers_exact_over_substring(self):
        # "Tint" en sous-chaîne ne doit pas gagner sur "Tint 02" exact :
        # l'ordre des passes est ce qui garantit qu'un profil de pack
        # déclarant ses préférences est réellement respecté.
        import ue2godot.ue.materials as m
        original = m._texture_parameters
        try:
            m._texture_parameters = lambda mat: {
                "Tint Mask": {"value": "/mask", "origin": "a"},
                "Tint 02": {"value": "/good", "origin": "b"},
            }
            value, name, _ = m.resolve_parameter(None, ["Tint 02", "Tint"], kind="texture")
            self.assertEqual(value, "/good")
            self.assertEqual(name, "Tint 02")
        finally:
            m._texture_parameters = original

    def test_resolve_parameter_returns_none_when_absent(self):
        import ue2godot.ue.materials as m
        original = m._texture_parameters
        try:
            m._texture_parameters = lambda mat: {}
            self.assertEqual(m.resolve_parameter(None, ["X"]), (None, None, None))
        finally:
            m._texture_parameters = original


class TestRegistryUsage(unittest.TestCase):
    def test_registry_counts_usages_without_duplicating_info(self):
        from ue2godot.core.registry import AssetRegistry
        reg = AssetRegistry()
        reg.register("/Game/M", {"name": "M"}, "ref1")
        reg.register("/Game/M", {}, "ref2")
        reg.register("/Game/M", {}, "ref1")
        entry = reg.get("/Game/M")
        self.assertEqual(entry["usage_count"], 3)
        self.assertEqual(sorted(entry["references"]), ["ref1", "ref2"])
        self.assertEqual(entry["name"], "M", "l'info initiale ne doit pas être écrasée")
        self.assertEqual(reg.count(), 1)


if __name__ == "__main__":
    unittest.main()
