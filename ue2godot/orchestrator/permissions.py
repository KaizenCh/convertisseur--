# -*- coding: utf-8 -*-
"""
Gestion explicite des autorisations nécessaires pour piloter Unreal depuis
l'orchestrateur externe.

PRINCIPE
--------
Piloter Unreal de l'extérieur n'est pas une seule permission, c'en est
quatre, indépendantes, qui échouent chacune avec un symptôme différent :

  1. Le plugin Python doit être activé dans le projet (.uproject).
  2. L'exécution distante doit être activée (Config/DefaultEngine.ini).
  3. Le dossier contenant `ue2godot` doit être visible depuis le sys.path de
     l'interpréteur embarqué d'Unreal (sinon ModuleNotFoundError — déjà
     rencontré en test réel, cf. §16.2 de la doc fusionnée).
  4. Un éditeur doit être lancé, avec le bon projet ouvert, et joignable sur
     le canal multicast (pare-feu).

Ce module les vérifie SÉPARÉMENT et sait en accorder trois automatiquement —
mais jamais sans consentement explicite de l'utilisateur, parce que les
accorder modifie des fichiers qui appartiennent à son projet (.uproject et
DefaultEngine.ini sont versionnés dans la plupart des équipes).

RÈGLE DE SÉCURITÉ NON NÉGOCIABLE
--------------------------------
`grant()` refuse d'écrire si l'éditeur est en cours d'exécution. Unreal
réécrit ses fichiers de config à la fermeture : patcher DefaultEngine.ini
pendant que l'éditeur tourne verrait la modification écrasée en silence à la
fermeture — l'utilisateur croirait l'autorisation accordée alors qu'elle
aurait disparu. Une sauvegarde `.ue2godot.bak` est écrite avant toute
modification.
"""

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple

from ue2godot.orchestrator.remote_execution import (
    RemoteExecutionClient, RemoteExecutionError, RemoteNode,
    MODE_EXEC_STATEMENT, probe_editor, diagnose_network,
)

PYTHON_PLUGIN_NAME = "PythonScriptPlugin"
REMOTE_EXEC_SECTION = "[/Script/PythonScriptPlugin.PythonScriptPluginSettings]"
REMOTE_EXEC_KEY = "bRemoteExecution"
ADDITIONAL_PATHS_KEY = "+AdditionalPaths"
BACKUP_SUFFIX = ".ue2godot.bak"


@dataclass
class Authorization:
    """Une autorisation, son état, et comment la régler."""
    key: str
    label: str
    granted: bool
    detail: str = ""
    remediation: str = ""
    auto_grantable: bool = False
    blocking: bool = True
    requires_editor_closed: bool = False
    requires_editor_restart: bool = False

    def marker(self) -> str:
        if self.granted:
            return "✓"
        return "✕" if self.blocking else "⚠"


@dataclass
class AuthorizationReport:
    authorizations: List[Authorization] = field(default_factory=list)
    nodes: List[RemoteNode] = field(default_factory=list)
    uproject_path: str = ""

    @property
    def all_granted(self) -> bool:
        return all(a.granted for a in self.authorizations)

    @property
    def can_execute_remotely(self) -> bool:
        """Toutes les autorisations BLOQUANTES sont accordées — les
        non-bloquantes (ex. AdditionalPaths, qui a un contournement par
        sys.path.insert dans chaque commande) n'empêchent pas l'exécution."""
        return all(a.granted for a in self.authorizations if a.blocking)

    def blocking_failures(self) -> List[Authorization]:
        return [a for a in self.authorizations if a.blocking and not a.granted]

    def get(self, key: str) -> Optional[Authorization]:
        for a in self.authorizations:
            if a.key == key:
                return a
        return None

    def as_lines(self) -> List[str]:
        lines = []
        for a in self.authorizations:
            lines.append(f"{a.marker()} {a.label}")
            if a.detail:
                lines.append(f"    {a.detail}")
            if not a.granted and a.remediation:
                lines.append(f"    → {a.remediation}")
        return lines


def find_uproject(start_path: str) -> Optional[str]:
    """Remonte l'arborescence depuis un chemin (une .umap, ou un dossier)
    jusqu'à trouver un .uproject. C'est la même convention
    <Projet>/Content/... déjà utilisée par
    landscape_material_config._umap_filesystem_path_to_game_folder()."""
    if not start_path:
        return None
    current = os.path.abspath(start_path)
    if os.path.isfile(current):
        current = os.path.dirname(current)

    for _ in range(12):  # borne : jamais remonter jusqu'à la racine du disque
        if not current or not os.path.isdir(current):
            break
        try:
            for name in os.listdir(current):
                if name.lower().endswith(".uproject"):
                    return os.path.join(current, name)
        except OSError:
            pass
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


class UnrealAuthorizationManager:
    """Vérifie et accorde les autorisations nécessaires à l'exécution distante."""

    def __init__(self, uproject_path: str = "", module_root: str = "",
                 editor_executable: str = ""):
        self.uproject_path = uproject_path
        self.module_root = module_root
        self.editor_executable = editor_executable

    # ------------------------------------------------------------------
    # Chemins dérivés
    # ------------------------------------------------------------------

    @property
    def project_dir(self) -> str:
        return os.path.dirname(self.uproject_path) if self.uproject_path else ""

    @property
    def default_engine_ini(self) -> str:
        return os.path.join(self.project_dir, "Config", "DefaultEngine.ini") if self.project_dir else ""

    # ------------------------------------------------------------------
    # Vérifications individuelles
    # ------------------------------------------------------------------

    def _check_uproject(self) -> Authorization:
        if not self.uproject_path:
            return Authorization(
                key="uproject", label="Projet Unreal identifié", granted=False,
                detail="Aucun .uproject trouvé à partir du chemin source sélectionné.",
                remediation="Sélectionnez une .umap située dans l'arborescence d'un projet Unreal "
                            "(<Projet>/Content/.../map.umap), ou désignez le .uproject manuellement.",
            )
        if not os.path.isfile(self.uproject_path):
            return Authorization(
                key="uproject", label="Projet Unreal identifié", granted=False,
                detail=f"Chemin renseigné introuvable : {self.uproject_path}",
                remediation="Vérifiez le chemin du fichier .uproject.",
            )
        return Authorization(
            key="uproject", label="Projet Unreal identifié", granted=True,
            detail=self.uproject_path,
        )

    def _check_python_plugin(self) -> Authorization:
        auth = Authorization(
            key="python_plugin", label="Plugin Python activé dans le projet", granted=False,
            auto_grantable=True, requires_editor_closed=True, requires_editor_restart=True,
            remediation="Peut être accordé automatiquement (écrit dans le .uproject), "
                        "ou manuellement via Edit > Plugins > Scripting > Python Editor Script Plugin.",
        )
        if not self.uproject_path or not os.path.isfile(self.uproject_path):
            auth.detail = "Projet non identifié — vérification impossible."
            return auth

        try:
            with open(self.uproject_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            auth.detail = f"Lecture du .uproject impossible : {exc}"
            auth.auto_grantable = False
            return auth

        for plugin in data.get("Plugins", []) or []:
            if not isinstance(plugin, dict):
                continue
            if plugin.get("Name") == PYTHON_PLUGIN_NAME:
                if plugin.get("Enabled", False):
                    auth.granted = True
                    auth.detail = "PythonScriptPlugin: Enabled"
                else:
                    auth.detail = "PythonScriptPlugin présent mais Enabled=false."
                return auth

        # Absent du .uproject n'est pas forcément un échec : le plugin peut
        # être activé globalement au niveau du moteur. On ne peut pas le
        # savoir d'ici avec certitude — on le signale sans le déclarer
        # bloquant à tort, et la découverte multicast tranchera.
        auth.detail = ("PythonScriptPlugin absent du .uproject. Il peut malgré tout être "
                       "activé au niveau du moteur — le test de connexion le confirmera.")
        auth.blocking = False
        return auth

    def _parse_remote_execution_ini(self) -> Tuple[Optional[bool], List[str]]:
        """(valeur de bRemoteExecution ou None, AdditionalPaths trouvés)."""
        path = self.default_engine_ini
        if not path or not os.path.isfile(path):
            return None, []

        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            return None, []

        in_section = False
        value: Optional[bool] = None
        paths: List[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("["):
                in_section = (stripped == REMOTE_EXEC_SECTION)
                continue
            if not in_section or not stripped or stripped.startswith(";"):
                continue
            if "=" not in stripped:
                continue
            key, _, raw = stripped.partition("=")
            key = key.strip()
            raw = raw.strip()
            if key == REMOTE_EXEC_KEY:
                value = raw.lower() in ("true", "1")
            elif key in (ADDITIONAL_PATHS_KEY, "AdditionalPaths", "-AdditionalPaths"):
                match = re.search(r'Path\s*=\s*"([^"]*)"', raw)
                paths.append(match.group(1) if match else raw.strip('"'))
        return value, paths

    def _check_remote_execution(self) -> Authorization:
        auth = Authorization(
            key="remote_execution", label="Exécution distante Python autorisée", granted=False,
            auto_grantable=True, requires_editor_closed=True, requires_editor_restart=True,
            remediation="Peut être accordé automatiquement (écrit bRemoteExecution=True dans "
                        "Config/DefaultEngine.ini), ou manuellement via Project Settings > "
                        "Plugins > Python > Enable Remote Execution.",
        )
        if not self.project_dir:
            auth.detail = "Projet non identifié — vérification impossible."
            auth.auto_grantable = False
            return auth

        value, _ = self._parse_remote_execution_ini()
        if value is True:
            auth.granted = True
            auth.detail = f"{REMOTE_EXEC_KEY}=True dans DefaultEngine.ini"
        elif value is False:
            auth.detail = f"{REMOTE_EXEC_KEY}=False dans DefaultEngine.ini"
        else:
            auth.detail = (f"{REMOTE_EXEC_KEY} absent de DefaultEngine.ini "
                           "(l'exécution distante est désactivée par défaut).")
        return auth

    def _check_module_path(self) -> Authorization:
        """Non bloquant : chaque commande générée préfixe déjà un
        sys.path.insert (§16.2). L'enregistrer dans AdditionalPaths est un
        confort, pas une nécessité."""
        auth = Authorization(
            key="module_path", label="Dossier de l'outil visible par Unreal (confort)",
            granted=False, auto_grantable=True, blocking=False,
            requires_editor_closed=True, requires_editor_restart=True,
            remediation="Peut être ajouté automatiquement aux AdditionalPaths du plugin Python. "
                        "Sans cela, chaque commande envoyée insère le chemin elle-même — "
                        "ça fonctionne, c'est juste plus verbeux.",
        )
        if not self.module_root:
            auth.detail = "Racine du module non fournie."
            auth.auto_grantable = False
            return auth
        if not self.project_dir:
            auth.detail = "Projet non identifié."
            auth.auto_grantable = False
            return auth

        _, paths = self._parse_remote_execution_ini()
        normalized = os.path.normcase(os.path.normpath(self.module_root))
        for p in paths:
            if os.path.normcase(os.path.normpath(p)) == normalized:
                auth.granted = True
                auth.detail = f"Déjà présent dans AdditionalPaths : {p}"
                return auth
        auth.detail = f"{self.module_root} absent des AdditionalPaths."
        return auth

    def _check_editor_reachable(self, timeout: float = 2.0) -> Tuple[Authorization, List[RemoteNode]]:
        auth = Authorization(
            key="editor_reachable", label="Éditeur Unreal joignable", granted=False,
            remediation="Lancez l'éditeur avec ce projet ouvert, puis relancez la vérification. "
                        "Si l'éditeur est déjà ouvert, vérifiez que le pare-feu Windows "
                        "autorise Python sur le réseau privé, et que les deux autorisations "
                        "ci-dessus sont accordées (un redémarrage de l'éditeur est "
                        "nécessaire après les avoir accordées).",
        )
        try:
            nodes = probe_editor(timeout=timeout, default_engine_ini=self.default_engine_ini)
        except Exception as exc:  # défensif : ne jamais faire tomber la vérif entière
            auth.detail = f"Sonde multicast en échec : {exc}"
            return auth, []

        if not nodes:
            auth.detail = "Aucun éditeur n'a répondu sur le canal multicast."
            return auth, []

        auth.granted = True
        auth.detail = " ; ".join(n.label() for n in nodes)

        # Un éditeur répond, mais est-ce le BON projet ? Un utilisateur avec
        # deux éditeurs ouverts enverrait sinon ses commandes au mauvais.
        expected = os.path.splitext(os.path.basename(self.uproject_path))[0] if self.uproject_path else ""
        if expected:
            matching = [n for n in nodes if n.project_name and n.project_name.lower() == expected.lower()]
            if not matching and any(n.project_name for n in nodes):
                auth.granted = False
                auth.detail = (f"Un éditeur répond, mais sur un autre projet que '{expected}' "
                               f"({auth.detail}).")
                auth.remediation = (f"Ouvrez le projet '{expected}' dans Unreal, ou sélectionnez "
                                    "une map appartenant au projet actuellement ouvert.")
        return auth, nodes

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def check_all(self, probe_timeout: float = 2.0) -> AuthorizationReport:
        """Vérifie toutes les autorisations. Ne modifie rien."""
        report = AuthorizationReport(uproject_path=self.uproject_path)
        report.authorizations.append(self._check_uproject())
        report.authorizations.append(self._check_python_plugin())
        report.authorizations.append(self._check_remote_execution())
        report.authorizations.append(self._check_module_path())
        editor_auth, nodes = self._check_editor_reachable(timeout=probe_timeout)
        report.authorizations.append(editor_auth)
        report.nodes = nodes
        return report

    def is_editor_running(self, timeout: float = 1.0) -> bool:
        return bool(probe_editor(timeout=timeout, default_engine_ini=self.default_engine_ini))

    def grant(self, key: str, consent: bool = False) -> Tuple[bool, str]:
        """Accorde une autorisation auto-accordable.

        `consent` doit être explicitement True : ces opérations écrivent dans
        des fichiers du projet de l'utilisateur (souvent versionnés). Un
        défaut à False garantit qu'aucun appelant ne peut modifier son projet
        par accident.
        """
        if not consent:
            return False, ("Consentement explicite requis : cette opération modifie un fichier "
                           "de votre projet Unreal.")

        if key not in ("python_plugin", "remote_execution", "module_path"):
            return False, f"L'autorisation '{key}' ne peut pas être accordée automatiquement."

        if self.is_editor_running():
            return False, ("L'éditeur Unreal est en cours d'exécution. Unreal réécrit ses "
                           "fichiers de configuration à la fermeture : la modification serait "
                           "écrasée sans avertissement. Fermez l'éditeur, accordez "
                           "l'autorisation, puis rouvrez-le.")

        if key == "python_plugin":
            return self._grant_python_plugin()
        if key == "remote_execution":
            return self._grant_ini_setting(REMOTE_EXEC_KEY, "True")
        return self._grant_module_path()

    def _backup(self, path: str) -> None:
        if os.path.isfile(path) and not os.path.isfile(path + BACKUP_SUFFIX):
            try:
                shutil.copy2(path, path + BACKUP_SUFFIX)
            except OSError:
                pass

    def _grant_python_plugin(self) -> Tuple[bool, str]:
        if not self.uproject_path or not os.path.isfile(self.uproject_path):
            return False, "Fichier .uproject introuvable."
        try:
            with open(self.uproject_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"Lecture du .uproject impossible : {exc}"

        plugins = data.setdefault("Plugins", [])
        for plugin in plugins:
            if isinstance(plugin, dict) and plugin.get("Name") == PYTHON_PLUGIN_NAME:
                plugin["Enabled"] = True
                break
        else:
            plugins.append({"Name": PYTHON_PLUGIN_NAME, "Enabled": True})

        self._backup(self.uproject_path)
        try:
            with open(self.uproject_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except OSError as exc:
            return False, f"Écriture du .uproject impossible : {exc}"
        return True, (f"PythonScriptPlugin activé dans {os.path.basename(self.uproject_path)} "
                      f"(sauvegarde : {os.path.basename(self.uproject_path)}{BACKUP_SUFFIX}). "
                      "Redémarrez l'éditeur pour que ce soit pris en compte.")

    def _read_ini_lines(self) -> List[str]:
        path = self.default_engine_ini
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                    return f.read().splitlines()
            except OSError:
                return []
        return []

    def _write_ini_lines(self, lines: List[str]) -> Tuple[bool, str]:
        path = self.default_engine_ini
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._backup(path)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError as exc:
            return False, f"Écriture de DefaultEngine.ini impossible : {exc}"
        return True, ""

    def _grant_ini_setting(self, key: str, value: str) -> Tuple[bool, str]:
        """Patch ligne à ligne, jamais via configparser : les .ini d'Unreal
        acceptent des clés dupliquées et des préfixes +/-/. que configparser
        normaliserait ou perdrait, corrompant silencieusement des réglages
        sans rapport avec le nôtre."""
        lines = self._read_ini_lines()
        section_start = None
        section_end = len(lines)

        for i, line in enumerate(lines):
            if line.strip() == REMOTE_EXEC_SECTION:
                section_start = i
                for j in range(i + 1, len(lines)):
                    if lines[j].strip().startswith("["):
                        section_end = j
                        break
                else:
                    section_end = len(lines)
                break

        if section_start is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(REMOTE_EXEC_SECTION)
            lines.append(f"{key}={value}")
        else:
            for i in range(section_start + 1, section_end):
                stripped = lines[i].strip()
                if stripped.startswith(";") or "=" not in stripped:
                    continue
                if stripped.partition("=")[0].strip() == key:
                    lines[i] = f"{key}={value}"
                    break
            else:
                lines.insert(section_end, f"{key}={value}")

        ok, err = self._write_ini_lines(lines)
        if not ok:
            return False, err
        return True, (f"{key}={value} écrit dans Config/DefaultEngine.ini "
                      f"(sauvegarde : DefaultEngine.ini{BACKUP_SUFFIX}). "
                      "Redémarrez l'éditeur pour que ce soit pris en compte.")

    def _grant_module_path(self) -> Tuple[bool, str]:
        if not self.module_root:
            return False, "Racine du module non fournie."
        lines = self._read_ini_lines()
        entry = f'{ADDITIONAL_PATHS_KEY}=(Path="{self.module_root}")'

        normalized = os.path.normcase(os.path.normpath(self.module_root))
        for line in lines:
            match = re.search(r'AdditionalPaths\s*=\s*\(?\s*Path\s*=\s*"([^"]*)"', line)
            if match and os.path.normcase(os.path.normpath(match.group(1))) == normalized:
                return True, "Ce chemin est déjà enregistré."

        section_start = None
        section_end = len(lines)
        for i, line in enumerate(lines):
            if line.strip() == REMOTE_EXEC_SECTION:
                section_start = i
                for j in range(i + 1, len(lines)):
                    if lines[j].strip().startswith("["):
                        section_end = j
                        break
                else:
                    section_end = len(lines)
                break

        if section_start is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(REMOTE_EXEC_SECTION)
            lines.append(entry)
        else:
            lines.insert(section_end, entry)

        ok, err = self._write_ini_lines(lines)
        if not ok:
            return False, err
        return True, (f"{self.module_root} ajouté aux AdditionalPaths du plugin Python. "
                      "Redémarrez l'éditeur pour que ce soit pris en compte.")

    # ------------------------------------------------------------------
    # Lancement de l'éditeur
    # ------------------------------------------------------------------

    def launch_editor(self) -> Tuple[bool, str]:
        """Lance l'éditeur avec le projet. Ne fait rien s'il tourne déjà."""
        if self.is_editor_running():
            return True, "Un éditeur Unreal répond déjà — aucun lancement nécessaire."
        if not self.uproject_path or not os.path.isfile(self.uproject_path):
            return False, "Fichier .uproject introuvable."

        exe = self.editor_executable or self._guess_editor_executable()
        if not exe:
            return False, ("Exécutable de l'éditeur Unreal introuvable. Renseignez-le dans les "
                           "options avancées, ou lancez l'éditeur manuellement.")
        if not os.path.isfile(exe):
            return False, f"Exécutable introuvable : {exe}"

        try:
            subprocess.Popen([exe, self.uproject_path])
        except OSError as exc:
            return False, f"Lancement impossible : {exc}"
        return True, ("Éditeur lancé. Le chargement d'un gros projet prend souvent plusieurs "
                      "minutes ; relancez la vérification une fois la map ouverte.")

    def _guess_editor_executable(self) -> str:
        """Cherche UnrealEditor.exe aux emplacements d'installation usuels.

        Volontairement limité à quelques chemins connus : deviner trop
        largement risquerait de lancer une version de moteur différente de
        celle du projet, ce qui est pire qu'un échec explicite.
        """
        candidates: List[str] = []
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramW6432", r"C:\Program Files")):
            if not base:
                continue
            epic_root = os.path.join(base, "Epic Games")
            if not os.path.isdir(epic_root):
                continue
            try:
                versions = sorted(os.listdir(epic_root), reverse=True)
            except OSError:
                continue
            for version in versions:
                for exe_name in ("UnrealEditor.exe", "UE4Editor.exe"):
                    candidate = os.path.join(epic_root, version, "Engine", "Binaries", "Win64", exe_name)
                    if os.path.isfile(candidate):
                        candidates.append(candidate)
        return candidates[0] if candidates else ""

    # ------------------------------------------------------------------
    # Test de bout en bout
    # ------------------------------------------------------------------

    def test_connection(self, timeout: float = 5.0) -> Tuple[bool, str]:
        """Ouvre réellement un canal et exécute une instruction triviale.

        C'est le seul contrôle qui prouve que la chaîne complète fonctionne :
        les vérifications de fichiers ci-dessus lisent des intentions
        déclarées, pas un comportement. Une config qui a l'air correcte mais
        qu'Unreal n'a pas rechargée (pas redémarré depuis la modification)
        passerait les premières et échouerait ici — c'est voulu.
        """
        try:
            with RemoteExecutionClient.from_project(self.default_engine_ini) as client:
                nodes = client.discover_nodes(timeout=timeout)
                if not nodes:
                    return False, ("Aucun éditeur n'a répondu. Vérifiez que l'éditeur est lancé, "
                                   "que l'exécution distante est activée, et que l'éditeur a bien "
                                   "été redémarré depuis l'activation.")
                node = self._select_node(nodes)
                client.open_command_channel(node, timeout=timeout)
                result = client.run_command(
                    "import unreal; print('ue2godot: canal OK, ' + unreal.SystemLibrary.get_engine_version())",
                    exec_mode=MODE_EXEC_STATEMENT, timeout=timeout,
                )
                if not result.success:
                    return False, f"Canal ouvert mais la commande a échoué : {result.result}"
                return True, f"Connexion vérifiée avec {node.label()}.\n{result.output_text()}"
        except RemoteExecutionError as exc:
            return False, str(exc)

    def diagnose(self, timeout: float = 3.0) -> List[str]:
        """Rapport réseau détaillé — voir remote_execution.diagnose_network."""
        return diagnose_network(timeout=timeout, default_engine_ini=self.default_engine_ini)

    def _select_node(self, nodes: List[RemoteNode]) -> RemoteNode:
        """Choisit le nœud correspondant au projet attendu si possible —
        jamais « le premier qui répond » quand plusieurs éditeurs tournent."""
        expected = os.path.splitext(os.path.basename(self.uproject_path))[0] if self.uproject_path else ""
        if expected:
            for node in nodes:
                if node.project_name and node.project_name.lower() == expected.lower():
                    return node
        return nodes[0]
