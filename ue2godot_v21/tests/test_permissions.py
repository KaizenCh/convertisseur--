import json
import os
import tempfile
import unittest

from ue2godot.orchestrator.remote_execution import (
    _encode_message, _decode_message, PROTOCOL_MAGIC, PROTOCOL_VERSION,
    TYPE_PING, TYPE_PONG, CommandResult, RemoteNode,
)
from ue2godot.orchestrator.permissions import (
    UnrealAuthorizationManager, find_uproject, REMOTE_EXEC_SECTION,
    BACKUP_SUFFIX,
)


class TestProtocolEncoding(unittest.TestCase):
    def test_roundtrip_preserves_fields(self):
        raw = _encode_message("src-id", TYPE_PING, dest_id="dst-id", data={"k": "v"})
        msg = _decode_message(raw)
        self.assertIsNotNone(msg)
        self.assertEqual(msg["magic"], PROTOCOL_MAGIC)
        self.assertEqual(msg["version"], PROTOCOL_VERSION)
        self.assertEqual(msg["source"], "src-id")
        self.assertEqual(msg["dest"], "dst-id")
        self.assertEqual(msg["type"], TYPE_PING)
        self.assertEqual(msg["data"], {"k": "v"})

    def test_optional_fields_omitted_when_absent(self):
        msg = json.loads(_encode_message("src", TYPE_PING).decode("utf-8"))
        self.assertNotIn("dest", msg)
        self.assertNotIn("data", msg)

    def test_rejects_foreign_traffic_without_raising(self):
        # Le groupe multicast peut transporter n'importe quoi — ce n'est pas
        # une erreur, ça doit juste être ignoré.
        for bad in (b"not json at all", b"{}", b'{"magic":"other","version":1,"type":"ping"}',
                    b'\xff\xfe\x00binary', b'[1,2,3]'):
            self.assertIsNone(_decode_message(bad), bad)

    def test_rejects_wrong_protocol_version(self):
        raw = json.dumps({"version": 99, "magic": PROTOCOL_MAGIC,
                          "source": "s", "type": TYPE_PONG}).encode("utf-8")
        self.assertIsNone(_decode_message(raw))

    def test_rejects_message_without_type(self):
        raw = json.dumps({"version": PROTOCOL_VERSION, "magic": PROTOCOL_MAGIC,
                          "source": "s"}).encode("utf-8")
        self.assertIsNone(_decode_message(raw))

    def test_partial_json_decodes_as_none_so_accumulation_continues(self):
        # Un message TCP peut arriver en plusieurs segments : un JSON tronqué
        # doit renvoyer None (on continue d'accumuler), pas lever.
        full = _encode_message("src", TYPE_PONG, data={"project_name": "Necropolis"})
        self.assertIsNone(_decode_message(full[: len(full) // 2]))
        self.assertIsNotNone(_decode_message(full))


class TestCommandResult(unittest.TestCase):
    def test_output_text_joins_and_strips_blank_entries(self):
        res = CommandResult(success=True, output=[
            {"type": "Info", "output": "ligne 1\n"},
            {"type": "Info", "output": ""},
            {"type": "Warning", "output": "ligne 2"},
        ])
        self.assertEqual(res.output_text(), "ligne 1\nligne 2")

    def test_node_label_handles_missing_fields(self):
        self.assertIn("projet inconnu", RemoteNode(node_id="x").label())
        self.assertIn("Necropolis", RemoteNode(node_id="x", project_name="Necropolis").label())


class TestFindUproject(unittest.TestCase):
    def test_finds_uproject_walking_up_from_map(self):
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "MyGame")
            maps = os.path.join(proj, "Content", "Maps", "Necropolis")
            os.makedirs(maps)
            uproject = os.path.join(proj, "MyGame.uproject")
            with open(uproject, "w") as f:
                f.write("{}")
            umap = os.path.join(maps, "Necropolis.umap")
            with open(umap, "w") as f:
                f.write("")
            self.assertEqual(find_uproject(umap), uproject)

    def test_returns_none_when_no_uproject_anywhere(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(find_uproject(d))

    def test_handles_empty_input(self):
        self.assertIsNone(find_uproject(""))


class TestAuthorizationChecks(unittest.TestCase):
    def _make_project(self, tmp, plugins=None, ini_body=None):
        proj = os.path.join(tmp, "MyGame")
        os.makedirs(os.path.join(proj, "Config"), exist_ok=True)
        uproject = os.path.join(proj, "MyGame.uproject")
        with open(uproject, "w", encoding="utf-8") as f:
            json.dump({"FileVersion": 3, "Plugins": plugins or []}, f)
        if ini_body is not None:
            with open(os.path.join(proj, "Config", "DefaultEngine.ini"), "w", encoding="utf-8") as f:
                f.write(ini_body)
        return uproject

    def test_detects_enabled_python_plugin(self):
        with tempfile.TemporaryDirectory() as d:
            up = self._make_project(d, plugins=[{"Name": "PythonScriptPlugin", "Enabled": True}])
            mgr = UnrealAuthorizationManager(uproject_path=up)
            auth = mgr._check_python_plugin()
            self.assertTrue(auth.granted)

    def test_detects_disabled_python_plugin(self):
        with tempfile.TemporaryDirectory() as d:
            up = self._make_project(d, plugins=[{"Name": "PythonScriptPlugin", "Enabled": False}])
            mgr = UnrealAuthorizationManager(uproject_path=up)
            auth = mgr._check_python_plugin()
            self.assertFalse(auth.granted)
            self.assertTrue(auth.auto_grantable)

    def test_missing_plugin_entry_is_non_blocking(self):
        # Le plugin peut être activé au niveau du moteur — on ne peut pas le
        # savoir depuis le .uproject, donc ça ne doit pas bloquer à tort.
        with tempfile.TemporaryDirectory() as d:
            up = self._make_project(d, plugins=[])
            mgr = UnrealAuthorizationManager(uproject_path=up)
            auth = mgr._check_python_plugin()
            self.assertFalse(auth.granted)
            self.assertFalse(auth.blocking)

    def test_detects_remote_execution_enabled(self):
        ini = f"{REMOTE_EXEC_SECTION}\nbRemoteExecution=True\n"
        with tempfile.TemporaryDirectory() as d:
            up = self._make_project(d, ini_body=ini)
            mgr = UnrealAuthorizationManager(uproject_path=up)
            self.assertTrue(mgr._check_remote_execution().granted)

    def test_detects_remote_execution_disabled(self):
        ini = f"{REMOTE_EXEC_SECTION}\nbRemoteExecution=False\n"
        with tempfile.TemporaryDirectory() as d:
            up = self._make_project(d, ini_body=ini)
            mgr = UnrealAuthorizationManager(uproject_path=up)
            self.assertFalse(mgr._check_remote_execution().granted)

    def test_missing_ini_means_not_granted(self):
        with tempfile.TemporaryDirectory() as d:
            up = self._make_project(d)
            mgr = UnrealAuthorizationManager(uproject_path=up)
            self.assertFalse(mgr._check_remote_execution().granted)


class TestAuthorizationGrants(unittest.TestCase):
    def _make_project(self, tmp, plugins=None, ini_body=None):
        proj = os.path.join(tmp, "MyGame")
        os.makedirs(os.path.join(proj, "Config"), exist_ok=True)
        uproject = os.path.join(proj, "MyGame.uproject")
        with open(uproject, "w", encoding="utf-8") as f:
            json.dump({"FileVersion": 3, "Plugins": plugins or []}, f)
        if ini_body is not None:
            with open(os.path.join(proj, "Config", "DefaultEngine.ini"), "w", encoding="utf-8") as f:
                f.write(ini_body)
        return uproject

    def test_grant_refuses_without_explicit_consent(self):
        with tempfile.TemporaryDirectory() as d:
            up = self._make_project(d)
            mgr = UnrealAuthorizationManager(uproject_path=up)
            ok, msg = mgr.grant("remote_execution")  # consent omis
            self.assertFalse(ok)
            self.assertIn("onsentement", msg)

    def test_grant_python_plugin_adds_entry_and_backup(self):
        with tempfile.TemporaryDirectory() as d:
            up = self._make_project(d, plugins=[{"Name": "Other", "Enabled": True}])
            mgr = UnrealAuthorizationManager(uproject_path=up)
            ok, _ = mgr._grant_python_plugin()
            self.assertTrue(ok)
            with open(up, encoding="utf-8") as f:
                data = json.load(f)
            names = {p["Name"]: p["Enabled"] for p in data["Plugins"]}
            self.assertTrue(names["PythonScriptPlugin"])
            self.assertTrue(names["Other"], "le plugin existant ne doit pas être perdu")
            self.assertTrue(os.path.isfile(up + BACKUP_SUFFIX))

    def test_grant_remote_execution_creates_section_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            up = self._make_project(d, ini_body="[/Script/Engine.RendererSettings]\nr.Foo=1\n")
            mgr = UnrealAuthorizationManager(uproject_path=up)
            ok, _ = mgr._grant_ini_setting("bRemoteExecution", "True")
            self.assertTrue(ok)
            self.assertTrue(mgr._check_remote_execution().granted)
            with open(mgr.default_engine_ini, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("r.Foo=1", content, "les réglages existants doivent survivre")

    def test_grant_remote_execution_flips_existing_false(self):
        ini = f"{REMOTE_EXEC_SECTION}\nbRemoteExecution=False\nOtherKey=42\n"
        with tempfile.TemporaryDirectory() as d:
            up = self._make_project(d, ini_body=ini)
            mgr = UnrealAuthorizationManager(uproject_path=up)
            mgr._grant_ini_setting("bRemoteExecution", "True")
            self.assertTrue(mgr._check_remote_execution().granted)
            with open(mgr.default_engine_ini, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("OtherKey=42", content)
            self.assertEqual(content.count("bRemoteExecution"), 1,
                             "ne doit pas dupliquer la clé")

    def test_ini_patch_preserves_duplicate_and_prefixed_keys(self):
        # Les .ini d'Unreal acceptent des clés dupliquées et des préfixes
        # +/-/. que configparser normaliserait ou perdrait. Le patch est
        # ligne à ligne précisément pour ça.
        ini = (
            "[/Script/Engine.RendererSettings]\n"
            "+Array=(Path=\"A\")\n"
            "+Array=(Path=\"B\")\n"
            "-Removed=X\n"
            f"{REMOTE_EXEC_SECTION}\n"
            "bRemoteExecution=False\n"
        )
        with tempfile.TemporaryDirectory() as d:
            up = self._make_project(d, ini_body=ini)
            mgr = UnrealAuthorizationManager(uproject_path=up)
            mgr._grant_ini_setting("bRemoteExecution", "True")
            with open(mgr.default_engine_ini, encoding="utf-8") as f:
                content = f.read()
            self.assertIn('+Array=(Path="A")', content)
            self.assertIn('+Array=(Path="B")', content)
            self.assertIn("-Removed=X", content)

    def test_module_path_grant_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            up = self._make_project(d, ini_body="")
            module_root = os.path.join(d, "tool")
            mgr = UnrealAuthorizationManager(uproject_path=up, module_root=module_root)
            ok1, _ = mgr._grant_module_path()
            self.assertTrue(ok1)
            self.assertTrue(mgr._check_module_path().granted)
            ok2, msg2 = mgr._grant_module_path()
            self.assertTrue(ok2)
            with open(mgr.default_engine_ini, encoding="utf-8") as f:
                content = f.read()
            self.assertEqual(content.count("AdditionalPaths"), 1,
                             "un second appel ne doit pas ré-ajouter le chemin")


if __name__ == "__main__":
    unittest.main()
