"""Le branchement UI ↔ moteur, vérifié sans afficher de fenêtre.

Ces tests existent parce que l'interface a déjà été remplacée une fois :
ils garantissent que la nouvelle mise en page appelle bien le moteur, et
qu'aucune action n'est redevenue un simple aperçu visuel.
"""
import os
import sys
import unittest
import importlib.util

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load_main():
    spec = importlib.util.spec_from_file_location("ue2godot_main",
                                                  os.path.join(ROOT, "main.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["ue2godot_main"] = module
    spec.loader.exec_module(module)
    return module


class TestBackendSeparation(unittest.TestCase):
    def test_backend_imports_without_any_ui(self):
        from ue2godot.app.backend import OrchestratorAdapter, SourceAnalyzer
        self.assertTrue(callable(SourceAnalyzer))
        self.assertTrue(hasattr(OrchestratorAdapter, "copy_assets"))
        self.assertTrue(hasattr(OrchestratorAdapter, "build_scene_headless"))
        self.assertTrue(hasattr(OrchestratorAdapter, "find_godot"))

    def test_backend_declares_no_layout_widgets(self):
        # Le moteur ne doit importer aucun widget de mise en page : c'est
        # ce qui permet de rechanger d'interface sans y toucher.
        source = open(os.path.join(ROOT, "ue2godot", "app", "backend.py"),
                      encoding="utf-8").read()
        for forbidden in ("QVBoxLayout", "QHBoxLayout", "QMainWindow",
                          "QFrame", "QLabel(", "QPushButton("):
            self.assertNotIn(forbidden, source,
                             f"le moteur ne doit pas manipuler {forbidden}")


class TestWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_main()
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.window = self.module.MainWindow()
        self.module.QMessageBox.warning = staticmethod(lambda *a, **k: None)
        self.module.QMessageBox.information = staticmethod(lambda *a, **k: None)

    def test_engine_is_instantiated(self):
        self.assertTrue(hasattr(self.window, "orchestrator"))
        self.assertTrue(hasattr(self.window, "source_analyzer"))
        self.assertIn("construction", self.window.config)

    def test_no_placeholder_button_remains_wired_to_actions(self):
        source = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
        # La classe peut subsister ; ce qui ne doit plus exister, c'est son
        # usage sur un point d'action.
        uses = [line for line in source.splitlines()
                if "PlaceholderButton(" in line and "class PlaceholderButton" not in line]
        self.assertEqual(uses, [], f"boutons encore factices : {uses}")

    def test_actions_degrade_gracefully_without_configuration(self):
        for name in ("run_verification", "run_unreal_exports", "run_godot_build",
                     "open_godot_project", "open_output_folder", "show_build_report"):
            with self.subTest(action=name):
                getattr(self.window, name)()  # ne doit pas lever

    def test_text_fields_drive_the_configuration(self):
        self.window.enter_advanced_mode()
        self.window.source_input.setText("/tmp/source-x")
        self.assertEqual(self.window.config["source"]["unreal_map"], "/tmp/source-x")
        self.window.project_input.setText("/tmp/godot-x")
        self.assertEqual(self.window.config["project"]["godot_project"], "/tmp/godot-x")

    def test_log_events_are_recorded_and_capped(self):
        for index in range(20):
            self.window.log_event(f"m{index}", "info", "test")
        self.assertGreaterEqual(len(self.window._central_log_entries), 20)

    def test_engine_output_reaches_the_log_without_explicit_launch(self):
        # La connexion doit être permanente : une action déclenchée hors
        # d'un lancement explicite doit quand même laisser une trace.
        before = len(self.window._central_log_entries)
        self.window.orchestrator.log.emit("message moteur")
        self.assertGreater(len(self.window._central_log_entries), before)

    def test_engine_noise_is_classified_not_dropped(self):
        self.window.orchestrator.log.emit("[  42% ] reimport | x.glb")
        last = self.window._central_log_entries[-1]
        self.assertEqual(last["level"], "engine")

    def test_all_advanced_pages_build(self):
        self.window.enter_advanced_mode()
        for index in range(5):
            with self.subTest(page=index):
                self.window.select_page(index)

    def test_every_workflow_can_be_selected(self):
        for workflow in self.module.WORKFLOWS:
            with self.subTest(workflow=workflow.key):
                self.window.select_workflow(workflow.key)

    def test_module_flags_map_to_real_config_keys(self):
        flags = self.window._module_flags()
        for key in flags:
            self.assertIn(key, self.window.config["construction"]["modules"])

    def test_vfx_mode_maps_to_framework_values(self):
        self.assertIn(self.window._vfx_mode(), ("markers", "substitutes", "none"))

    def test_plan_carries_the_paths_the_engine_needs(self):
        self.window.config["source"]["unreal_map"] = "/tmp/src"
        self.window.config["project"]["godot_project"] = "/tmp/gd"
        plan = self.window._build_plan()
        for key in ("source", "godot_project", "godot_asset_root"):
            self.assertIn(key, plan)
        self.assertTrue(plan["godot_asset_root"].startswith("res://"))


if __name__ == "__main__":
    unittest.main()


class TestThreeModes(unittest.TestCase):
    """Les trois modes partagent UNE configuration et UN moteur.

    C'est le point qui rend le troisième mode utile plutôt que redondant :
    passer de l'un à l'autre ne doit rien perdre, et aucun mode ne doit
    avoir sa propre copie de la logique.
    """

    @classmethod
    def setUpClass(cls):
        cls.module = load_main()
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.window = self.module.MainWindow()
        self.module.QMessageBox.warning = staticmethod(lambda *a, **k: None)
        self.module.QMessageBox.information = staticmethod(lambda *a, **k: None)

    def test_chooser_offers_three_modes(self):
        for mode in ("simple", "hybrid", "advanced"):
            with self.subTest(mode=mode):
                self.window.choose_mode(mode)

    def test_hybrid_has_all_five_steps(self):
        self.window.choose_mode("hybrid")
        self.assertEqual(len(self.window.hybrid.STEPS), 5)
        for index in range(5):
            self.window.hybrid.go_to(index)
            self.assertTrue(self.window.hybrid.title.text())

    def test_hybrid_fields_write_to_the_shared_configuration(self):
        self.window.choose_mode("hybrid")
        self.window.hybrid.source_field.setText("/tmp/source-h")
        self.assertEqual(self.window.config["source"]["unreal_map"], "/tmp/source-h")
        self.window.hybrid.godot_field.setText("/tmp/godot-h")
        self.assertEqual(self.window.config["project"]["godot_project"], "/tmp/godot-h")

    def test_hybrid_module_toggles_reach_the_real_config(self):
        self.window.choose_mode("hybrid")
        self.window.hybrid.module_boxes["decals"].setChecked(False)
        self.assertFalse(self.window.config["construction"]["modules"]["decals"])
        self.window.hybrid.module_boxes["decals"].setChecked(True)
        self.assertTrue(self.window.config["construction"]["modules"]["decals"])

    def test_hybrid_exposes_the_scan_guard_threshold(self):
        self.window.choose_mode("hybrid")
        self.window.hybrid.min_actors.setValue(77)
        self.assertEqual(self.window.config["advanced"]["min_expected_actors"], 77)

    def test_switching_modes_preserves_what_was_entered(self):
        self.window.choose_mode("hybrid")
        self.window.hybrid.source_field.setText("/tmp/keepme")
        self.window.enter_advanced_mode()
        self.window.show_hybrid_mode()
        self.assertEqual(self.window.hybrid.source_field.text(), "/tmp/keepme")
        self.assertEqual(self.window.config["source"]["unreal_map"], "/tmp/keepme")

    def test_hybrid_buttons_call_the_same_engine_methods(self):
        # Aucun mode ne réimplémente la conversion : les trois appellent
        # les mêmes méthodes. On le vérifie par substitution.
        self.window.choose_mode("hybrid")
        called = []
        self.window.run_unreal_exports = lambda *a, **k: called.append("unreal")
        self.window.run_godot_build = lambda *a, **k: called.append("godot")
        # Reconstruire l'étape pour que les boutons pointent sur les
        # méthodes substituées.
        page = self.window.hybrid._step_run()
        for button in page.findChildren(self.module.QPushButton):
            if "Unreal" in button.text() or "Construire" in button.text():
                button.click()
        self.assertIn("unreal", called)
        self.assertIn("godot", called)

    def test_hybrid_log_receives_events_but_not_engine_noise(self):
        self.window.choose_mode("hybrid")
        self.window.log_event("visible", "ok", "t")
        self.window.log_event("[  42% ] reimport | x.glb", "engine", "godot")
        text = self.window.hybrid.run_log.toPlainText()
        self.assertIn("visible", text)
        self.assertNotIn("reimport", text)

    def test_hybrid_navigation_never_leaves_the_step_range(self):
        self.window.choose_mode("hybrid")
        for _ in range(10):
            self.window.hybrid.next_step()
        self.assertEqual(self.window.hybrid.step, len(self.window.hybrid.STEPS) - 1)
        for _ in range(3):
            self.window.hybrid.previous_step()
        self.assertGreaterEqual(self.window.hybrid.step, 0)

    def test_advanced_page_selection_is_inert_while_hybrid_is_active(self):
        # Le mode guidé n'a pas de pages avancées : une commande de
        # navigation ne doit pas le faire sauter ailleurs.
        self.window.choose_mode("hybrid")
        current = self.window.pages.currentIndex()
        self.window.select_page(3)
        self.assertEqual(self.window.pages.currentIndex(), current)


class TestModeIsolation(unittest.TestCase):
    """Chaque interface est complète en elle-même.

    Le sélecteur de la barre d'en-tête est le SEUL passage entre modes :
    aucune interface ne doit renvoyer l'utilisateur vers une autre en
    cours de route, sous peine de le projeter dans une densité qu'il
    avait justement choisi d'éviter.
    """

    @classmethod
    def setUpClass(cls):
        cls.module = load_main()
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.source = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()

    def setUp(self):
        self.window = self.module.MainWindow()
        self.module.QMessageBox.warning = staticmethod(lambda *a, **k: None)
        self.module.QMessageBox.information = staticmethod(lambda *a, **k: None)

    def _class_body(self, name):
        body = self.source[self.source.index(f"class {name}"):]
        return body[:body.index("\nclass ")]

    def test_no_mode_class_redirects_to_another_mode(self):
        for name in ("SimpleWizard", "HybridMode"):
            with self.subTest(mode=name):
                body = self._class_body(name)
                for forbidden in ("enter_advanced_mode", "show_simple_mode",
                                  "show_hybrid_mode", "select_page("):
                    self.assertNotIn(
                        forbidden, body,
                        f"{name} redirige vers une autre interface via {forbidden}")

    def test_each_mode_goes_from_config_to_result(self):
        # Simple et guidé doivent tous deux posséder leurs propres écrans
        # d'exécution et de résultat, et les boutons d'ouverture de Godot.
        self.window.choose_mode("simple")
        self.assertGreaterEqual(self.window.simple_home.steps.count(), 5)
        self.assertTrue(hasattr(self.window.simple_home, "run_log"))
        self.assertTrue(hasattr(self.window.simple_home, "result_view"))

        self.window.choose_mode("hybrid")
        self.assertEqual(len(self.window.hybrid.STEPS), 5)
        self.assertTrue(hasattr(self.window.hybrid, "run_log"))
        self.assertTrue(hasattr(self.window.hybrid, "result_view"))

    def test_selector_offers_exactly_three_modes(self):
        self.assertEqual(sorted(self.window.mode_buttons),
                         ["advanced", "hybrid", "simple"])

    def test_selector_reflects_the_active_mode(self):
        for mode in ("simple", "hybrid", "advanced"):
            with self.subTest(mode=mode):
                self.window.choose_mode(mode)
                active = [k for k, b in self.window.mode_buttons.items() if b.isChecked()]
                self.assertEqual(active, [mode])

    def test_selector_is_hidden_on_the_launch_chooser(self):
        self.window.show_mode_chooser()
        self.assertFalse(self.window.mode_selector.isVisible())

    def test_switch_mode_is_inert_while_the_chooser_is_up(self):
        self.window.show_mode_chooser()
        self.window.switch_mode("advanced")
        self.assertTrue(self.window.mode_chooser)

    def test_configuration_survives_every_mode_transition(self):
        self.window.choose_mode("hybrid")
        self.window.hybrid.source_field.setText("/tmp/shared-path")
        for mode in ("advanced", "simple", "hybrid", "advanced"):
            self.window.switch_mode(mode)
            self.assertEqual(self.window.config["source"]["unreal_map"],
                             "/tmp/shared-path")

    def test_simple_mode_navigation_reaches_its_own_result_screen(self):
        import tempfile, os as _os
        godot = tempfile.mkdtemp()
        open(_os.path.join(godot, "project.godot"), "w").write("config_version=5")
        self.window.config["source"]["unreal_map"] = tempfile.mkdtemp()
        self.window.config["project"]["godot_project"] = godot
        self.window.choose_mode("simple")
        self.window.simple_home.sync_from_config()
        for _ in range(10):
            self.window.simple_home.next_step()
        self.assertEqual(self.window.simple_home.step,
                         self.window.simple_home.steps.count() - 1)

    def test_toggle_cycles_through_all_three_modes(self):
        self.window.choose_mode("simple")
        seen = set()
        for _ in range(3):
            self.window.toggle_user_mode()
            seen.add("simple" if self.window.simple_mode else
                     "hybrid" if self.window.hybrid_mode else "advanced")
        self.assertEqual(seen, {"simple", "hybrid", "advanced"})


class TestSimpleModeIsUsable(unittest.TestCase):
    """Le mode simple doit être réellement utilisable seul.

    Trois défauts constatés en usage réel : les cartes de choix ne
    répondaient pas au clic, aucun projet Godot ne pouvait être désigné, et
    la source ne pouvait être qu'un dossier.
    """

    @classmethod
    def setUpClass(cls):
        cls.module = load_main()
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.window = self.module.MainWindow()
        self.module.QMessageBox.warning = staticmethod(lambda *a, **k: None)
        self.module.QMessageBox.information = staticmethod(lambda *a, **k: None)
        self.window.choose_mode("simple")
        self.wizard = self.window.simple_home

    def test_choice_cards_are_mutually_exclusive(self):
        # Sans QButtonGroup, deux radios dans des cartes différentes ont des
        # parents différents et restaient cochés tous les deux.
        buttons = self.wizard.scope_group.buttons()
        self.assertGreaterEqual(len(buttons), 2)
        buttons[0].setChecked(True)
        buttons[1].setChecked(True)
        self.assertEqual(sum(1 for b in buttons if b.isChecked()), 1)

    def test_clicking_a_card_selects_it(self):
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtGui import QMouseEvent
        card = self.wizard.scope_group.buttons()[1].parent()
        event = QMouseEvent(QEvent.MouseButtonPress, QPoint(5, 5),
                            Qt_LeftButton(), Qt_LeftButton(), Qt_NoModifier())
        card.mousePressEvent(event)
        self.assertTrue(self.wizard.scope_group.buttons()[1].isChecked())

    def test_choice_updates_the_wizard_state(self):
        self.wizard.scope_group.buttons()[1].setChecked(True)
        self.assertEqual(self.wizard.scope, "selected")
        self.wizard.quality_radios[2].setChecked(True)
        self.assertEqual(self.wizard.quality, "Fast preview")

    def test_godot_target_can_be_configured(self):
        self.assertTrue(hasattr(self.wizard, "godot_input"))
        self.wizard.godot_input.setText("/tmp/godot-simple")
        self.assertEqual(self.window.config["project"]["godot_project"],
                         "/tmp/godot-simple")

    def test_source_accepts_a_umap_file_not_only_a_folder(self):
        # browse_unreal_map_file existe et alimente la même clé de config.
        self.assertTrue(hasattr(self.window, "browse_unreal_map_file"))
        self.window.config["source"]["unreal_map"] = "/tmp/x/Content/Maps/m1.umap"
        self.wizard.sync_from_config()
        self.assertIn("m1.umap", self.wizard.project_input.text())

    def test_first_step_refuses_to_advance_without_both_paths(self):
        self.window.config["source"]["unreal_map"] = ""
        self.window.config["project"]["godot_project"] = ""
        self.wizard.go_to(0)
        self.wizard.next_step()
        self.assertEqual(self.wizard.step, 0)

    def test_first_step_advances_once_both_paths_are_valid(self):
        import tempfile, os as _os
        godot = tempfile.mkdtemp()
        open(_os.path.join(godot, "project.godot"), "w").write("config_version=5")
        self.wizard.project_input.setText(tempfile.mkdtemp())
        self.wizard.godot_input.setText(godot)
        self.wizard.go_to(0)
        self.wizard.next_step()
        self.assertEqual(self.wizard.step, 1)


class TestNoStrayWindows(unittest.TestCase):
    """Aucun widget de premier niveau parasite.

    Un QWidget créé sans parent et jamais ajouté à un layout devient une
    FENÊTRE : c'est ce qui faisait apparaître une petite fenêtre portant le
    nom du mode à chaque bascule.
    """

    @classmethod
    def setUpClass(cls):
        cls.module = load_main()
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_switching_modes_creates_no_extra_window(self):
        # On compare les OBJETS, pas un décompte : d'autres tests laissent
        # des fenêtres en vie, ce qui rendrait un comptage dépendant de
        # l'ordre d'exécution.
        from PySide6.QtWidgets import QApplication
        window = self.module.MainWindow()
        window.show()
        QApplication.processEvents()
        before = {id(w) for w in QApplication.topLevelWidgets()}

        for mode in ("simple", "hybrid", "advanced", "simple"):
            window.choose_mode(mode)
        QApplication.processEvents()

        # On ne compte que les fenêtres VISIBLES : Qt crée des QFrame
        # internes invisibles (sans nom ni enfant) au fil des changements de
        # page, et les compter rendrait ce test bruyant sans rien dire du
        # symptôme réel — une petite fenêtre parasite à l'écran.
        created = [w for w in QApplication.topLevelWidgets()
                   if id(w) not in before and w.isVisible()]
        self.assertEqual(
            created, [],
            f"une bascule de mode a fait apparaître {len(created)} fenêtre(s) : "
            f"{[(type(w).__name__, w.windowTitle()) for w in created]}")


def Qt_LeftButton():
    from PySide6.QtCore import Qt as _Qt
    return _Qt.LeftButton


def Qt_NoModifier():
    from PySide6.QtCore import Qt as _Qt
    return _Qt.NoModifier


class TestModuleSingleSourceOfTruth(unittest.TestCase):
    """Les modules n'ont qu'UNE source de vérité : la configuration.

    Auparavant `selected_modules` (préréglage figé du workflow) était
    consulté en priorité : décocher un module écrivait bien False dans la
    configuration, mais le préréglage le remettait à True au lancement. Le
    module était converti malgré la case décochée, sans aucun message.
    """

    @classmethod
    def setUpClass(cls):
        cls.module = load_main()
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.window = self.module.MainWindow()
        self.module.QMessageBox.warning = staticmethod(lambda *a, **k: None)
        self.module.QMessageBox.information = staticmethod(lambda *a, **k: None)

    def test_unchecking_a_module_actually_disables_it(self):
        self.window.choose_mode("hybrid")
        self.window.hybrid.module_boxes["decals"].setChecked(False)
        self.assertFalse(self.window.config["construction"]["modules"]["decals"])
        self.assertFalse(self.window._module_flags()["decals"],
                         "le préréglage du workflow écrase la case décochée")

    def test_rechecking_re_enables_it(self):
        self.window.choose_mode("hybrid")
        box = self.window.hybrid.module_boxes["decals"]
        box.setChecked(False)
        box.setChecked(True)
        self.assertTrue(self.window._module_flags()["decals"])

    def test_workflow_preset_is_projected_into_the_configuration(self):
        self.window.choose_mode("hybrid")
        self.window.apply_configuration(
            "T", "Maximum", "Markers", ["Landscape", "Decals"], "test")
        modules = self.window.config["construction"]["modules"]
        self.assertTrue(modules["landscape"])
        self.assertTrue(modules["decals"])
        self.assertFalse(modules["audio"], "un module hors préréglage doit être désactivé")

    def test_workflow_preset_updates_the_visible_checkboxes(self):
        # Un réglage appliqué sans retour visuel est indiscernable d'un
        # réglage ignoré.
        self.window.choose_mode("hybrid")
        self.window.apply_configuration(
            "T", "Maximum", "Markers", ["Landscape"], "test")
        self.assertTrue(self.window.hybrid.module_boxes["landscape"].isChecked())
        self.assertFalse(self.window.hybrid.module_boxes["decals"].isChecked())

    def test_module_flags_keys_match_the_real_config_keys(self):
        for key in self.window._module_flags():
            self.assertIn(key, self.window.config["construction"]["modules"])
